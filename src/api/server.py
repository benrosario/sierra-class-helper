"""
FastAPI server for Sierra Class Helper
Provides REST API endpoints for course search and chat functionality
"""
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import logging
import os
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

# System prompts for the chatbot
def get_system_prompts():
    prompts = [
        "You are a helpful academic advisor named Sierra Class Helper, created by student Ben Rosario.",
        f"Today's date is {datetime.now().strftime('%B %d, %Y')}. Use this date when discussing course schedules and enrollment.",
        "Pay extreme attention to requested dates and times to ensure good responses for users.",
        "You are helping students from the California Community College 'Sierra College'. Their website is https://sierracollege.edu.",
        "Sierra College has TWO active campuses: Rocklin Campus (main campus) and Nevada County Campus (Grass Valley/Tahoe-Truckee area). NOTE: The Roseville Campus is CLOSED and no longer offers courses. When students ask about campus location, clearly state which campus each course is at.",
        "You can advise students on any academic matter, grabbing information from the Sierra College website.",
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

            # Add current message
            messages.append({"role": "user", "content": request.message})

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

        results = search_courses(search_query, k=request.num_courses)

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

        # Add current query with course context
        messages.append({
            "role": "user",
            "content": f"User query: {request.message}\n\nHere are the top {len(results)} matching courses from the search:\n{context}\n\nPlease present these {len(results)} courses to the user, or respond to their question if they're not asking about specific courses."
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