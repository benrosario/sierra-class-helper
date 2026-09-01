"""
Hybrid course search: exact-code resolution + FAISS vector search + IDF-weighted
lexical search, fused into a single ranked list.

Retrieval state (OpenAI client, FAISS index, entries, lexical index, valid
subjects) lives on a SearchIndex instance. `get_index()` returns the process-
wide singleton, built lazily on first call. `reload_index()` builds a fresh
SearchIndex and rebinds the singleton in one atomic assignment — any request
already holding a reference to the old instance completes safely on consistent
state instead of seeing a half-swapped mix of old vectors and new metadata.

Pure helpers (query normalization, `build_lexical_index`, `lexical_search`,
`fuse_candidates`, `all_sections_for`) stay module-level and stateless — they
take their inputs as parameters and can be tested without constructing a
SearchIndex or hitting any I/O.
"""
import json
import faiss
import os
import logging
import math
import re

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

_DIMENSION = Config.EMBEDDING_DIMENSION
_INDEX_FILE = str(COURSES_INDEX)
_METADATA_FILE = str(ID_TO_COURSE_JSON)


# --- Small helpers (pure) ---------------------------------------------------

def course_to_text(course: dict) -> str:
    """Convert a course dict to the formatted text used for embedding and LLM context."""
    return course_to_text_helper(course, informalName, meetingDays, get_campus)


def _extract_valid_subjects(entries: list) -> set:
    """Set of all subject codes present in the loaded course data."""
    subjects: set = set()
    for entry in entries or []:
        subject = entry.get("course", {}).get("subject", "")
        if subject:
            subjects.add(subject)
    return subjects


# --- Query normalization / code resolution (pure) ---------------------------

def normalize_course_query(query: str) -> str:
    """
    Normalize queries to improve search accuracy.

    Handles patterns like:
    - "Chem1B" -> "CHEM 1B"
    - "CS50" -> "CS 50"
    - "Math10" -> "MATH 10"
    """
    pattern = r'\b([A-Za-z]{2,4})(\d{1,4}[A-Z]?)\b'

    def add_space(match):
        return f"{match.group(1).upper()} {match.group(2)}"

    normalized = re.sub(pattern, add_space, query)
    logger.info(f"Query normalized: '{query}' -> '{normalized}'")
    return normalized


def extract_course_code(query: str, valid_subjects: set) -> tuple:
    """
    Extract (SUBJECT, padded_number) from a query, validating against
    valid_subjects. Returns (None, None) if no code pattern matched or the
    subject isn't in the whitelist.
    """
    pattern = r'\b([A-Z]{2,4})\s*(\d{1,4}[A-Z]?)\b'
    match = re.search(pattern, query.upper())
    if not match:
        return (None, None)

    subject = match.group(1)
    number = match.group(2)

    if subject not in valid_subjects:
        logger.info(f"Rejected course code '{subject} {number}' - '{subject}' is not a valid subject code")
        return (None, None)

    if number[-1].isalpha():
        normalized = number[:-1].zfill(4) + number[-1]
    else:
        normalized = number.zfill(4)
    return (subject, normalized)


def detect_subject_preference(query: str) -> str | None:
    """Return the subject code if the query mentions a common subject keyword, else None."""
    query_lower = query.lower()

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

    for keyword, subject_code in sorted(subject_keywords.items(), key=lambda x: len(x[0]), reverse=True):
        if keyword in query_lower:
            logger.info(f"Detected subject preference: {keyword} -> {subject_code}")
            return subject_code
    return None


def _pad_course_number(raw: str) -> str:
    """Normalize a course number like "1b"/"205" to Banner's "0001B"/"0205" form."""
    raw = raw.upper()
    if raw and raw[-1].isalpha():
        return raw[:-1].zfill(4) + raw[-1]
    return raw.zfill(4)


def resolve_course_code(query: str, valid_subjects: set) -> tuple:
    """
    Resolve a query naming a specific course to (SUBJECT, padded_number), else
    (None, None).

    Two ways in:
      1. A real subject code + number ("MATH 31", "PHYS205") via extract_course_code.
      2. A natural subject word + adjacent number ("physics 205") via SUBJECT_MAPPING
         — this is what lets "physics 205" land on PHYS0205, which the 2-4 letter
         code pattern alone can't see.
    """
    subject, number = extract_course_code(query, valid_subjects=valid_subjects)
    if subject and number:
        return subject, number

    lowered = query.lower()
    for word, code in sorted(SUBJECT_MAPPING.items(), key=lambda kv: len(kv[0]), reverse=True):
        if valid_subjects and code not in valid_subjects:
            continue
        match = re.search(rf'\b{re.escape(word)}\s*(\d{{1,4}}[a-z]?)\b', lowered)
        if match:
            return code, _pad_course_number(match.group(1))
    return None, None


# --- Lexical (keyword) search channel (pure) --------------------------------

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

    Per course we gather weighted tokens from the title (highest weight), the
    code (subject + number, kept both zero-padded "0205" and bare "205"), and the
    subject description. We also compute an IDF per token across the corpus so
    rare distinguishing words (e.g. "linear", in a couple of titles) count for
    far more than common ones (e.g. "algebra"). Positions line up with `entries`.

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


# --- Hybrid fusion (pure) ---------------------------------------------------

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

    Results are de-duplicated by (subject, courseNumber) — `seen_codes` carries
    codes already pinned as exact matches — so a topic search returns distinct
    courses rather than many sections of the same one.
    """
    scores: dict[int, float] = {}

    if lexical_scored:
        max_lex = max(score for _, score in lexical_scored) or 1.0
        for pos, score in lexical_scored:
            scores[pos] = scores.get(pos, 0.0) + score / max_lex

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


def all_sections_for(courses: list, entries: list,
                     others_limit: int | None = 1) -> list[dict]:
    """
    Expand a ranked list of courses into their sections, grouped by course.

    The **top** (best-matching) course is expanded to *every* section, so a
    search for a class shows all of its sessions. The remaining courses are
    capped at `others_limit` sections each (default 1) — they are weaker,
    secondary matches, and a popular one (e.g. College Algebra with 24 sections)
    would otherwise flood the results. Pass `others_limit=None` to expand every
    course fully. De-dups the input codes, so it's idempotent.
    """
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


# --- SearchIndex: retrieval state + methods ---------------------------------

class SearchIndex:
    """
    Owns the retrieval state built at construction: OpenAI client, FAISS index,
    the flat entries list (positionally aligned with the FAISS vectors), the
    lexical index built over those entries, and the set of valid subject codes.

    Constructed once at API startup (via `get_index()`) and replaced wholesale
    by `reload_index()` after a course refresh. Instances are effectively
    immutable — nothing mutates `self` after `__init__` returns — so an in-flight
    request holding a reference to the old instance always sees consistent state.
    """

    def __init__(self):
        if not Config.OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY environment variable is required")
        self.client: OpenAI = OpenAI(api_key=Config.OPENAI_API_KEY)

        ensure_dirs()

        if os.path.exists(_INDEX_FILE) and os.path.exists(_METADATA_FILE):
            logger.info("Loading existing FAISS index and metadata...")
            self.faiss_index = faiss.read_index(_INDEX_FILE)
            with open(_METADATA_FILE, "r", encoding="utf-8") as f:
                # Decode HTML entities ("&amp;" -> "&") so /search and /ask serve
                # clean text without needing the data re-scraped.
                self.entries: list = unescape_html(json.load(f))
        else:
            logger.info("No saved index found. Creating embeddings...")
            courses = load_all_semesters()
            self.faiss_index = faiss.IndexFlatL2(_DIMENSION)
            self.entries = []

            logger.info(f"Processing {len(courses)} courses...")
            all_texts, all_entries = [], []
            for crn, course in courses.items():
                try:
                    all_texts.append(course_to_text(course))
                    all_entries.append({"crn": crn, "course": course})
                except Exception as e:
                    logger.error(f"Failed to process course {crn}: {e}")

            logger.info("Generating embeddings in batches...")
            embeddings = get_embeddings_batch_helper(self.client, all_texts)

            logger.info("Adding embeddings to FAISS index...")
            for embedding, entry in zip(embeddings, all_entries):
                self.faiss_index.add(np.array([embedding], dtype="float32"))
                self.entries.append(entry)

            logger.info(f"Saving index with {len(self.entries)} courses...")
            faiss.write_index(self.faiss_index, _INDEX_FILE)
            with open(_METADATA_FILE, "w", encoding="utf-8") as f:
                json.dump(self.entries, f, indent=2)

        self.valid_subjects: set = _extract_valid_subjects(self.entries)
        logger.info(f"Loaded {len(self.valid_subjects)} valid subject codes")
        self.lexical_index: dict = build_lexical_index(self.entries)
        logger.info(f"Built lexical index over {len(self.lexical_index['docs'])} courses")

    def search(self, query: str, k: int = 3, subject_hint: str | None = None) -> list[dict]:
        """
        Return up to `k` distinct courses matching the query.

        If the query names a specific course (e.g. "physics 205" -> PHYS0205),
        that one course is returned. Otherwise the top `k` distinct courses come
        from a fusion of semantic (vector) and lexical (keyword) retrieval — the
        lexical channel is what catches distinctive-but-rare or abbreviated
        title terms (e.g. "linear algebra" -> "Diff Equations/Linear Alg") that
        pure embeddings bury.

        Results are one representative per course; callers expand them to every
        section with `.all_sections_for()`. `subject_hint` (e.g. "Music")
        overrides automatic subject detection for the preference boost.
        """
        try:
            normalized_query = normalize_course_query(query)
            subject, course_number = resolve_course_code(normalized_query, self.valid_subjects)

            if subject_hint:
                preferred_subject = SUBJECT_MAPPING.get(subject_hint.lower())
                if preferred_subject:
                    logger.info(f"Using subject hint from intent: {subject_hint} -> {preferred_subject}")
                else:
                    logger.warning(f"Subject hint '{subject_hint}' not found in mapping")
                    preferred_subject = None
            elif subject:
                preferred_subject = subject
            else:
                preferred_subject = detect_subject_preference(normalized_query)

            # Exact course code -> return just that course (a representative section);
            # .all_sections_for() expands it to every section.
            if subject and course_number:
                for entry in self.entries:
                    c = entry["course"]
                    if c.get("subject") == subject and c.get("courseNumber") == course_number:
                        logger.info(f"Exact course code resolved: {subject} {course_number}")
                        return [c]
                logger.info(f"Resolved code {subject} {course_number} not offered; falling back to hybrid.")

            m = max(k * 10, 50)
            vector_positions = self._vector_positions(normalized_query, m)
            lexical_scored = lexical_search(self.lexical_index, normalized_query, m)
            return fuse_candidates(
                vector_positions, lexical_scored, preferred_subject,
                self.entries, k, set(),
            )
        except Exception as e:
            logger.error(f"Failed to search courses: {e}")
            raise

    def all_sections_for(self, courses: list, others_limit: int | None = 1) -> list[dict]:
        """Expand `courses` to every section using this instance's entries."""
        return all_sections_for(courses, self.entries, others_limit)

    def _vector_positions(self, query: str, m: int) -> list[int]:
        """Top-m course positions from the FAISS (semantic) index for the query."""
        query_emb = np.array([get_embedding_helper(self.client, query)], dtype="float32")
        _, indices = self.faiss_index.search(query_emb, m)
        return [int(i) for i in indices[0] if i >= 0]


# --- Process-wide singleton -------------------------------------------------

_INSTANCE: SearchIndex | None = None


def get_index() -> SearchIndex:
    """
    Return the process-wide SearchIndex, building it on first call.

    Callers that make multiple related calls (e.g. `.search()` then
    `.all_sections_for()`) should snapshot the return value once and reuse it,
    so a concurrent `reload_index()` can't swap the singleton between calls
    and leave positions misaligned across old and new state.
    """
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = SearchIndex()
    return _INSTANCE


def reload_index() -> None:
    """
    Rebuild the SearchIndex from disk and atomically replace the singleton.

    Called by the in-process scheduler after a fresh scrape + embeddings rebuild
    so the API picks up new data without a restart. Any request already holding
    the old instance completes on it; the next `get_index()` call returns the
    fresh one. This is the reason state lives on an instance instead of on the
    module — a single reference swap replaces N racy global reassignments.
    """
    global _INSTANCE
    logger.info("Reloading SearchIndex from disk...")
    _INSTANCE = SearchIndex()
    logger.info(f"Reload complete: {len(_INSTANCE.entries)} courses, {len(_INSTANCE.valid_subjects)} subjects")
