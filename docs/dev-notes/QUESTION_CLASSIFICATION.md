# Question Classification System

## Problem: Irrelevant Course Results

**Issue:** When users ask identity/general questions like "What are you?", the bot would search the course database using semantic similarity, resulting in irrelevant course listings.

**Example:**
```
User: "What are you?"
Bot: [Shows random courses like "Intro to Environmental Science" because
     "Intro" is semantically similar to introductory questions]
```

## Root Cause

The RAG (Retrieval-Augmented Generation) system **always** performed semantic search on the course database, even for questions that had nothing to do with courses. Vector similarity would find courses with words phonetically or semantically close to the user's question.

## Solution: Two-Stage Question Processing

### Stage 1: Question Classification

Before searching courses, we use a **fast LLM classifier** to determine if the question is course-related:

```python
def is_course_related_question(message: str) -> bool:
    """
    Uses GPT-4o-mini to classify question as course-related or general
    Returns True if asking about courses, False otherwise
    """
```

**Classification Examples:**

**COURSE-RELATED (TRUE):**
- "What CS classes are available?"
- "Show me math courses"
- "Are there any biology classes on Monday?"
- "When does calculus start?"
- "What classes does Professor Smith teach?"

**NON-COURSE (FALSE):**
- "What are you?"
- "Who are you?"
- "What is your purpose?"
- "How do you work?"
- "Tell me about yourself"
- "Hello"
- "What can you do?"

### Stage 2: Response Generation

**For Non-Course Questions:**
- Skip the FAISS semantic search entirely
- Use a specialized identity prompt explaining the bot's purpose and capabilities
- Return `courses_searched: 0`

**For Course Questions:**
- Proceed with normal RAG pipeline
- Search FAISS index for relevant courses
- Generate response with course context

## Implementation

### api_server.py Changes

```python
@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    # Step 1: Classify question
    is_course_question = is_course_related_question(request.message)

    if not is_course_question:
        # Handle identity/general questions
        # Use identity prompt, no course search
        return ChatResponse(
            response=response_text,
            courses_searched=0
        )

    # Step 2: For course questions, do RAG
    results = search_courses(request.message, k=request.num_courses)
    # ... generate response with course context
```

## Benefits

### 1. **Relevant Responses**
- Identity questions get proper explanations of the bot's purpose
- Course questions get actual course results

### 2. **Cost Savings**
- No wasted embedding API calls for non-course questions
- Classification is very cheap (10 tokens max)

### 3. **Better User Experience**
- Users can ask "What are you?" and get a helpful explanation
- No confusing random course listings for general questions

### 4. **Faster Response Time**
- Skip expensive FAISS search for non-course questions
- Classification takes ~100-200ms
- FAISS search + embedding takes ~500-1000ms

## Cost Analysis

### Per Request Cost

**Non-Course Question (NEW):**
- Classification: ~20 tokens input + 5 tokens output = $0.000004
- Response generation: ~300 tokens input + 200 tokens output = $0.00012
- **Total: ~$0.00012 per non-course question**
- **No FAISS search, no course embedding needed**

**Course Question (EXISTING):**
- Classification: ~20 tokens input + 5 tokens output = $0.000004
- Query embedding: ~30 tokens = $0.0000006
- Response generation: ~2000 tokens input + 300 tokens output = $0.0009
- **Total: ~$0.0009 per course question**

### Comparison

**Before (without classification):**
- Every question triggered course search: $0.0009
- 100 non-course questions/day: $0.09/day = $2.70/month

**After (with classification):**
- Course questions: $0.0009
- Non-course questions: $0.00012 (87% cheaper!)
- 100 non-course questions/day: $0.012/day = $0.36/month
- **Savings: $2.34/month on non-course questions**

## Identity Prompt

When non-course questions are detected, the bot uses a comprehensive identity prompt:

```
You are Sierra Class Helper, an AI academic advisor created by Sierra College student Ben Rosario.

**Your Purpose:**
I help students at Sierra College find and explore courses across our three campuses...

**How I Work:**
I use a RAG (Retrieval-Augmented Generation) system:
- Course data is scraped from Sierra College's course catalog
- Text descriptions are converted into vector embeddings
- Embeddings are stored in a FAISS vector database
- I use GPT-4o-mini to generate natural language responses

**What I Can Do:**
- Search for courses by subject
- Find courses by schedule
- Check course availability and enrollment
...
```

## Testing

### Test Cases

**Test 1: Identity Question**
```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What are you?"}'

Expected: Explanation of bot purpose, courses_searched: 0
```

**Test 2: Course Question**
```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Show me CS classes"}'

Expected: Course listings, courses_searched: 3-10
```

**Test 3: Greeting**
```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello!"}'

Expected: Friendly greeting, courses_searched: 0
```

### Logs

Check logs for classification:
```
INFO:     Non-course question detected, responding without course search
```

Or for course questions:
```
INFO:     Chat request: Show me CS classes
INFO:     Generated response for: Show me CS classes
```

## Edge Cases

### Classification Failure
If the LLM classifier fails (network error, etc.):
```python
except Exception as e:
    logger.warning(f"Question classification failed, defaulting to course-related: {e}")
    return True  # Safe default: treat as course question
```

This ensures backward compatibility - if classification breaks, the bot still works (just might return irrelevant courses for identity questions like before).

### Ambiguous Questions
For ambiguous questions like "Tell me about programming":
- Could be asking about programming courses
- Could be asking about programming in general

The classifier will likely return TRUE (course-related), which is safer:
- If user wanted general info about programming, they'll get CS courses (still helpful)
- If user wanted CS courses, they'll get exactly what they wanted

## Deployment

### No Additional Setup Required
- Changes are in `api_server.py` only
- No new dependencies
- No environment variables needed
- Just restart your API server:
  ```bash
  # Stop current server (Ctrl+C)
  python api_server.py
  ```

### Rolling Back
If you want to disable classification (revert to always searching courses):
```python
# In api_server.py, change:
is_course_question = is_course_related_question(request.message)

# To:
is_course_question = True  # Always treat as course question
```

## Future Enhancements

### 1. Caching Classification Results
For repeated questions:
```python
classification_cache = {}

def is_course_related_question(message: str) -> bool:
    if message in classification_cache:
        return classification_cache[message]

    # ... do classification
    classification_cache[message] = result
    return result
```

### 2. Regex Pre-Filter (Even Faster)
Before using LLM classifier:
```python
# Quick pattern matching for obvious cases
identity_patterns = [
    r"^(what|who|how) (are|is) you",
    r"^what('s| is) your (name|purpose)",
    r"^(hello|hi|hey)$",
    r"^help$"
]

for pattern in identity_patterns:
    if re.match(pattern, message.lower()):
        return False  # Not course-related
```

### 3. Conversation History
Track if user previously asked identity question:
```python
# If user already knows what you are, treat follow-ups as course questions
if user_id in identity_asked_users:
    return True  # Assume course question
```

## Summary

**Problem:** "What are you?" returned random irrelevant courses

**Solution:** Two-stage processing with question classification

**Result:**
- Identity questions get proper explanations
- Course questions get course results
- 87% cheaper for non-course questions
- Better user experience

**No changes needed to:**
- Discord bot
- Embeddings system
- Frontend/deployment
- Environment variables

Just restart your API server! 🚀
