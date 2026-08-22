"""
Embedding and text processing utilities for OpenAI API.

This module provides helper functions for estimating tokens and
generating embeddings in batches.
"""

import logging
from openai import OpenAI

from src.config import Config

logger = logging.getLogger(__name__)


def estimate_tokens(text: str) -> int:
    """
    Estimate token count for a text string.

    Uses rough approximation: 1 token ≈ 4 characters

    Args:
        text: Text string to estimate tokens for

    Returns:
        Estimated number of tokens

    Example:
        >>> estimate_tokens("Hello, world!")
        3
    """
    return len(text) // 4


def get_embeddings_batch(client: OpenAI, texts: list[str], model: str | None = None) -> list[list[float]]:
    """
    Get embeddings for multiple texts using token-aware batching.

    Uses token-aware batching to stay under OpenAI's 300k token limit per request.
    Processes texts in batches while respecting both token and input count limits.

    Args:
        client: OpenAI client instance
        texts: List of text strings to embed
        model: Embedding model to use (defaults to Config.EMBEDDING_MODEL)

    Returns:
        List of embedding vectors (one per input text)

    Raises:
        Exception: If API call fails

    Example:
        >>> client = OpenAI(api_key="...")
        >>> embeddings = get_embeddings_batch(client, ["text1", "text2"])
        >>> len(embeddings)
        2
    """
    if model is None:
        model = Config.EMBEDDING_MODEL
    try:
        # estimate_tokens (chars/4) undercounts dense course text — course codes,
        # dates, and room labels tokenize denser than prose. Keep a wide margin
        # under OpenAI's hard 300k-tokens-per-request cap so a batch never blows it.
        MAX_TOKENS_PER_BATCH = 200000
        MAX_INPUTS_PER_BATCH = 2048    # OpenAI's input limit

        all_embeddings = []
        current_batch = []
        current_tokens = 0
        batch_num = 1

        for text in texts:
            text_tokens = estimate_tokens(text)

            # Check if adding this text would exceed limits
            if (current_tokens + text_tokens > MAX_TOKENS_PER_BATCH or
                len(current_batch) >= MAX_INPUTS_PER_BATCH):

                # Process current batch
                if current_batch:
                    logger.info(f"Processing batch {batch_num} ({len(current_batch)} texts, ~{current_tokens:,} tokens)...")

                    response = client.embeddings.create(
                        model=model,
                        input=current_batch
                    )

                    embeddings = [item.embedding for item in response.data]
                    all_embeddings.extend(embeddings)

                    batch_num += 1
                    current_batch = []
                    current_tokens = 0

            # Add text to current batch
            current_batch.append(text)
            current_tokens += text_tokens

        # Process final batch
        if current_batch:
            logger.info(f"Processing batch {batch_num} ({len(current_batch)} texts, ~{current_tokens:,} tokens)...")

            response = client.embeddings.create(
                model=model,
                input=current_batch
            )

            embeddings = [item.embedding for item in response.data]
            all_embeddings.extend(embeddings)

        logger.info(f"Completed {batch_num} batches, total {len(all_embeddings)} embeddings")
        return all_embeddings

    except Exception as e:
        logger.error(f"Failed to get batch embeddings: {e}")
        raise


def get_embedding(client: OpenAI, text: str, model: str | None = None) -> list[float]:
    """
    Get embedding vector for a single text string.

    Args:
        client: OpenAI client instance
        text: Text string to embed
        model: Embedding model to use (defaults to Config.EMBEDDING_MODEL)

    Returns:
        Embedding vector as list of floats

    Raises:
        Exception: If API call fails

    Example:
        >>> client = OpenAI(api_key="...")
        >>> embedding = get_embedding(client, "sample text")
        >>> len(embedding)
        1536
    """
    if model is None:
        model = Config.EMBEDDING_MODEL
    try:
        response = client.embeddings.create(
            model=model,
            input=text
        )
        return response.data[0].embedding
    except Exception as e:
        logger.error(f"Failed to get embedding: {e}")
        raise


def course_to_text(course: dict, informalName_func, meetingDays_func, get_campus_func) -> str:
    """
    Convert course dictionary to formatted text string for embedding.

    Args:
        course: Course dictionary with all course data
        informalName_func: Function to format instructor names
        meetingDays_func: Function to format meeting days
        get_campus_func: Function to determine campus from building

    Returns:
        Formatted text string describing the course

    Example:
        >>> from utils.course_formatting import informalName, meetingDays
        >>> from utils.campus import get_campus
        >>> text = course_to_text(course_dict, informalName, meetingDays, get_campus)
    """
    faculty_names = [
        f'{informalName_func(item["name"])}'
        for item in course["faculty"]
    ]

    # Get term - prefer 'term' field, fallback to 'source_term'
    term = course.get("term", course.get("source_term", "Unknown Term"))

    meeting_days = meetingDays_func(course["meetings"])

    faculty_list = [
        f"{item['name']} ({item.get('email', 'no email provided')})"
        for item in course["faculty"]
    ]
    faculty_str = ", ".join(faculty_list) if faculty_list else "No instructor assigned"

    # Get start and end dates from the first meeting
    start_date = "TBA"
    end_date = "TBA"
    campus = "Unknown Campus"
    if course['meetings'] and len(course['meetings']) > 0:
        first_meeting = course['meetings'][0]
        start_date = first_meeting.get('startDate', 'TBA')
        end_date = first_meeting.get('endDate', 'TBA')
        # Determine campus from the first meeting location
        building = first_meeting.get('building', '')
        campus = get_campus_func(building)

    meeting_list = [
        f"{item['days']} {item['begin']}-{item['end']} in {item['building']} {item['room']}"
        for item in course['meetings']
    ]
    meeting_str = "; ".join(meeting_list) if meeting_list else "No meeting info"

    enrollment = {
        "class_max": course['enrollment']['max'],
        "class_enrolled": course['enrollment']['enrolled'],
        "class_available": course['enrollment']['available'],
        "waitlist_enrolled": course['enrollment']['waitCount'],
        "waitlist_max": course['enrollment']['waitCapacity']
    }

    return (
        f"The teacher(s) for this course are {', '.join(faculty_names)}. \n"
        f"This course is in the term: {term}. \n"
        f"Campus: {campus}. \n"
        f"Course runs from {start_date} to {end_date}. \n"
        f"This class meets on the follow day(s): {', '.join(meeting_days)}. \n"
        f"CRN: {course['CRN']}. \n"
        f"{course['subjectDescription']} {course['subject']}{course['courseNumber']}, {course['courseTitle']}. Credits: {course['credits']}.\n"
        f"Meetings: {meeting_str}. \n"
        f"Enrollment: Class Max: {enrollment['class_max']}, Enrolled: {enrollment['class_enrolled']}, Available: {enrollment['class_available']}, Waitlist Enrollment: {enrollment['waitlist_enrolled']}, Waitlist Max: {enrollment['waitlist_max']}. \n"
        f"Attributes: {', '.join(course['attributes']) if course['attributes'] else 'None'}. \n"
    )
