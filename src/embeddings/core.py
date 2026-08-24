"""
Hybrid course search: exact-code resolution + FAISS vector search + IDF-weighted
lexical search, fused into a single ranked list.

Import is cheap and side-effect free — the OpenAI client, course data, FAISS
index, and lexical index are only created when `initialize()` is called. Pure
helpers (query normalization, `build_lexical_index`, `lexical_search`,
`fuse_candidates`, `all_sections_for`) work without initialization; the API
server (via its FastAPI lifespan) and the CLIs call `initialize()` at startup.
"""
import json
import faiss
import os
import logging
import math
import re
from typing import Any

import numpy as np
from openai import OpenAI

from src.config import Config
from src.utils.course_formatting import informalName, meetingDays
from src.utils.campus import get_campus
from src.utils.subject_mapping import SUBJECT_MAPPING
from src.utils.course_loader import load_all_semesters
from src.utils.paths import COURSES_INDEX, ID_TO_COURSE_JSON, ensure_dirs
from src.utils.sanitize import unescape_html
from src.utils.embedding_helpers import (
    get_embeddings_batch as get_embeddings_batch_helper,
    get_embedding as get_embedding_helper,
    course_to_text as course_to_text_helper,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

dimension = Config.EMBEDDING_DIMENSION
index_file = str(COURSES_INDEX)
metadata_file = str(ID_TO_COURSE_JSON)

# State populated by initialize(); left unset so `import` is cheap. reload_index()
# may reassign these later. VALID_SUBJECTS starts empty so pure functions like
# resolve_course_code (which falls back to SUBJECT_MAPPING) still work uninitialized.
#
# Typed Any because Pylance can't narrow module globals across a
# `_require_initialized()` guard call, so Optional here produces a false-positive
# warning on every read site. The runtime contract is enforced by
# _require_initialized().
client: Any = None
courses: Any = None
index: Any = None
id_to_course_list: Any = None
VALID_SUBJECTS: set = set()
LEXICAL_INDEX: Any = None


def initialize() -> None:
    """
    One-time heavy setup: build the OpenAI client, load course data, load or
    build the FAISS index, then build the lexical index over it.

    Idempotent — calling twice is a no-op. Callers that need to hit the vector
    store or the OpenAI API (the API server, `src.embeddings.incremental`,
    `src.embeddings.fast`) must call this at startup.
    """
    global client, courses, index, id_to_course_list, VALID_SUBJECTS, LEXICAL_INDEX
    if client is not None:
        return

    if not Config.OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY environment variable is required")
    client = OpenAI(api_key=Config.OPENAI_API_KEY)

    ensure_dirs()
    courses = load_all_semesters()

    if os.path.exists(index_file) and os.path.exists(metadata_file):
        logger.info("Loading existing FAISS index and metadata...")
        index = faiss.read_index(index_file)
        with open(metadata_file, "r", encoding="utf-8") as f:
            # Decode HTML entities ("&amp;" -> "&") so /search and /ask serve
            # clean text without needing the data re-scraped.
            id_to_course_list = unescape_html(json.load(f))
    else:
        logger.info("No saved index found. Creating embeddings...")
        index = faiss.IndexFlatL2(dimension)
        id_to_course_list = []

        logger.info(f"Processing {len(courses)} courses...")
        all_texts = []
        all_entries = []
        for crn, course in courses.items():
            try:
                all_texts.append(course_to_text(course))
                all_entries.append({"crn": crn, "course": course})
            except Exception as e:
                logger.error(f"Failed to process course {crn}: {e}")
                continue

        logger.info("Generating embeddings in batches...")
        embeddings = get_embeddings_batch(all_texts)

        logger.info("Adding embeddings to FAISS index...")
        for embedding, entry in zip(embeddings, all_entries):
            vector = np.array([embedding], dtype="float32")
            index.add(vector)
            id_to_course_list.append(entry)

        logger.info(f"Saving index with {len(id_to_course_list)} courses...")
        faiss.write_index(index, index_file)
        with open(metadata_file, "w", encoding="utf-8") as f:
            json.dump(id_to_course_list, f, indent=2)

    VALID_SUBJECTS = _extract_valid_subjects(id_to_course_list)
    logger.info(f"Loaded {len(VALID_SUBJECTS)} valid subject codes")
    LEXICAL_INDEX = build_lexical_index(id_to_course_list)
    logger.info(f"Built lexical index over {len(LEXICAL_INDEX['docs'])} courses")


def _require_initialized() -> None:
    if index is None:
        raise RuntimeError(
            "src.embeddings.core.initialize() must be called before search_courses(). "
            "The API server does this in its FastAPI lifespan; scripts should call it explicitly."
        )


# Wrapper function to convert course to text using utility
def course_to_text(course):
    """Convert course to text using shared utility function."""
    return course_to_text_helper(course, informalName, meetingDays, get_campus)


# Wrapper functions for embeddings to use the client
def get_embedding(text: str) -> list[float]:
    """Get embedding for a single text using shared utility."""
    if client is None:
        initialize()
    return get_embedding_helper(client, text)


def get_embeddings_batch(texts: list[str]) -> list[list[float]]:
    """Get embeddings in batches using shared utility."""
    if client is None:
        initialize()
    return get_embeddings_batch_helper(client, texts)


def _extract_valid_subjects(entries: list) -> set:
    """Set of all subject codes present in the loaded course data."""
    subjects: set = set()
    for entry in entries or []:
        subject = entry.get("course", {}).get("subject", "")
        if subject:
            subjects.add(subject)
    return subjects


def get_valid_subjects() -> set:
    """Backward-compat alias — reads the current VALID_SUBJECTS set."""
    return set(VALID_SUBJECTS)


def reload_index() -> None:
    """
    Re-read courses.index and id_to_course.json from disk and swap the
    module-level globals in place. Called by the in-process scheduler after a
    fresh scrape + embeddings rebuild so the API picks up new data without a
    restart.

    Reassignment of module globals is atomic in Python — concurrent searches
    will see either the old index or the new one, never a mix.
    """
    global index, id_to_course_list, VALID_SUBJECTS, LEXICAL_INDEX
    logger.info("Reloading FAISS index and metadata from disk...")
    new_index = faiss.read_index(index_file)
    with open(metadata_file, "r", encoding="utf-8") as f:
        new_metadata = unescape_html(json.load(f))
    index = new_index
    id_to_course_list = new_metadata
    VALID_SUBJECTS = _extract_valid_subjects(id_to_course_list)
    LEXICAL_INDEX = build_lexical_index(id_to_course_list)
    logger.info(f"Reload complete: {len(id_to_course_list)} courses, {len(VALID_SUBJECTS)} subjects")

def normalize_course_query(query: str) -> str:
    """
    Normalize course queries to improve search accuracy

    Handles patterns like:
    - "Chem1B" -> "CHEM 1B" or "Chemistry 1B"
    - "CS50" -> "CS 50" or "Computer Science 50"
    - "Math10" -> "MATH 10" or "Math 10"
    """
    # Pattern: Subject code (2-4 letters) followed immediately by course number
    # Examples: Chem1B, CS50, MATH10, BIO12A
    pattern = r'\b([A-Za-z]{2,4})(\d{1,4}[A-Z]?)\b'

    def add_space(match):
        subject = match.group(1).upper()
        number = match.group(2)
        return f"{subject} {number}"

    # Add spaces between subject and number
    normalized = re.sub(pattern, add_space, query)

    logger.info(f"Query normalized: '{query}' -> '{normalized}'")
    return normalized

def extract_course_code(query: str, valid_subjects: set | None = None) -> tuple[str, str]:
    """
    Extract subject and course number from query, validating against real subject codes.
    Returns (subject, course_number) or (None, None) if not found or invalid.

    Pass `valid_subjects` explicitly to avoid depending on module-level state
    (useful in tests). Defaults to the initialized VALID_SUBJECTS set.

    Examples:
    - "CHEM 1B" -> ("CHEM", "0001B")  # CHEM is valid
    - "MATH 31" -> ("MATH", "0031")   # MATH is valid
    - "CALC 2" -> (None, None)        # CALC is not a valid subject code
    - "math classes" -> (None, None)  # No pattern match
    """
    subjects_to_check = valid_subjects if valid_subjects is not None else VALID_SUBJECTS

    # Pattern: Subject (2-4 letters) followed by course number (1-4 digits + optional letter)
    pattern = r'\b([A-Z]{2,4})\s*(\d{1,4}[A-Z]?)\b'
    match = re.search(pattern, query.upper())

    if match:
        subject = match.group(1)
        number = match.group(2)

        # Validate subject code against actual course subjects
        if subject not in subjects_to_check:
            logger.info(f"Rejected course code '{subject} {number}' - '{subject}' is not a valid subject code")
            return (None, None)

        # Normalize course number to 4 digits + letter format (e.g., "1B" -> "0001B")
        if number[-1].isalpha():
            # Has letter suffix (like "1B")
            digit_part = number[:-1]
            letter_part = number[-1]
            normalized = digit_part.zfill(4) + letter_part
        else:
            # No letter suffix
            normalized = number.zfill(4)

        return (subject, normalized)

    return (None, None)

def detect_subject_preference(query: str) -> str | None:
    """
    Detect if the query mentions a specific academic subject/department.

    Returns the subject code if detected, None otherwise.

    Examples:
    - "math courses" -> "MATH"
    - "chemistry classes" -> "CHEM"
    - "computer science" -> "CSCI"
    - "biology" -> "BIOL"
    """
    query_lower = query.lower()

    # Subject keyword mappings (keyword -> subject code)
    subject_keywords = {
        "math": "MATH",
        "mathematics": "MATH",
        "chemistry": "CHEM",
        "chem": "CHEM",
        "computer science": "CSCI",
        "cs": "CSCI",
        "compsci": "CSCI",
        "biology": "BIOL",
        "bio": "BIOL",
        "physics": "PHYS",
        "english": "ENGL",
        "history": "HIST",
        "psychology": "PSYC",
        "psych": "PSYC",
        "sociology": "SOCI",
        "economics": "ECON",
        "econ": "ECON",
        "business": "BUS",
        "accounting": "ACCT",
        "art": "ART",
        "music": "MUSC",
        "nursing": "RN",
        "philosophy": "PHIL",
        "political science": "POLS",
        "anthropology": "ANTH",
    }

    # Check for subject keywords in query (longest match first)
    for keyword, subject_code in sorted(subject_keywords.items(), key=lambda x: len(x[0]), reverse=True):
        if keyword in query_lower:
            logger.info(f"Detected subject preference: {keyword} -> {subject_code}")
            return subject_code

    return None


# --- Exact course-code resolution -------------------------------------------

def _pad_course_number(raw: str) -> str:
    """Normalize a course number like "1b"/"205" to Banner's "0001B"/"0205" form."""
    raw = raw.upper()
    if raw and raw[-1].isalpha():
        return raw[:-1].zfill(4) + raw[-1]
    return raw.zfill(4)


def resolve_course_code(query: str, valid_subjects: set | None = None) -> tuple:
    """
    Resolve a query naming a specific course to (SUBJECT, padded_number), else
    (None, None).

    Two ways in:
      1. A real subject code + number ("MATH 31", "PHYS205") via extract_course_code.
      2. A natural subject word + an adjacent number ("physics 205") via
         SUBJECT_MAPPING — this is what lets "physics 205" land on PHYS0205, which
         the 2-4 letter code pattern alone can't see.
    """
    if valid_subjects is None:
        valid_subjects = VALID_SUBJECTS

    subject, number = extract_course_code(query, valid_subjects=valid_subjects)
    if subject and number:
        return subject, number

    lowered = query.lower()
    # Longest subject names first so "political science" beats "science", etc.
    for word, code in sorted(SUBJECT_MAPPING.items(), key=lambda kv: len(kv[0]), reverse=True):
        if valid_subjects and code not in valid_subjects:
            continue
        match = re.search(rf'\b{re.escape(word)}\s*(\d{{1,4}}[a-z]?)\b', lowered)
        if match:
            return code, _pad_course_number(match.group(1))
    return None, None


# --- Lexical (keyword) search channel ---------------------------------------

_TOKEN_RE = re.compile(r"[a-z0-9]+")
# Filler words that carry no course-identifying signal — dropped from both the
# index and the query so they don't create spurious matches.
_STOPWORDS = {
    "the", "a", "an", "of", "and", "or", "for", "to", "in", "on", "at", "is",
    "are", "be", "with", "about", "class", "classes", "course", "courses",
    "section", "sections", "what", "which", "when", "where", "who", "i", "want",
    "need", "looking", "take", "taking", "me", "my", "please", "show", "find",
    "any", "available", "all", "some", "best", "good", "easy",
}
_TITLE_WEIGHT = 3.0
_CODE_WEIGHT = 3.0
_DESC_WEIGHT = 1.0
# Prefix (abbreviation) matches count for less than exact matches, so a real word
# match ("computer" == "computer") outweighs a coincidental prefix ("mat" ~ "math").
_PREFIX_PENALTY = 0.6


def _tokenize(text: str) -> list[str]:
    """Lowercase word/number tokens, minus stopwords and 1-char noise."""
    return [t for t in _TOKEN_RE.findall((text or "").lower())
            if len(t) >= 2 and t not in _STOPWORDS]


def build_lexical_index(entries: list[dict]) -> dict:
    """
    Build the in-memory keyword index used by the lexical search channel.

    Per course we gather weighted tokens from the title (weighted highest), the
    code (subject + number, kept both zero-padded "0205" and bare "205"), and the
    subject description. We also compute an IDF per token across the corpus so rare,
    distinguishing words (e.g. "linear", in a couple of titles) count for far more
    than common ones (e.g. "algebra"). Positions line up with `entries`.

    Returns {"docs": [ {token: weight}, ... ], "idf": {token: idf}}.
    """
    docs: list[dict] = []
    df: dict[str, int] = {}

    for entry in entries:
        course = entry.get("course", {})
        weighted: dict[str, float] = {}

        def add(text, weight):
            for tok in _tokenize(text):
                weighted[tok] = max(weighted.get(tok, 0.0), weight)

        add(course.get("courseTitle", ""), _TITLE_WEIGHT)
        add(course.get("subjectDescription", ""), _DESC_WEIGHT)

        subject = (course.get("subject") or "").lower()
        number = (course.get("courseNumber") or "").lower()
        for code_tok in {subject, number, number.lstrip("0")}:
            if code_tok:
                weighted[code_tok] = max(weighted.get(code_tok, 0.0), _CODE_WEIGHT)

        docs.append(weighted)
        for tok in weighted:
            df[tok] = df.get(tok, 0) + 1

    n = max(len(entries), 1)
    idf = {tok: math.log(1 + n / freq) for tok, freq in df.items()}
    return {"docs": docs, "idf": idf}


def _best_token_score(query_tok: str, doc: dict, idf: dict) -> float:
    """
    Best idf-weighted field score for one query token against a course's tokens.

    A doc token matches the query token if equal, or (both >=3 chars) one is a
    prefix of the other — so "algebra" matches the abbreviated title token "alg".
    """
    # Numbers (course numbers, years) must match exactly — prefix matching them
    # turns "2026" into a match for course "202", which is never what's meant.
    query_is_numeric = query_tok.isdigit()

    best = 0.0
    for doc_tok, field_weight in doc.items():
        if doc_tok == query_tok:
            factor = 1.0
        elif (not query_is_numeric and not doc_tok.isdigit()
              and len(query_tok) >= 3 and len(doc_tok) >= 3
              and (doc_tok.startswith(query_tok) or query_tok.startswith(doc_tok))):
            factor = _PREFIX_PENALTY
        else:
            continue
        score = field_weight * idf.get(doc_tok, 0.0) * factor
        if score > best:
            best = score
    return best


def lexical_search(lexical_index: dict, query: str, m: int) -> list[tuple]:
    """Rank courses by keyword overlap; return top-m (position, score), best first."""
    query_tokens = _tokenize(query)
    if not query_tokens:
        return []

    docs = lexical_index["docs"]
    idf = lexical_index["idf"]

    scored = []
    for pos, doc in enumerate(docs):
        if not doc:
            continue
        total = sum(_best_token_score(qt, doc, idf) for qt in query_tokens)
        if total > 0:
            scored.append((pos, total))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:m]


# --- Hybrid fusion ----------------------------------------------------------

def _vector_search_positions(query: str, m: int) -> list[int]:
    """Top-m course positions from the FAISS (semantic) index."""
    query_emb = np.array([get_embedding(query)], dtype="float32")
    _, indices = index.search(query_emb, m)
    return [int(i) for i in indices[0] if i >= 0]


def fuse_candidates(vector_positions: list, lexical_scored: list, preferred_subject,
                    entries: list, k: int, seen_codes: set,
                    vec_weight: float = 0.5, subject_bonus: float = 0.15) -> list[dict]:
    """
    Blend the vector and lexical channels into the top-k *distinct* courses.

    Unlike plain Reciprocal Rank Fusion (which is rank-only), this keeps the
    lexical channel's *magnitude*: a query like "linear algebra" matches the rare
    title word "linear" with a far higher keyword score than the common "algebra",
    and that gap is what lets the right course beat one the embeddings prefer. So:

        score(pos) = normalized_lexical_score        # 0..1, the precise signal
                   + vec_weight / (1 + vector_rank)   # semantic, secondary
                   + subject_bonus (if preferred subject)

    Results are de-duplicated by (subject, courseNumber) — `seen_codes` carries the
    codes already pinned as exact matches — so a topic search returns distinct
    courses rather than many sections of the same one.
    """
    scores: dict[int, float] = {}

    # Lexical magnitude, normalized per query so the scale is comparable to vector.
    if lexical_scored:
        max_lex = max(score for _, score in lexical_scored) or 1.0
        for pos, score in lexical_scored:
            scores[pos] = scores.get(pos, 0.0) + score / max_lex

    # Vector rank as a secondary, fast-decaying signal.
    for rank, pos in enumerate(vector_positions):
        scores[pos] = scores.get(pos, 0.0) + vec_weight / (1 + rank)

    if preferred_subject:
        for pos in scores:
            if entries[pos]["course"].get("subject") == preferred_subject:
                scores[pos] += subject_bonus

    results: list[dict] = []
    for pos in sorted(scores, key=lambda p: scores[p], reverse=True):
        course = entries[pos]["course"]
        code = (course.get("subject"), course.get("courseNumber"))
        if code in seen_codes:
            continue
        seen_codes.add(code)
        results.append(course)
        if len(results) >= k:
            break
    return results


def all_sections_for(courses: list, entries: list | None = None,
                     others_limit: int | None = 1) -> list[dict]:
    """
    Expand a ranked list of courses into their sections, grouped by course.

    `courses` is what search_courses returns — distinct courses (or, for an exact
    match, a single representative). The **top** (best-matching) course is expanded
    to *every* section, so a search for a class shows all of its sessions. The
    remaining courses are capped at `others_limit` sections each (default 1) — they
    are weaker, secondary matches, and a popular one (e.g. College Algebra with 24
    sections) would otherwise flood the results. Pass `others_limit=None` to expand
    every course fully. De-dups the input codes, so it's idempotent.
    """
    if entries is None:
        entries = id_to_course_list

    codes, seen = [], set()
    for c in courses:
        code = (c.get("subject"), c.get("courseNumber"))
        if code not in seen:
            seen.add(code)
            codes.append(code)

    sections: list[dict] = []
    for rank, code in enumerate(codes):
        matches = [
            entry["course"] for entry in entries
            if (entry["course"].get("subject"), entry["course"].get("courseNumber")) == code
        ]
        if rank == 0 or others_limit is None:
            sections.extend(matches)
        else:
            sections.extend(matches[:others_limit])
    return sections


def search_courses(query: str, k: int = 3, subject_hint: str = None) -> list[dict]:
    """
    Hybrid course search returning up to `k` *distinct* courses.

    If the query names a specific course (e.g. "physics 205" -> PHYS0205), that one
    course is returned. Otherwise the top `k` distinct courses come from a fusion of
    semantic (vector) and lexical (keyword) retrieval — the lexical channel is what
    catches distinctive-but-rare or abbreviated title terms (e.g. "linear algebra"
    -> "Diff Equations/Linear Alg") that pure embeddings bury.

    Results are one representative per course; callers expand them to every section
    with all_sections_for(). `subject_hint` (e.g. "Music") overrides automatic
    subject detection for the preference boost.
    """
    _require_initialized()
    try:
        normalized_query = normalize_course_query(query)
        subject, course_number = resolve_course_code(normalized_query)

        # Subject preference for the fusion boost: explicit hint wins; otherwise a
        # resolved code's subject, else auto-detect from the words.
        if subject_hint:
            preferred_subject = SUBJECT_MAPPING.get(subject_hint.lower())
            if preferred_subject:
                logger.info(f"Using subject hint from intent: {subject_hint} -> {preferred_subject}")
            else:
                logger.warning(f"Subject hint '{subject_hint}' not found in mapping")
        elif subject:
            preferred_subject = subject
        else:
            preferred_subject = detect_subject_preference(normalized_query)

        # Exact course code -> return just that course (a representative section);
        # all_sections_for() expands it to every section.
        if subject and course_number:
            for entry in id_to_course_list:
                c = entry["course"]
                if c.get("subject") == subject and c.get("courseNumber") == course_number:
                    logger.info(f"Exact course code resolved: {subject} {course_number}")
                    return [c]
            logger.info(f"Resolved code {subject} {course_number} not offered; falling back to hybrid.")

        # Hybrid: top-k distinct courses from blended vector + lexical retrieval.
        m = max(k * 10, 50)
        vector_positions = _vector_search_positions(normalized_query, m)
        lexical_scored = lexical_search(LEXICAL_INDEX, normalized_query, m)

        return fuse_candidates(
            vector_positions, lexical_scored, preferred_subject,
            id_to_course_list, k, set(),
        )

    except Exception as e:
        logger.error(f"Failed to search courses: {e}")
        raise
