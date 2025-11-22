import json
import faiss
import os
import logging
import numpy as np
import re
from pathlib import Path
from openai import OpenAI

# Import shared utilities
from src.utils.course_formatting import informalName, meetingDays
from src.utils.campus import get_campus
from src.utils.embedding_helpers import (
    estimate_tokens,
    get_embeddings_batch as get_embeddings_batch_helper,
    get_embedding as get_embedding_helper,
    course_to_text as course_to_text_helper
)

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Grab API key with validation
api_key = os.environ.get("OPENAI_API_KEY")
if not api_key:
    raise ValueError("OPENAI_API_KEY environment variable is required")
client = OpenAI(api_key=api_key)

dimension = 1536
index_file = "courses.index"
metadata_file = "id_to_course.json"

# Load course data for each semester
def load_all_semesters(directory="course_data"):
    all_courses = {}
    
    for json_file in Path(directory).glob("*.json"):
        with open (json_file, 'r') as f:
            data = json.load(f)
            
            term = json_file.stem
            
            for crn, course_data in data.items():
                if 'course' in course_data:
                    course_data['course']['source_term'] = term
                all_courses[crn] = course_data
                
    return all_courses

courses = load_all_semesters()

# Wrapper function to convert course to text using utility
def course_to_text(course):
    """Convert course to text using shared utility function."""
    return course_to_text_helper(course, informalName, meetingDays, get_campus)

# Wrapper functions for embeddings to use the client
def get_embedding(text: str) -> list[float]:
    """Get embedding for a single text using shared utility."""
    return get_embedding_helper(client, text)

def get_embeddings_batch(texts: list[str]) -> list[list[float]]:
    """Get embeddings in batches using shared utility."""
    return get_embeddings_batch_helper(client, texts)

# Format course start/end time
def format_time(t):
    if not t or len(t) < 3:  # handle None or malformed
        return "N/A"
    return f"{t[:-2]}:{t[-2:]}"  # split into hours:minutes

# Check if index + metadata already exist
if os.path.exists(index_file) and os.path.exists(metadata_file):
    logger.info("Loading existing FAISS index and metadata...")
    index = faiss.read_index(index_file)
    with open(metadata_file, "r", encoding="utf-8") as f:
        id_to_course_list = json.load(f)
else:
    logger.info("No saved index found. Creating embeddings...")
    # embedding vectors will be stored in index
    index = faiss.IndexFlatL2(dimension)

    id_to_course_list = []  # each position in this list matches FAISS vector position

    # Batch process all courses for efficiency
    logger.info(f"Processing {len(courses)} courses...")
    all_texts = []
    all_entries = []

    for crn, course in courses.items():
        try:
            # create string version of course
            text = course_to_text(course)
            all_texts.append(text)
            all_entries.append({"crn": crn, "course": course})
        except Exception as e:
            logger.error(f"Failed to process course {crn}: {e}")
            continue

    # Get all embeddings in token-aware batches
    logger.info("Generating embeddings in batches...")
    embeddings = get_embeddings_batch(all_texts)

    # Add all embeddings to FAISS index
    logger.info("Adding embeddings to FAISS index...")
    for embedding, entry in zip(embeddings, all_entries):
        vector = np.array([embedding], dtype="float32")
        index.add(vector)
        id_to_course_list.append(entry)

    # Save FAISS index and metadata
    logger.info(f"Saving index with {len(id_to_course_list)} courses...")
    faiss.write_index(index, index_file)
    with open(metadata_file, "w", encoding="utf-8") as f:
        json.dump(id_to_course_list, f, indent=2)

# Extract valid subject codes from loaded course data
def get_valid_subjects() -> set:
    """
    Extract all unique subject codes from the loaded course data.

    Returns:
        Set of valid subject codes (e.g., {'MATH', 'CSCI', 'ENGL', ...})
    """
    subjects = set()
    for entry in id_to_course_list:
        course = entry.get("course", {})
        subject = course.get("subject", "")
        if subject:
            subjects.add(subject)
    return subjects

# Initialize valid subjects cache (computed once at startup)
VALID_SUBJECTS = get_valid_subjects()
logger.info(f"Loaded {len(VALID_SUBJECTS)} valid subject codes")

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

def extract_course_code(query: str) -> tuple[str, str]:
    """
    Extract subject and course number from query, validating against real subject codes.
    Returns (subject, course_number) or (None, None) if not found or invalid.

    Examples:
    - "CHEM 1B" -> ("CHEM", "0001B")  # CHEM is valid
    - "MATH 31" -> ("MATH", "0031")   # MATH is valid
    - "CALC 2" -> (None, None)        # CALC is not a valid subject code
    - "math classes" -> (None, None)  # No pattern match
    """
    # Pattern: Subject (2-4 letters) followed by course number (1-4 digits + optional letter)
    import re
    pattern = r'\b([A-Z]{2,4})\s*(\d{1,4}[A-Z]?)\b'
    match = re.search(pattern, query.upper())

    if match:
        subject = match.group(1)
        number = match.group(2)

        # Validate subject code against actual course subjects
        if subject not in VALID_SUBJECTS:
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

def search_courses(query: str, k: int = 3, subject_hint: str = None) -> list[dict]:
    """
    Search for courses using exact match + semantic similarity.

    If query contains a specific course code (e.g., "CHEM 1B"), we first try to find
    exact matches, then fall back to semantic search if not enough results.

    Args:
        query: Natural language search query
        k: Number of results to return
        subject_hint: Optional subject area hint from intent extraction (e.g., "Music", "Computer Science")
                     This overrides automatic subject detection for better accuracy.

    Returns:
        List of course dictionaries
    """
    try:
        # Normalize the query to handle common course code patterns
        normalized_query = normalize_course_query(query)

        # Try to extract exact course code
        subject, course_number = extract_course_code(normalized_query)

        # Detect subject preference - use hint if provided, otherwise auto-detect
        if subject_hint:
            # Map subject hint to subject code (e.g., "Music" -> "MUS")
            # Complete mapping of natural language subject names to subject codes (all 62 subjects)
            subject_mapping = {
                # Common aliases
                "math": "MATH",
                "psych": "PSYC",
                "cs": "CSCI",
                "theater": "THEA",
                "theatre": "THEA",
                "nursing": "NRSR",
                "esl": "ESL",
                # Full subject names from course data
                "applied art and design": "AAD",
                "administration of justice": "ADMJ",
                "advanced manufacturing": "ADVM",
                "agriculture": "AGRI",
                "allied health": "ALH",
                "anthropology": "ANTH",
                "art history": "ARHI",
                "art": "ART",
                "astronomy": "ASTR",
                "athletics": "ATHL",
                "automotive technology": "AUTO",
                "automotive": "AUTO",
                "building industries": "BI",
                "biological sciences": "BIOL",
                "biology": "BIOL",
                "business": "BUS",
                "chemistry": "CHEM",
                "communication studies": "COMM",
                "communication": "COMM",
                "computer science": "CSCI",
                "deaf studies": "DFST",
                "economics": "ECON",
                "education": "EDU",
                "english": "ENGL",
                "engineering": "ENGR",
                "earth science": "ESCI",
                "english as a second language": "ESL",
                "environmental sciences": "ESS",
                "ethnic studies": "ETHN",
                "fashion": "FASH",
                "fire technology": "FIRE",
                "fire science": "FIRE",
                "french": "FREN",
                "geography": "GEOG",
                "german": "GER",
                "human development and family": "HDEV",
                "health education": "HED",
                "history": "HIST",
                "health sciences": "HSCI",
                "humanities": "HUM",
                "information technology": "IT",
                "italian": "ITAL",
                "japanese": "JPN",
                "kinesiology": "KIN",
                "lgbt studies": "LGBT",
                "mathematics": "MATH",
                "mechatronics": "MECH",
                "music": "MUS",
                "nursing assistant": "NRSA",
                "nursing registered": "NRSR",
                "nutrition and food science": "NUTF",
                "nutrition": "NUTF",
                "personal development": "PDEV",
                "philosophy": "PHIL",
                "photography": "PHOT",
                "physics": "PHYS",
                "political science": "POLS",
                "psychology": "PSYC",
                "recreation management": "RECM",
                "rise": "RISE",
                "skill development": "SKDV",
                "sociology": "SOC",
                "spanish": "SPAN",
                "statistics": "STAT",
                "theatre arts": "THEA",
                "welding technology": "WELD",
                "welding": "WELD",
                "women's studies": "WMST",
            }
            preferred_subject = subject_mapping.get(subject_hint.lower())
            if preferred_subject:
                logger.info(f"Using subject hint from intent: {subject_hint} -> {preferred_subject}")
            else:
                logger.warning(f"Subject hint '{subject_hint}' not found in mapping")
        else:
            preferred_subject = detect_subject_preference(normalized_query) if not subject else None

        # If we found a specific course code, try exact match first
        exact_matches = []
        if subject and course_number:
            logger.info(f"Exact course code detected: {subject} {course_number}")

            for entry in id_to_course_list:
                course = entry["course"]
                if (course.get("subject") == subject and
                    course.get("courseNumber") == course_number):
                    exact_matches.append(course)
                    logger.info(f"Found exact match: {subject}{course_number} - {course.get('courseTitle')}")

            # If we have enough exact matches, return those
            if len(exact_matches) >= k:
                logger.info(f"Returning {k} exact matches")
                return exact_matches[:k]
            elif exact_matches:
                logger.info(f"Found {len(exact_matches)} exact matches, will supplement with semantic search")

        # If we found exact matches for a specific course code, ONLY return those
        # Don't mix with other courses from semantic search
        if subject and course_number and exact_matches:
            logger.info(f"Returning {len(exact_matches)} exact matches only (no semantic mixing)")
            return exact_matches[:k]

        # Do semantic search (either as primary method or to supplement exact matches)
        query_emb = np.array([get_embedding(normalized_query)], dtype="float32")
        _, indices = index.search(query_emb, k * 3)  # Get more results for filtering/reranking

        # Collect candidates with subject-based scoring
        candidates = []
        for idx in indices[0]:
            idx = int(idx)
            course = id_to_course_list[idx]["course"]

            # If we have exact matches, skip duplicates
            if exact_matches:
                course_code = f"{course.get('subject')}{course.get('courseNumber')}"
                exact_code = f"{subject}{course_number}"
                if course_code == exact_code:
                    continue  # Skip, already in exact_matches

            # Calculate preference score
            score = 0
            if preferred_subject and course.get("subject") == preferred_subject:
                score = 1  # Boost courses from preferred subject

            candidates.append((course, score))

        # Sort candidates: preferred subject first, then by semantic similarity (order from FAISS)
        candidates.sort(key=lambda x: x[1], reverse=True)
        semantic_results = [course for course, _ in candidates[:k - len(exact_matches)]]

        # Combine exact matches (first) with semantic results
        final_results = exact_matches + semantic_results
        return final_results[:k]

    except Exception as e:
        logger.error(f"Failed to search courses: {e}")
        raise