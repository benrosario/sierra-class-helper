# Conversation History Feature

## Overview

The bot now maintains **per-user conversation history** to provide contextual responses. This allows users to have natural follow-up conversations without repeating information.

## How It Works

### Example Conversation

**User:** "What Math30 classes are available in spring?"
**Bot:** *[Lists MATH0030 courses for Spring 2026]*

**User:** "What about in summer?"
**Bot:** *[Understands context and searches for MATH0030 courses in Summer 2026]*

## Technical Implementation

### Architecture: Stateless (Client-Side History)

- **Discord Bot** stores conversation history in memory (per user)
- **API Server** receives history with each request
- **No database required** - simple in-memory storage

### Data Flow

```
1. User sends: "What Math30 classes in spring?"
   └─> Bot stores: {user_id: [{role: "user", content: "What Math30..."}]}
   └─> API receives: {message: "What Math30...", conversation_history: null}
   └─> API responds with course list
   └─> Bot stores: {user_id: [{user msg}, {role: "assistant", content: "Here are..."}]}

2. User sends: "What about summer?"
   └─> Bot retrieves user's history
   └─> API receives: {message: "What about summer?", conversation_history: [{previous messages}]}
   └─> API uses context: "What Math30 classes in spring? What about summer?"
   └─> API responds with summer courses
```

## Features

### 1. **Automatic Context Understanding**

The bot enhances search queries using recent conversation history:

```python
# If user previously asked "What Math30 classes in spring?"
# Then asks "What about summer?"

# Search query becomes: "What Math30 classes in spring? What about summer?"
# This helps find relevant courses even with vague follow-ups
```

### 2. **Per-User History**

Each user has their own conversation history:
- User A's conversation is separate from User B's
- History is tied to Discord user ID
- Works across channels and DMs

### 3. **History Limits**

To avoid memory issues and token limits:
- **Max messages per user:** 20 messages (10 exchanges)
- **Sent to API:** Last 10 messages (5 exchanges)
- **Search context:** Last 3 user messages
- Automatically trims old messages

### 4. **Clear Command**

Users can reset their conversation:

```
!clear
```

Response: "✅ Your conversation history has been cleared! I'll start fresh with your next message."

## Configuration

### In `discord_bot.py`:

```python
class SierraClassHelper(commands.Cog):
    def __init__(self, bot):
        self.conversation_history = {}  # {user_id: [{role, content}, ...]}
        self.max_history_per_user = 20  # Max messages to store
```

### In `api_server.py`:

```python
# Limit sent to LLM: last 10 messages
if request.conversation_history:
    for msg in request.conversation_history[-10:]:
        messages.append({"role": msg.role, "content": msg.content})

# Search context: last 3 user messages
recent_context = " ".join([
    msg.content for msg in request.conversation_history[-3:]
    if msg.role == "user"
])
```

## API Changes

### Request Model (Updated)

```python
class ConversationMessage(BaseModel):
    role: str  # "user" or "assistant"
    content: str

class ChatRequest(BaseModel):
    message: str
    num_courses: int = 3
    conversation_history: list[ConversationMessage] | None = None  # NEW!
```

### Example API Request

**Without history:**
```json
{
  "message": "What CS classes are available?",
  "num_courses": 3
}
```

**With history:**
```json
{
  "message": "What about summer?",
  "num_courses": 3,
  "conversation_history": [
    {"role": "user", "content": "What CS classes are available in spring?"},
    {"role": "assistant", "content": "Here are 3 CS classes in Spring 2026..."},
    {"role": "user", "content": "Tell me more about the first one"},
    {"role": "assistant", "content": "CSCI0026 is Discrete Structures..."}
  ]
}
```

## Backwards Compatibility

✅ **Fully backwards compatible!**

- `conversation_history` is optional
- If not provided, bot works exactly as before
- Existing API clients don't need updates

## Memory Management

### Per-User Storage

Assuming average message length of ~100 characters:
- 20 messages × 100 chars = 2KB per user
- 1000 active users = 2MB total
- **Very lightweight!**

### Automatic Cleanup

History is **lost when bot restarts** (stored in memory, not database):
- This is acceptable for a Discord bot
- Users can use `!clear` to reset manually
- Could add persistence with Redis/database if needed

## Token Usage Impact

### Before (No History)

```
System prompts: ~500 tokens
Course context: ~2000 tokens
User message: ~30 tokens
Total: ~2530 tokens per request
```

### After (With History)

```
System prompts: ~500 tokens
Conversation history: ~300 tokens (last 10 messages)
Course context: ~2000 tokens
User message: ~30 tokens
Total: ~2830 tokens per request
```

**Cost increase:** ~12% per request with history

For most requests (first message from user), there's **no history** so **no cost increase**.

## Benefits

### 1. **Better User Experience**

Users can have natural conversations:
- "Show me math classes"
- "What about the one taught by Smith?"
- "When does it meet?"

### 2. **Improved Search Accuracy**

Context from previous messages improves search:
- "What about summer?" → "What Math30 classes in spring? What about summer?"
- Detects subject preference from earlier messages

### 3. **Reduced User Frustration**

No need to repeat course codes or subjects:
- Before: "Show me MATH0030 in spring" → "Show me MATH0030 in summer"
- After: "Show me MATH0030 in spring" → "What about summer?"

## Limitations

### 1. **Memory-Based (Lost on Restart)**

History is stored in bot's memory and lost on restart.

**Solutions:**
- Accept this limitation (reasonable for Discord bot)
- Add Redis for persistence if needed
- Export history logs for analytics

### 2. **No Cross-Channel Context**

Each conversation is isolated to where it happens.

**Current behavior:** History persists per user across channels
**Alternative:** Could isolate by channel if preferred

### 3. **Token Limits**

Very long conversations could approach token limits.

**Mitigation:**
- Limit to last 10 messages sent to API
- Limit to last 3 user messages for search context
- User can use `!clear` to reset

## Testing

### Test Case 1: Follow-up Question

```
User: !ask What Math30 classes in spring?
Bot: [Lists MATH0030 Spring courses]

User: !ask What about summer?
Bot: [Lists MATH0030 Summer courses]  ✅ Should work!
```

### Test Case 2: Clear History

```
User: !ask What CS classes are available?
Bot: [Lists CS courses]

User: !clear
Bot: ✅ Your conversation history has been cleared!

User: !ask What about in fall?
Bot: [Should ask for clarification, no context]  ✅ Correct!
```

### Test Case 3: Different Users

```
User A: !ask What bio classes in spring?
User B: !ask What about summer?

Bot to User B: [Should NOT use User A's context]  ✅ Isolated!
```

## Deployment

### No Additional Setup Required!

1. **Restart API server**:
   ```bash
   python api_server.py
   ```

2. **Restart Discord bot**:
   ```bash
   python discord_bot.py
   ```

That's it! Conversation history will work automatically.

### No Environment Variables

No new environment variables or configuration needed.

### No Database

Everything is in-memory, no database setup required.

## Future Enhancements

### 1. **Persistent History (Optional)**

Add Redis for persistent storage:

```python
import redis

class SierraClassHelper(commands.Cog):
    def __init__(self, bot):
        self.redis = redis.Redis(host='localhost', port=6379)

    def get_user_history(self, user_id):
        history = self.redis.get(f"history:{user_id}")
        return json.loads(history) if history else []
```

### 2. **Export History**

Add command to export conversation:

```python
@commands.command(name="export")
async def export_command(self, ctx):
    """Export your conversation history"""
    history = self.get_user_history(ctx.author.id)
    # Create text file and send to user
```

### 3. **Smart Context Trimming**

Instead of keeping last N messages, keep most relevant:
- Prioritize messages with course codes
- Keep messages from current topic
- Remove off-topic messages

### 4. **Analytics**

Track conversation patterns:
- Common follow-up questions
- Context success rate
- Average conversation length

## Summary

**Added:** Conversation history for contextual responses
**Complexity:** Medium (100-150 lines of code)
**Cost Impact:** ~12% more tokens (only when history exists)
**Memory Impact:** ~2KB per active user
**Setup Required:** None (just restart bot)

**Result:** Users can have natural conversations without repeating themselves! 🎉
