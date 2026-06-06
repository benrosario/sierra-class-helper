"""
FastAPI server for Sierra Class Helper
Provides REST API endpoints for course search and chat functionality
"""
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import logging
import os
import json
from datetime import datetime
from openai import OpenAI
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
# Load environment variables from .env BEFORE importing local modules. Several
# of them read env vars at import time — src.utils.paths reads SIERRA_DATA_DIR,
# and src.embeddings.core requires OPENAI_API_KEY — so .env must be applied
# first. On Railway these vars come from the service config and load_dotenv() is
# a harmless no-op (it never overrides vars already present in the environment).
from dotenv import load_dotenv
load_dotenv()

from src.api.analytics import get_stats, init_schema, record_message
from src.api.scheduler import start_scheduler, stop_scheduler
from src.embeddings.core import search_courses, course_to_text, all_sections_for
from src.utils.professor_ratings import get_rating, format_rating

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def _rate_limit_key(request: Request) -> str:
    """
    Key requests by Discord user when available, falling back to IP.

    All bot traffic shares one Railway IP, so we can't usefully rate limit on IP
    alone. The bot forwards the Discord user ID in X-Discord-User; for any other
    caller we degrade to per-IP.
    """
    return request.headers.get("X-Discord-User") or get_remote_address(request)


limiter = Limiter(key_func=_rate_limit_key)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start the in-process scheduler on app boot (gated by env var)."""
    init_schema()
    if os.environ.get("SIERRA_ENABLE_SCHEDULER") == "1":
        start_scheduler()
    else:
        logger.info("SIERRA_ENABLE_SCHEDULER not set; skipping background scheduler.")
    yield
    stop_scheduler()


# Initialize FastAPI
app = FastAPI(
    title="Sierra Class Helper API",
    description="API for searching Sierra College courses and getting AI-powered academic advice",
    version="1.0.0",
    lifespan=lifespan,
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS is intentionally restrictive: the only client is the Discord bot, which calls
# this API server-to-server (no browser involved). If a browser-based client is added
# later, list its exact origin here.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-Discord-User"],
)

# Initialize OpenAI client
openai_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

# Request/Response models
class CourseSearchRequest(BaseModel):
    query: str
    num_results: int = 3

class ConversationMessage(BaseModel):
    role: str  # "user" or "assistant"
    content: str

class ChatRequest(BaseModel):
    message: str
    num_courses: int = 3
    conversation_history: list[ConversationMessage] | None = None  # Optional: previous messages

class ChatResponse(BaseModel):
    response: str
    courses_searched: int

def detect_language(text: str, conversation_history: list = None) -> tuple[str, str]:
    """
    Detect the language of the input text.

    Args:
        text: The user's current message
        conversation_history: Optional list of previous conversation messages to establish context

    Returns:
        tuple: (language_code, language_name)
        e.g., ('en', 'English'), ('es', 'Spanish'), ('uk', 'Ukrainian')
    """
    # Language code to name mapping
    language_names = {
        'en': 'English',
        'es': 'Spanish',
        'fr': 'French',
        'de': 'German',
        'it': 'Italian',
        'pt': 'Portuguese',
        'ru': 'Russian',
        'uk': 'Ukrainian',
        'zh-cn': 'Chinese (Simplified)',
        'zh-tw': 'Chinese (Traditional)',
        'ja': 'Japanese',
        'ko': 'Korean',
        'ar': 'Arabic',
        'hi': 'Hindi',
        'vi': 'Vietnamese',
        'th': 'Thai',
        'pl': 'Polish',
        'nl': 'Dutch',
        'tr': 'Turkish',
        'sv': 'Swedish',
        'da': 'Danish',
        'no': 'Norwegian',
        'fi': 'Finnish',
        'cs': 'Czech',
        'ro': 'Romanian',
        'el': 'Greek',
        'he': 'Hebrew',
        'id': 'Indonesian',
        'ms': 'Malay',
        'tl': 'Tagalog',
    }

    # Always use LLM for language detection on every query
    # This allows users to switch languages mid-conversation and handles all edge cases
    detection_prompt = f"""What language is this text written in?
Text: "{text}"

Reply with ONLY the language name from this list: English, Spanish, French, German, Italian, Portuguese, Russian, Ukrainian, Chinese, Japanese, Korean, Arabic, Hindi, Vietnamese, Thai, Polish, Dutch, Turkish, Swedish, Danish, Norwegian, Finnish, Czech, Romanian, Greek, Hebrew, Indonesian, Malay, Tagalog.

If the text appears to be English with technical terms or abbreviations, reply with "English"."""

    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a language detection expert. Reply with only the language name."},
                {"role": "user", "content": detection_prompt}
            ],
            temperature=0,
            max_tokens=10
        )

        detected_lang_name = response.choices[0].message.content.strip()

        # Map language name back to code
        name_to_code = {v: k for k, v in language_names.items()}
        llm_lang_code = name_to_code.get(detected_lang_name, 'en')

        logger.info(f"LLM language detection: '{text}' -> {detected_lang_name} ({llm_lang_code})")

        lang_name = language_names.get(llm_lang_code, detected_lang_name)
        return (llm_lang_code, lang_name)
    except Exception as e:
        logger.error(f"Failed to detect language with LLM: {e}")
        # On error, default to English
        return ('en', 'English')

def detect_topic_continuation(current_message: str, conversation_history: list) -> bool:
    """
    Intelligently detect if the current message is a continuation of the previous topic
    or if it's a new, unrelated question.

    Args:
        current_message: The user's current message
        conversation_history: List of previous conversation messages

    Returns:
        bool: True if same topic (use context), False if new topic (ignore context)
    """
    if not conversation_history or len(conversation_history) == 0:
        return False

    # Get the last few user messages for context
    recent_user_messages = [
        msg.content for msg in conversation_history[-3:]
        if msg.role == "user"
    ]

    if not recent_user_messages:
        return False

    # Combine recent messages
    previous_context = " | ".join(recent_user_messages)

    try:
        prompt = f"""Determine if the new message is a continuation of the previous conversation topic or a completely new topic.

Previous conversation:
{previous_context}

New message:
{current_message}

Respond with ONLY "SAME" if the new message is asking about the same general topic/subject area as the previous conversation (e.g., follow-up questions, asking about different semesters of the same subject, clarifying questions).

Respond with ONLY "NEW" if the new message is asking about a completely different topic/subject area (e.g., switching from Computer Science to English, or from Math to Music).

Examples:
- Previous: "What CS classes are available?" | New: "Which one teaches algorithms?" → SAME
- Previous: "Show me math classes" | New: "What about in summer?" → SAME
- Previous: "Computer science classes in fall" | New: "When does it meet?" → SAME
- Previous: "What programming classes can I take?" | New: "English 1B in spring" → NEW
- Previous: "Show me biology courses" | New: "What about chemistry classes?" → NEW

Your response (SAME or NEW):"""

        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are an expert at detecting conversation topic changes. Reply with only SAME or NEW."},
                {"role": "user", "content": prompt}
            ],
            temperature=0,
            max_tokens=5
        )

        result = response.choices[0].message.content.strip().upper()
        is_same_topic = result == "SAME"

        logger.info(f"Topic detection: Previous=[{previous_context[:50]}...] | Current=[{current_message}] → {result} (same_topic={is_same_topic})")
        return is_same_topic

    except Exception as e:
        logger.error(f"Failed to detect topic continuation: {e}")
        # On error, default to treating as new topic to avoid contamination
        return False


# System prompts for the chatbot
def get_system_prompts():
    prompts = [
        "You are a helpful academic advisor named Sierra Class Helper, created by student Ben Rosario.",
        f"Today's date is {datetime.now().strftime('%B %d, %Y')}. Use this date when discussing course schedules and enrollment.",
        "Pay extreme attention to requested dates and times to ensure good responses for users.",
        "You are helping students from the California Community College 'Sierra College'. Their website is https://sierracollege.edu.",
        "Sierra College has TWO active campuses: Rocklin Campus (main campus) and Nevada County Campus (Grass Valley/Tahoe-Truckee area). NOTE: The Roseville Campus is CLOSED and no longer offers courses. When students ask about campus location, clearly state which campus each course is at.",
        "You can advise students on any academic matter, grabbing information from the Sierra College website.",
        "IMPORTANT: ALWAYS copy the language of the user. Example: If a user speaks to you in Ukrainian, respond in Ukrainian.",
        "IMPORTANT: When listing courses, ALWAYS show the full course name (e.g., 'College Algebra (MATH0012)') at the start of each course listing.",
        "IMPORTANT: ALWAYS include the CRN (Course Reference Number) for EVERY course you list. The CRN is critical information that students need to register. Example format: 'CRN: 12345' or include it prominently in the course listing.",
        "IMPORTANT: ALWAYS include the instructor's name for EVERY course you list. If multiple instructors, list all of them. If no instructor is assigned, clearly state 'Instructor: TBA' or 'No instructor assigned'.",
        "When a course's context includes 'Instructor ratings (RateMyProfessors):', append the rating in parentheses right after the instructor's name (e.g., 'Instructor: Dan Groff (RateMyProfessors: 3.8/5, 27 ratings, 75% would take again)'). If no rating data is provided for an instructor, simply omit it — do NOT say 'no rating available' or similar. Do not invent ratings.",
        "IMPORTANT: ALWAYS show the campus location prominently for each course (e.g., 'Rocklin Campus', 'Nevada County Campus', 'Online', or 'Unknown Campus').",
        "IMPORTANT: You are being provided with the TOP matching courses based on semantic search. There may be many more courses available. Do NOT say these are the ONLY courses - say 'Here are the top matches' or 'Here are some relevant courses'.",
        "IMPORTANT: Count the courses carefully. If you receive 3 courses, say '3 courses', not '2 courses'.",
        "Make sure to show class start and end dates, and comment on whether or not the class has begun instruction by checking today's date.",
        "Make sure to format enrollment numbers as fractions for a cleaner presentation (e.g., '24/35 enrolled').",
        "IMPORTANT: ALWAYS show waitlist information for EVERY course. Format it as 'Waitlist: X/Y' where X is current waitlist enrollment and Y is waitlist capacity. Always include this, even if the waitlist is 0/20 or 0/0.",
        "If the user doesn't ask for/about classes, do not show them.",
        "Change dates from 'M' to 'Monday' and so on, also change times from '900' to '9:00am', for example.",
        "Make sure to give the building letter for classes, not just the building name. Make sure to append the building letter directly next to the room number, e.g. V303.",
    ]

    # NOTE: The "this is AI, verify on the official site" disclaimer is appended
    # deterministically by the Discord bot on every outgoing message (see
    # src/bot/bot.py), so it is intentionally NOT requested from the model here.
    return prompts

@app.get("/")
async def root():
    """Health check endpoint"""
    return {
        "status": "online",
        "service": "Sierra Class Helper API",
        "version": "1.0.0"
    }

@app.get("/health")
async def health():
    """Detailed health check"""
    return {
        "status": "healthy",
        "openai_configured": bool(os.environ.get("OPENAI_API_KEY")),
    }


@app.get("/admin/stats")
async def admin_stats(token: str = ""):
    """
    Return engagement aggregates. Gated by a shared secret to keep the data
    private. Set SIERRA_ADMIN_TOKEN in the api env and pass ?token=... when
    calling.
    """
    expected = os.environ.get("SIERRA_ADMIN_TOKEN")
    if not expected:
        raise HTTPException(status_code=503, detail="Stats endpoint not configured")
    if token != expected:
        raise HTTPException(status_code=403, detail="Invalid token")
    return get_stats()

@app.post("/search")
async def search(request: CourseSearchRequest):
    """
    Search for courses using semantic similarity

    Returns raw course data without LLM processing
    """
    try:
        logger.info(f"Searching for: {request.query}")
        # num_results is the number of distinct courses; expand each to all of its
        # sections so students see every meeting time / instructor / CRN.
        courses = search_courses(request.query, k=request.num_results)
        sections = all_sections_for(courses)
        # Attach each section's RateMyProfessors rating here. The bot formats the
        # /search results itself, but it runs as a separate service without the
        # ratings file, so the lookup has to happen on the API side.
        for course in sections:
            course["instructorRating"] = _primary_instructor_rating(course)
        return {
            "query": request.query,
            "num_results": len(sections),
            "courses": sections
        }
    except Exception as e:
        logger.error(f"Search failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

def is_course_related_question(message: str) -> bool:
    """
    Determine if the user's question is asking about courses or is a general/identity question
    Uses a quick LLM check to classify the question type
    """
    classification_prompt = """You are a question classifier. Determine if the user's question is asking about COURSES/CLASSES at a college, or if it's a general/identity question.

Examples of COURSE-RELATED questions (return TRUE):
- "What CS classes are available?"
- "Show me math courses"
- "Are there any biology classes on Monday?"
- "When does calculus start?"
- "What classes does Professor Smith teach?"

Examples of NON-COURSE questions (return FALSE):
- "What are you?"
- "Who are you?"
- "What is your purpose?"
- "How do you work?"
- "Tell me about yourself"
- "Hello"
- "What can you do?"

Respond with ONLY "TRUE" or "FALSE"."""

    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": classification_prompt},
                {"role": "user", "content": f"Is this asking about courses/classes? '{message}'"}
            ],
            temperature=0,
            max_tokens=10
        )

        answer = response.choices[0].message.content.strip().upper()
        return answer == "TRUE"
    except Exception as e:
        logger.warning(f"Question classification failed, defaulting to course-related: {e}")
        # Default to treating as course-related to maintain backward compatibility
        return True

def _handle_non_course_question(request: ChatRequest) -> ChatResponse:
    """
    Handle general/identity questions without course search.

    Args:
        request: The chat request containing user message and history

    Returns:
        ChatResponse with identity/general information and 0 courses searched
    """
    logger.info("Non-course question detected, responding without course search")

    identity_prompt = """You are Sierra Class Helper, an AI academic advisor created by Sierra College student Ben Rosario.

**Your Purpose:**
I help students at Sierra College (a California Community College) find and explore courses across our two active campuses:
- Rocklin Campus (main campus)
- Nevada County Campus (Grass Valley/Tahoe-Truckee area)

Note: The Roseville Campus is no longer open.

**How I Work:**
I use a RAG (Retrieval-Augmented Generation) system:
- Course data is scraped from Sierra College's course catalog
- Text descriptions are converted into vector embeddings using OpenAI's text-embedding-3-small model
- Embeddings are stored in a FAISS vector database for fast semantic search
- When you ask about courses, I search for semantically similar courses
- I use GPT-4o-mini to generate natural language responses based on the retrieved courses

**What I Can Do:**
- Search for courses by subject (e.g., "Show me CS classes")
- Find courses by schedule (e.g., "What math classes are on Monday?")
- Check course availability and enrollment
- Show course details (instructor, time, location, campus)
- Answer questions about course schedules and requirements

**Technical Stack:**
- Backend: FastAPI (Python)
- Vector Database: FAISS
- Embeddings: OpenAI text-embedding-3-small
- LLM: OpenAI GPT-4o-mini
- Bot Interface: Discord.py

**Data Freshness:**
Course data is updated regularly from Sierra College's course catalog. Check the Sierra College website for the most current enrollment information.

Ask me about any courses at Sierra College!"""

    messages = [
        {"role": "system", "content": identity_prompt},
        # Anchor the model to the real date — without this, general questions like
        # "what day is it?" get answered from the model's training cutoff.
        {"role": "system", "content": f"Today's date is {datetime.now().strftime('%B %d, %Y')}."},
    ]

    # Add conversation history if provided
    if request.conversation_history:
        for msg in request.conversation_history[-10:]:  # Limit to last 10 messages
            messages.append({"role": msg.role, "content": msg.content})

    # Detect language and add current message with explicit language instruction
    lang_code, lang_name = detect_language(request.message, request.conversation_history)
    messages.append({
        "role": "user",
        "content": f"{request.message}\n\nIMPORTANT: Respond in {lang_name}."
    })

    completion = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages
    )

    response_text = completion.choices[0].message.content

    return ChatResponse(
        response=response_text,
        courses_searched=0
    )


def _build_search_query(request: ChatRequest) -> str:
    """
    Build enhanced search query with conversation context if needed.

    Intelligently detects if the current message is a follow-up to previous
    conversation and adds context accordingly.

    Args:
        request: The chat request with message and conversation history

    Returns:
        str: Enhanced search query (with or without context)
    """
    search_query = request.message

    # Only use conversation context if the query seems like a follow-up (not a new topic)
    if request.conversation_history and len(request.conversation_history) > 0:
        # Use GPT to intelligently detect if this is a continuation of the previous topic
        is_same_topic = detect_topic_continuation(
            request.message,
            request.conversation_history
        )

        # Only add context if this is truly a follow-up on the same topic
        if is_same_topic:
            # Get last few messages for context (helps with "what about summer?" queries)
            recent_context = " ".join([
                msg.content for msg in request.conversation_history[-3:]
                if msg.role == "user"
            ])
            search_query = f"{recent_context} {request.message}"
            logger.info(f"Same topic continuation detected, enhanced search query with context: {search_query}")
        else:
            logger.info(f"New topic detected, using query without previous context: {search_query}")

    return search_query




def _handle_no_results(request: ChatRequest) -> ChatResponse:
    """
    Generate a response when the search returns nothing for a course query.

    Args:
        request: The chat request with user message and history

    Returns:
        ChatResponse informing the user no matches were found
    """
    logger.warning("No courses found for query")

    lang_code, lang_name = detect_language(request.message, request.conversation_history)

    messages = [
        {"role": "system", "content": prompt}
        for prompt in get_system_prompts()
    ]

    if request.conversation_history:
        for msg in request.conversation_history[-10:]:
            messages.append({"role": msg.role, "content": msg.content})

    messages.append({
        "role": "user",
        "content": f"User query (in {lang_name}): {request.message}\n\nNo courses were found matching this query.\n\nIMPORTANT: Respond in {lang_name}. Politely inform the user that no courses match their specific query, and suggest they try different search terms or ask about related subjects."
    })

    completion = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages
    )

    return ChatResponse(
        response=completion.choices[0].message.content,
        courses_searched=0
    )


def _primary_instructor_rating(course: dict) -> str | None:
    """
    Return a short RMP rating string for a course's first instructor, or None.

    Used to enrich /search results so the bot can show ratings without needing
    the ratings file itself. Only the primary (first-listed) instructor is looked
    up to keep the /search output compact.
    """
    faculty = course.get("faculty") or []
    if not faculty:
        return None
    rating = get_rating(faculty[0].get("name", ""))
    if not rating:
        return None
    return format_rating(rating) or None


def _augment_with_ratings(course_text: str, course: dict) -> str:
    """Append RateMyProfessors ratings (if any) under a course's LLM context block."""
    rating_lines = []
    for item in course.get("faculty", []) or []:
        name = item.get("name", "")
        rating = get_rating(name)
        if not rating:
            continue
        formatted = format_rating(rating)
        if not formatted:
            continue
        line = f"  {name}: {formatted}"
        if rating.get("url"):
            line += f" (source: {rating['url']})"
        rating_lines.append(line)
    if not rating_lines:
        return course_text
    return course_text + "Instructor ratings (RateMyProfessors):\n" + "\n".join(rating_lines) + "\n"


def _generate_course_response(request: ChatRequest, results: list[dict]) -> ChatResponse:
    """
    Generate final LLM response with course data.

    Args:
        request: The chat request with user message and history
        results: List of validated course results to include

    Returns:
        ChatResponse with generated answer and course count
    """
    # Build context from search results (with professor ratings appended where available)
    context = "\n".join(_augment_with_ratings(course_to_text(r), r) for r in results)

    # Generate response using OpenAI
    messages = [
        {"role": "system", "content": prompt}
        for prompt in get_system_prompts()
    ]

    # Add conversation history if provided
    if request.conversation_history:
        for msg in request.conversation_history[-10:]:  # Limit to last 10 messages
            messages.append({"role": msg.role, "content": msg.content})

    # Detect the language of the user's query (using conversation history for context)
    lang_code, lang_name = detect_language(request.message, request.conversation_history)

    # Add current query with course context and explicit language instruction
    messages.append({
        "role": "user",
        "content": f"User query (in {lang_name}): {request.message}\n\nHere are the top {len(results)} matching courses from the search:\n{context}\n\nIMPORTANT: Respond in {lang_name}. Present these {len(results)} courses to the user, or respond to their question if they're not asking about specific courses."
    })

    completion = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages
    )

    response_text = completion.choices[0].message.content

    logger.info(f"Generated response for: {request.message}")

    return ChatResponse(
        response=response_text,
        courses_searched=len(results)
    )


@app.post("/chat", response_model=ChatResponse)
@limiter.limit("20/minute")
@limiter.limit("200/day")
async def chat(request: Request, body: ChatRequest):
    """
    Chat endpoint that searches courses and generates AI response

    Uses RAG to provide context-aware responses. Rate limited per Discord user
    (via X-Discord-User header) or per IP if header is absent.
    """
    try:
        logger.info(f"Chat request: {body.message}")
        record_message(request.headers.get("X-Discord-User"), len(body.message))

        # Check if this is a course-related question
        is_course_question = is_course_related_question(body.message)

        if not is_course_question:
            return _handle_non_course_question(body)

        # Course-related: build a context-aware query and trust hybrid search.
        # (The old extract-intent -> keyword-filter -> LLM-validate gate was both a
        # source of false "no results" and the bulk of the per-request LLM cost;
        # hybrid retrieval in search_courses now does the relevance work.)
        search_query = _build_search_query(body)
        top_courses = search_courses(search_query, k=body.num_courses)
        results = all_sections_for(top_courses)  # every section of each top course
        logger.info(f"Search returned {len(results)} sections across {len(top_courses)} courses")

        if not results:
            return _handle_no_results(body)

        return _generate_course_response(body, results)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Chat failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)