"""
FastAPI server for Sierra Class Helper
Provides REST API endpoints for course search and chat functionality
"""
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import logging
import os
import json
from datetime import datetime
from openai import OpenAI
from src.embeddings.core import search_courses, course_to_text
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastAPI
app = FastAPI(
    title="Sierra Class Helper API",
    description="API for searching Sierra College courses and getting AI-powered academic advice",
    version="1.0.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure this for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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

# Function to get data freshness info (dynamically checks on each call)
def get_data_last_updated():
    """Get the last modified time of id_to_course.json"""
    import os.path
    if os.path.exists("id_to_course.json"):
        data_timestamp = os.path.getmtime("id_to_course.json")
        return datetime.fromtimestamp(data_timestamp).strftime('%B %d, %Y at %I:%M %p')
    return None

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

def extract_user_intent(query: str) -> dict:
    """
    Extract structured intent from user query using LLM.

    Returns a dictionary with:
    - subject_area: Academic subject (e.g., "Computer Science", "Math")
    - keywords_required: List of terms that must appear in course descriptions
    - keywords_exclude: List of terms that disqualify courses
    - intent_summary: Human-readable description of intent
    """
    try:
        prompt = """Extract the academic intent from this course search query. Return ONLY valid JSON (no markdown, no code blocks).

Return JSON in this exact format:
{
  "subject_area": "Computer Science" or "Math" or "English" or null if unclear,
  "keywords_required": ["keyword1", "keyword2"],
  "keywords_exclude": ["keyword1", "keyword2"],
  "intent_summary": "Brief description of what the user wants",
  "is_specific_course_title": true or false
}

CRITICAL GUIDELINES:
- If the query mentions a specific course title (e.g., "History of Rock and Roll", "Introduction to Psychology", "Creative Writing"), set is_specific_course_title: true
- For specific course titles, extract keywords from the FULL course title, not just individual words
- For example, "History of Rock and Roll" should require ["rock", "roll", "music"] NOT ["history"] because it's a music course
- keywords_required: Terms that MUST appear in relevant courses
- keywords_exclude: Terms that indicate WRONG courses
- Be generous with required keywords (synonyms and related terms)
- Only exclude terms that are clearly opposite to intent

Examples:
Query: "what coding class is best for designers"
{"subject_area": "Computer Science", "keywords_required": ["programming", "coding", "computer science", "software", "web programming"], "keywords_exclude": ["graphic design", "art", "illustration", "visual design"], "intent_summary": "Programming courses suitable for design students", "is_specific_course_title": false}

Query: "History of Rock and Roll classes"
{"subject_area": "Music", "keywords_required": ["rock", "roll", "music"], "keywords_exclude": ["u.s.", "american", "world history", "european"], "intent_summary": "Music course about rock and roll history", "is_specific_course_title": true}

Query: "easy math class"
{"subject_area": "Math", "keywords_required": ["math", "mathematics", "algebra", "statistics"], "keywords_exclude": ["calculus", "advanced", "honors"], "intent_summary": "Introductory or easier mathematics courses", "is_specific_course_title": false}

Query: "Introduction to Psychology"
{"subject_area": "Psychology", "keywords_required": ["psychology", "psych", "intro"], "keywords_exclude": [], "intent_summary": "Introductory psychology course", "is_specific_course_title": true}

Now extract intent from this query:"""

        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": query}
            ],
            temperature=0,
            max_tokens=200
        )

        intent_json = response.choices[0].message.content.strip()

        # Remove markdown code blocks if present
        if intent_json.startswith("```"):
            intent_json = intent_json.split("```")[1]
            if intent_json.startswith("json"):
                intent_json = intent_json[4:]
            intent_json = intent_json.strip()

        intent = json.loads(intent_json)
        logger.info(f"Extracted intent: {intent}")
        return intent
    except Exception as e:
        logger.error(f"Failed to extract intent: {e}")
        # Return default intent on failure
        return {
            "subject_area": None,
            "keywords_required": [],
            "keywords_exclude": [],
            "is_specific_course_title": False,
            "intent_summary": "General course search"
        }

def validate_course_relevance(course: dict, intent: dict) -> bool:
    """
    Validate if a course matches the user's intent using LLM.

    Returns True if relevant, False otherwise.
    """
    try:
        # Quick check: if no intent requirements, accept all courses
        if not intent.get("keywords_required") and not intent.get("intent_summary"):
            return True

        # Build course summary for validation
        course_summary = f"{course.get('subjectDescription', '')} {course.get('subject', '')}{course.get('courseNumber', '')} - {course.get('courseTitle', '')}"

        prompt = f"""Does this course match the user's intent?

Intent: {intent.get('intent_summary', 'General course search')}

Course: {course_summary}

Reply with ONLY 'YES' or 'NO'."""

        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a course relevance validator. Reply with only YES or NO."},
                {"role": "user", "content": prompt}
            ],
            temperature=0,
            max_tokens=5
        )

        answer = response.choices[0].message.content.strip().upper()
        is_relevant = answer == "YES"

        logger.info(f"Course validation: {course_summary[:50]}... -> {answer}")
        return is_relevant
    except Exception as e:
        logger.error(f"Failed to validate course relevance: {e}")
        # On error, accept the course (fail open)
        return True

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

    # Add data freshness warning (dynamically check current timestamp)
    data_last_updated = get_data_last_updated()
    if data_last_updated:
        prompts.append(
            f"Data freshness: The course data was last updated on {data_last_updated}. "
            f"IMPORTANT: At the VERY START of your response (before listing any courses), add this disclaimer with the :bangbang: emoji: "
            f"':bangbang: **Showing [X] of potentially more courses.** Course data was last updated {data_last_updated}. "
            f"Enrollment numbers and availability may have changed. For the most current information, visit the Sierra College website.' "
            f"Replace [X] with the actual number of courses you are showing. Make this disclaimer prominent and eye-catching."
        )

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

@app.post("/search")
async def search(request: CourseSearchRequest):
    """
    Search for courses using semantic similarity

    Returns raw course data without LLM processing
    """
    try:
        logger.info(f"Searching for: {request.query}")
        results = search_courses(request.query, k=request.num_results)
        return {
            "query": request.query,
            "num_results": len(results),
            "courses": results
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

@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    Chat endpoint that searches courses and generates AI response

    Uses RAG to provide context-aware responses
    """
    try:
        logger.info(f"Chat request: {request.message}")

        # Check if this is a course-related question
        is_course_question = is_course_related_question(request.message)

        if not is_course_question:
            # Handle non-course questions without searching courses
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
                {"role": "system", "content": identity_prompt}
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

        # For course-related questions, proceed with normal RAG pipeline
        # Search for relevant courses using current message + context from history
        search_query = request.message

        # Only use conversation context if the query seems like a follow-up (not a new topic)
        if request.conversation_history and len(request.conversation_history) > 0:
            # Detect if this is a follow-up question (starts with "what about", "how about", "and", etc.)
            follow_up_indicators = [
                "what about", "how about", "and what", "what if", "or what",
                "in summer", "in fall", "in winter", "in spring",
                "for summer", "for fall", "for winter", "for spring",
                "and summer", "and fall", "and winter", "and spring",
            ]

            query_lower = request.message.lower()
            is_follow_up = any(indicator in query_lower for indicator in follow_up_indicators)

            # Also check if query is very short (likely incomplete without context)
            is_short_query = len(request.message.split()) <= 4

            # Only add context if this seems like a follow-up or incomplete query
            if is_follow_up or is_short_query:
                # Get last few messages for context (helps with "what about summer?" queries)
                recent_context = " ".join([
                    msg.content for msg in request.conversation_history[-3:]
                    if msg.role == "user"
                ])
                search_query = f"{recent_context} {request.message}"
                logger.info(f"Follow-up detected, enhanced search query with context: {search_query}")
            else:
                logger.info(f"New topic detected, using query without context: {search_query}")

        # Extract user intent for intelligent filtering
        intent = extract_user_intent(search_query)
        logger.info(f"User intent: {intent['intent_summary']}")

        # Search for MORE candidates (30 instead of 3) to allow filtering
        # Pass subject area hint from intent to improve search accuracy
        subject_hint = intent.get("subject_area")
        candidate_results = search_courses(search_query, k=30, subject_hint=subject_hint)

        # Filter candidates using keywords from intent
        filtered_results = []
        keywords_required = [kw.lower() for kw in intent.get("keywords_required", [])]
        keywords_exclude = [kw.lower() for kw in intent.get("keywords_exclude", [])]

        for course in candidate_results:
            # Convert course to text for keyword matching
            course_text = course_to_text(course).lower()

            # Check if course has required keywords (if any specified)
            if keywords_required:
                has_required = any(keyword in course_text for keyword in keywords_required)
            else:
                has_required = True  # No requirements, accept all

            # Check if course has excluded keywords
            has_excluded = any(keyword in course_text for keyword in keywords_exclude)

            # Accept course if it has required keywords and no excluded keywords
            if has_required and not has_excluded:
                filtered_results.append(course)

                # Stop once we have enough candidates for validation
                if len(filtered_results) >= 10:
                    break

        logger.info(f"Filtered {len(candidate_results)} candidates down to {len(filtered_results)} using keywords")

        # If no results after keyword filtering, fallback to original candidates
        if not filtered_results:
            logger.warning("No results after keyword filtering, using original candidates")
            filtered_results = candidate_results[:10]

        # Validate filtered results using LLM (only validate top candidates to save cost)
        validated_results = []
        for course in filtered_results[:6]:  # Validate top 6 to get final 3
            if validate_course_relevance(course, intent):
                validated_results.append(course)

            # Stop once we have enough results
            if len(validated_results) >= request.num_courses:
                break

        # If validation rejected all courses, retry search without subject hint
        # This handles cases where LLM picked wrong subject (e.g., "Engineering" instead of "Mechatronics")
        if not validated_results and subject_hint:
            logger.warning(f"All courses rejected by validation. Subject hint '{subject_hint}' may be incorrect.")
            logger.info("Retrying search without subject hint...")

            # Retry search without subject bias
            retry_candidates = search_courses(search_query, k=30, subject_hint=None)

            # Filter retry candidates with keywords
            retry_filtered = []
            for course in retry_candidates:
                course_text = course_to_text(course).lower()
                has_required = any(keyword in course_text for keyword in keywords_required) if keywords_required else True
                has_excluded = any(keyword in course_text for keyword in keywords_exclude)

                if has_required and not has_excluded:
                    retry_filtered.append(course)
                    if len(retry_filtered) >= 10:
                        break

            # Validate retry results
            for course in retry_filtered[:6]:
                if validate_course_relevance(course, intent):
                    validated_results.append(course)
                if len(validated_results) >= request.num_courses:
                    break

            if validated_results:
                logger.info(f"Retry successful: Found {len(validated_results)} courses without subject hint")

        # Use validated results
        results = validated_results if validated_results else []

        logger.info(f"Final results after validation: {len(results)} courses")

        # If still no results after retry, inform user
        if not results:
            logger.warning("No courses found matching user intent after validation and retry")
            # Don't show rejected courses - instead inform user nothing matches
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
                "content": f"User query (in {lang_name}): {request.message}\n\nNo courses were found matching this query. The search looked for courses related to '{intent.get('intent_summary', request.message)}' but could not find any matches.\n\nIMPORTANT: Respond in {lang_name}. Politely inform the user that no courses match their specific query, and suggest they try different search terms or ask about related subjects."
            })

            completion = openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages
            )

            return ChatResponse(
                response=completion.choices[0].message.content,
                courses_searched=0
            )

        # Build context from search results
        context = "\n".join(course_to_text(r) for r in results)

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

    except Exception as e:
        logger.error(f"Chat failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)