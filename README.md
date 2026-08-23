# Sierra Class Helper

An AI-powered academic advisor for Sierra College students. Students can search for courses and get personalized academic advice through a Discord bot interface.

## Architecture

```
Discord Bot (discord_bot.py)
    ↓ HTTP
FastAPI Server (api_server.py)
    ↓
RAG System (embeddings.py)
    ↓
OpenAI API
```

## Features

- **Semantic Course Search**: Find courses using natural language queries
- **AI Academic Advisor**: Get personalized course recommendations
- **Discord Integration**: Easy access through Discord commands
- **REST API**: Flexible API for future integrations

## Setup

### Prerequisites

- Python 3.9+
- OpenAI API key
- Discord bot token (for Discord integration)

### Installation

1. **Clone the repository** (or navigate to project directory)

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Install Playwright browsers** (for web scraping)
   ```bash
   playwright install chromium
   ```

4. **Set up environment variables**
   ```bash
   cp .env.example .env
   ```

   Edit `.env` and add your API keys:
   ```
   OPENAI_API_KEY=your_openai_api_key_here
   DISCORD_BOT_TOKEN=your_discord_bot_token_here
   ```

5. **Cold start: build the data files**

   None of the generated data (`courses.index`, `id_to_course.json`,
   `professor_ratings.json`, `course_data/`) is committed to git — it's
   regenerated locally and lives on the Railway volume in production. On a fresh
   checkout, build it once:

   ```bash
   python -m src.scraper.cli            # scrape active terms -> course_data/*.json
   python -m src.scraper.ratemyprofessors  # fetch ratings -> professor_ratings.json
   python -m src.embeddings.incremental     # full index build -> courses.index + id_to_course.json
   ```

   On Railway you don't run any of this by hand: with `SIERRA_ENABLE_SCHEDULER=1`
   the API's in-process scheduler scrapes + rebuilds hourly and refreshes ratings
   daily, building the index from scratch automatically on a fresh volume.

## Running Locally

### Option 1: API Server Only

Start the FastAPI server:
```bash
python api_server.py
```

The API will be available at `http://localhost:8000`

Test it:
```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What math classes are available?"}'
```

### Option 2: Discord Bot + API Server

1. **Terminal 1 - Start the API server:**
   ```bash
   python api_server.py
   ```

2. **Terminal 2 - Start the Discord bot:**
   ```bash
   python discord_bot.py
   ```

3. **Use in Discord:**
   ```
   !ask What CS classes are available on Monday?
   !search calculus
   ```

## Deployment

### Deploying to Railway/Render/Fly.io

1. **Create two services:**
   - Service 1: API Server (`api_server.py`)
   - Service 2: Discord Bot (`discord_bot.py`)

2. **Set environment variables** on your hosting platform:
   ```
   OPENAI_API_KEY=your_key
   DISCORD_BOT_TOKEN=your_token
   API_URL=https://your-api-server.railway.app
   ```

3. **Upload your data files:**
   - `courses.index`
   - `id_to_course.json`
   - `course_data/` directory

4. **Deploy:**
   - API Server: `python api_server.py`
   - Discord Bot: `python discord_bot.py`

### Example Railway Configuration

**api-server/railway.toml:**
```toml
[build]
builder = "NIXPACKS"

[deploy]
startCommand = "python api_server.py"
```

**discord-bot/railway.toml:**
```toml
[build]
builder = "NIXPACKS"

[deploy]
startCommand = "python discord_bot.py"
```

## API Endpoints

### `GET /`
Health check

### `GET /health`
Detailed health status

### `POST /search`
Search for courses (returns raw data)

**Request:**
```json
{
  "query": "calculus",
  "num_results": 3
}
```

**Response:**
```json
{
  "query": "calculus",
  "num_results": 3,
  "courses": [...]
}
```

### `POST /chat`
Chat with AI advisor (returns formatted response)

**Request:**
```json
{
  "message": "What math classes are on Monday?",
  "num_courses": 3
}
```

**Response:**
```json
{
  "response": "Here are the math classes available on Monday...",
  "courses_searched": 3
}
```

## Discord Commands

- `!ask <question>` - Ask the AI advisor a question
- `!search <query>` - Search for courses (raw results)
- `!help` - Show help information

## Project Structure

```
class-gpt/
├── api_server.py          # Entry point → src/api/server.py
├── discord_bot.py         # Entry point → src/bot/bot.py
├── src/
│   ├── api/               # FastAPI server, scheduler, analytics
│   ├── bot/               # Discord bot
│   ├── embeddings/        # FAISS index build/load + hybrid search
│   ├── scraper/           # Playwright scraper + RMP fetcher
│   ├── utils/             # Formatting, paths, subject map, sanitize
│   ├── jobs/              # Background refresh jobs
│   └── config.py          # Single source of truth for tunables
├── requirements.txt
├── .env.example
├── course_data/           # Scraped course data (JSON) — not committed
├── courses.index          # FAISS vector index — not committed
└── id_to_course.json      # Course metadata — not committed
```

## Development

### Updating Course Data

> **Important:** scraping alone does **not** change what the bot shows. The API
> serves from the prebuilt FAISS index (`courses.index` / `id_to_course.json`), so
> a refresh is always **scrape → rebuild the index → (re)load**.

In production the scheduler does the whole refresh automatically (see the
cold-start note in Setup). To refresh manually during local development:

```bash
python -m src.scraper.cli            # 1. scrape all active terms (or --term <slug>)
python -m src.embeddings.fast        # 2. rebuild the index (re-embeds only changes)
# 3. restart the API server (or let the in-process scheduler hot-swap on next tick)
```

The scraper also prunes `course_data/` to the latest snapshot of currently-active
terms — ended terms and older duplicate scrapes are deleted — so the index never
resurfaces classes that are over.

### Testing

Test the API:
```bash
# Health check
curl http://localhost:8000/health

# Search
curl -X POST http://localhost:8000/search \
  -H "Content-Type: application/json" \
  -d '{"query": "computer science", "num_results": 5}'

# Chat
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "I want to learn programming"}'
```

## Troubleshooting

### "OPENAI_API_KEY is required" error
Make sure you've set the `OPENAI_API_KEY` environment variable in your `.env` file.

### "DISCORD_BOT_TOKEN is required" error
Set the `DISCORD_BOT_TOKEN` in your `.env` file. Get it from [Discord Developer Portal](https://discord.com/developers/applications).

### Discord bot not responding
1. Check that the API server is running
2. Verify `API_URL` points to the correct server
3. Check bot permissions in Discord (needs to read messages)

### Embeddings taking too long
The first run creates embeddings for all courses, which can take a few minutes. Subsequent runs load from the saved index file.

## Support

For issues or questions, contact benrosario@berkeley.edu.
