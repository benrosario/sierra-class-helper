"""
FastAPI server for Sierra Class Helper
Provides REST API endpoints for course search and chat functionality
"""
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import logging
from datetime import datetime, timezone

from src.utils.paths import ID_TO_COURSE_JSON
from openai import OpenAI
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from src.config import Config
from src.api.analytics import get_stats, init_schema, record_message
from src.api.scheduler import start_scheduler, stop_scheduler
from src.embeddings import core as embeddings_core
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
    """
    Boot-time setup: initialize the embeddings module (loads the FAISS + lexical
    index, builds the OpenAI client) and analytics schema, then start the
    background refresh scheduler when Config.ENABLE_SCHEDULER is set.
    """
    embeddings_core.initialize()
    init_schema()
    if Config.ENABLE_SCHEDULER:
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

# OpenAI client for chat calls (embeddings live in src.embeddings.core). Built
# lazily on first use so the module imports cleanly in tests that don't hit
# the API — matches the pattern in src.embeddings.core.
_openai_client: OpenAI | None = None


def _get_openai_client() -> OpenAI:
    global _openai_client
    if _openai_client is None:
        if not Config.OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY environment variable is required")
        _openai_client = OpenAI(api_key=Config.OPENAI_API_KEY)
    return _openai_client

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
    data_updated_at: str | None = None  # ISO-8601 UTC timestamp of the index file's last write

# System prompts for the chatbot.
#
# Every /chat request is served by a single model call — no separate LLM
# classifiers for language, topic-continuation, or course-vs-identity. Those
# decisions are folded in here as instructions the model applies against the
# full message + conversation history it already has.
def get_system_prompts():
    prompts = [
        # --- Identity & scope ---
        "You are a helpful academic advisor named Sierra Class Helper, created by student Ben Rosario.",
        f"Today's date is {datetime.now().strftime('%B %d, %Y')}. Use this date when discussing course schedules and enrollment.",
        "Pay extreme attention to requested dates and times to ensure good responses for users.",
        "You are helping students from the California Community College 'Sierra College'. Their website is https://sierracollege.edu.",
        "Sierra College has TWO active campuses: Rocklin Campus (main campus) and Nevada County Campus (Grass Valley/Tahoe-Truckee area). NOTE: The Roseville Campus is CLOSED and no longer offers courses. When students ask about campus location, clearly state which campus each course is at.",

        # --- Language handling (replaces the removed detect_language LLM call) ---
        "IMPORTANT: Reply in the same language the user's most recent message is in. If the current message is too short to identify a language (e.g. a one-word reply), use the language of prior turns in the conversation. Do not switch languages unless the user does.",

        # --- Deciding whether to talk about courses at all
        # (replaces the removed is_course_related_question LLM call) ---
        "The user's message will be followed by a 'Retrieved courses:' block containing the top matches from a semantic search. These are candidates — they are NOT guaranteed to be relevant. Decide whether the user is actually asking about courses. If the user is asking a general question (who you are, what you do, how you work, greetings, small talk, thanks), answer that question directly and DO NOT mention, list, or reference the retrieved courses at all — treat that block as if it weren't there. Only list courses when the user is genuinely asking about them.",
        "If the retrieved-courses block is empty, or if none of the retrieved courses actually match what the user asked for (e.g. they asked about Physics but the retrieval returned unrelated subjects), politely tell the user no matching courses were found and suggest they try different search terms or related subjects. Do NOT invent courses, and do NOT present unrelated courses as if they were matches.",

        # --- Course-listing format (used when courses ARE being presented) ---
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
        "openai_configured": bool(Config.OPENAI_API_KEY),
    }


@app.get("/admin/stats")
async def admin_stats(token: str = ""):
    """
    Return engagement aggregates. Gated by a shared secret to keep the data
    private. Set SIERRA_ADMIN_TOKEN in the api env and pass ?token=... when
    calling.
    """
    if not Config.ADMIN_TOKEN:
        raise HTTPException(status_code=503, detail="Stats endpoint not configured")
    expected = Config.ADMIN_TOKEN
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
            "courses": sections,
            "data_updated_at": _data_last_updated(),
        }
    except Exception as e:
        logger.error(f"Search failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

def _build_search_query(request: ChatRequest) -> str:
    """
    Build the retrieval query.

    If we have prior turns, prepend up to the last two user messages so
    fragment follow-ups ("what about summer?", "which one is online?") have
    enough tokens to retrieve against. New-topic queries are safe from
    contamination: `_STOPWORDS` in the lexical channel drops filler words
    like "classes"/"available", and the IDF weighting means the distinctive
    tokens in the *new* message dominate. That's the reason we can afford to
    skip the previous LLM-based topic-continuation check entirely.
    """
    if not request.conversation_history:
        return request.message

    prior_user_msgs = [
        msg.content for msg in request.conversation_history[-4:]
        if msg.role == "user"
    ][-2:]
    if not prior_user_msgs:
        return request.message

    return " ".join(prior_user_msgs + [request.message])


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


def _generate_response(request: ChatRequest, results: list[dict]) -> ChatResponse:
    """
    Single LLM call that answers every /chat request.

    The system prompts tell the model to (a) reply in the user's language,
    (b) ignore the retrieved-courses block for identity/greeting/non-course
    questions, and (c) tell the user politely when no courses match — so we
    can always retrieve and always pass the results in without branching.
    """
    if results:
        context = "\n".join(_augment_with_ratings(course_to_text(r), r) for r in results)
        retrieval_block = f"Retrieved courses ({len(results)}):\n{context}"
    else:
        retrieval_block = "Retrieved courses: (none returned)"

    messages = [{"role": "system", "content": p} for p in get_system_prompts()]

    if request.conversation_history:
        for msg in request.conversation_history[-10:]:
            messages.append({"role": msg.role, "content": msg.content})

    # Put the retrieval block FIRST and the user's actual message last, so the
    # model reads the query in the user's language most recently. Retrieved
    # course descriptions can be in other languages (e.g. SPAN, FREN course
    # entries) and would otherwise bias the reply language via recency.
    messages.append({
        "role": "user",
        "content": (
            f"{retrieval_block}\n\n---\n"
            f"User message: {request.message}\n\n"
            "Reply in the same language this message is written in."
        ),
    })

    completion = _get_openai_client().chat.completions.create(
        model=Config.CHAT_MODEL,
        messages=messages,
    )

    logger.info(f"Generated response for: {request.message}")
    return ChatResponse(
        response=completion.choices[0].message.content,
        courses_searched=len(results),
        data_updated_at=_data_last_updated(),
    )


def _data_last_updated() -> str | None:
    """
    Return the ISO-8601 UTC timestamp of the last course-index write, or None
    if the file isn't there yet. The scheduler writes id_to_course.json
    immediately after every successful refresh, so its mtime is a reliable
    "when was the currently-served data last built" signal.
    """
    try:
        mtime = ID_TO_COURSE_JSON.stat().st_mtime
    except OSError:
        return None
    return datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()


@app.post("/chat", response_model=ChatResponse)
@limiter.limit("20/minute")
@limiter.limit("200/day")
async def chat(request: Request, body: ChatRequest):
    """
    Chat endpoint that searches courses and generates AI response.

    One retrieval pass + one LLM call per request. Language handling,
    topic-continuation, and course-vs-identity decisions all happen inside
    that single response call via the system prompts — no separate
    classifier round-trips. Rate limited per Discord user (via
    X-Discord-User header) or per IP if header is absent.
    """
    try:
        logger.info(f"Chat request: {body.message}")
        record_message(request.headers.get("X-Discord-User"), len(body.message))

        search_query = _build_search_query(body)
        top_courses = search_courses(search_query, k=body.num_courses)
        results = all_sections_for(top_courses)  # every section of each top course
        logger.info(f"Search returned {len(results)} sections across {len(top_courses)} courses")

        return _generate_response(body, results)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Chat failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=Config.PORT)