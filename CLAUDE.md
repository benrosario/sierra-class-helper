# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Sierra Class Helper: an AI academic advisor for Sierra College. Two services (FastAPI + Discord bot) backed by a FAISS+lexical hybrid retrieval system over scraped Sierra College course data enriched with RateMyProfessors ratings. Deployed on Railway.

## Common commands

```bash
# Install (once)
pip install -r requirements.txt
playwright install chromium            # --with-deps in containers

# Cold-start data build (nothing in course_data/*, courses.index, id_to_course.json is committed)
python -m src.scraper.cli              # scrape all currently-active terms
python -m src.scraper.ratemyprofessors # fetch professor ratings
python -m src.embeddings.incremental   # full initial index build

# Refresh during dev (scrape + only re-embed changed courses)
python -m src.scraper.cli
python -m src.embeddings.fast

# Run locally (needs .env with OPENAI_API_KEY, DISCORD_BOT_TOKEN)
python api_server.py                   # FastAPI on :8000 (thin entry → src.api.server)
python discord_bot.py                  # separate process; talks to $API_URL
./start_local.sh                       # convenience: both, backgrounded

# Tests
pytest                                 # asyncio_mode=auto, uses tests/conftest.py
pytest tests/test_search_hybrid.py     # single file
pytest -k "hybrid and not integration" # by keyword
pytest -m integration                  # integration tests (need real OPENAI_API_KEY + built index)
```

## Architecture

### Two-service shape

`api` (FastAPI, [src/api/server.py](src/api/server.py)) — RAG + LLM. Also hosts the background refresh scheduler in-process ([src/api/scheduler.py](src/api/scheduler.py)) because Railway volumes are not shareable between services, so a separate scraper container's writes would never reach the API's filesystem. Jobs run under `asyncio.to_thread` so the event loop keeps serving requests.

`bot` (Discord, [src/bot/bot.py](src/bot/bot.py)) — thin frontend. Every user turn calls the API's `/chat`; `/search` calls `/search`. The bot maintains 30-min TTL, 10-turn short-term memory keyed by Discord user ID, sends it as `conversation_history`, and forwards the user ID as `X-Discord-User` so rate limits and analytics key on user rather than shared Railway egress IP.

### Top-level entry points

Only `api_server.py` and `discord_bot.py` remain at the repo root — thin entry points wired into [Procfile](Procfile), [railway.toml](railway.toml), and [start_local.sh](start_local.sh). All real code lives in `src/`.

### Config is authoritative

[src/config.py](src/config.py) is the single source of truth for tunables — model names (`EMBEDDING_MODEL`, `CHAT_MODEL`), embedding dimension, data filenames, env-derived values (`OPENAI_API_KEY`, `ENABLE_SCHEDULER`, `ADMIN_TOKEN`, `DATA_DIR`). Do not hardcode a model string or read `os.environ` directly in a new module — read `Config.X`. [src/utils/paths.py](src/utils/paths.py) composes Config's filenames onto `DATA_DIR`.

### Hybrid retrieval ([src/embeddings/core.py](src/embeddings/core.py))

**Import is side-effect free.** All heavy setup (OpenAI client, course-data load, FAISS build/load, lexical index) is deferred to `initialize()`. The API server's FastAPI lifespan calls it; the CLIs (`src.embeddings.fast`, `src.embeddings.incremental`) build their own OpenAI client lazily via `_get_client()`. Pure functions (`normalize_course_query`, `build_lexical_index`, `lexical_search`, `fuse_candidates`, `all_sections_for`) work uninitialized — tests exercise them without any key. `search_courses` calls `_require_initialized()` and errors if not.

`search_courses` does three things in order:

1. **Exact course-code resolution** via `resolve_course_code` — patterns like "CHEM 1B", "physics 205", or "Chem1B" (`normalize_course_query`) return that single course. Subject codes are validated against `VALID_SUBJECTS` derived from loaded data; natural-language subject names come from `src/utils/subject_mapping.py`.
2. **FAISS vector top-m** on `text-embedding-3-small` (dim 1536).
3. **Lexical (IDF-weighted keyword) top-m** with title weight 3, code weight 3, subject-description weight 1, and a prefix-match penalty (`_PREFIX_PENALTY = 0.6`). Numbers only match exactly.
4. **`fuse_candidates`** blends them: normalized-lexical-magnitude + `vec_weight/(1+rank)` + subject bonus. Deduped by `(subject, courseNumber)` so results are distinct courses, not many sections of one.

Callers then call `all_sections_for(courses)` to expand the top-1 course to every section but cap secondary courses at `others_limit=1` — otherwise a popular course (24 sections of College Algebra) would flood results.

`reload_index()` atomically swaps `index`, `id_to_course_list`, `VALID_SUBJECTS`, and `LEXICAL_INDEX` after a data refresh — no restart needed.

### Chat pipeline ([src/api/server.py](src/api/server.py))

Per `/chat` request:
- `is_course_related_question` (LLM classifier) — non-course questions get `_handle_non_course_question` (identity prompt, no search).
- `_build_search_query` — `detect_topic_continuation` (LLM) decides whether to prepend the last 3 user messages so follow-ups like "what about summer?" carry context.
- `search_courses` → `all_sections_for` → `_augment_with_ratings` prepends per-instructor RateMyProfessors data into each course's LLM context.
- `detect_language` (LLM) reads the current message and instructs the model to reply in the same language.
- Response generated with `gpt-4o-mini` under the system prompts in `get_system_prompts()`.

The "verify on the official site" disclaimer is **not** requested from the model; the bot appends it deterministically in `format_outgoing`. Do not duplicate.

Rate limit key is `X-Discord-User` header, falling back to IP (`_rate_limit_key`). CORS is empty on purpose — server-to-server only.

### Data files and persistence

All persistent state lives at `SIERRA_DATA_DIR` (repo root locally, `/data` on Railway; single source of truth in [src/utils/paths.py](src/utils/paths.py)):

- `courses.index` (FAISS), `id_to_course.json` — one entry per course-section, positionally aligned with the FAISS vectors.
- `course_data/*.json` — scraper output, one file per term-scrape.
- `professor_ratings.json` — RMP cache.
- `course_hashes.json` / `course_hashes_no_enrollment.json` — used by `embeddings_fast.py` so enrollment-only churn is a metadata update with no OpenAI calls.
- `analytics.sqlite` — one row per `/chat`.

None are committed to git. Locally you regenerate them; on Railway they live on a Volume attached to the `api` service and are populated by the in-process scheduler.

### Scheduler ([src/api/scheduler.py](src/api/scheduler.py))

Gated by `SIERRA_ENABLE_SCHEDULER=1` (production only — leave off locally and in tests, or every FastAPI boot fires a real scrape). Two loops:
- courses: `refresh_courses` every hour, first run 30s after boot
- ratings: `refresh_professor_ratings` every 24h; first run is *immediate* on a fresh volume (no ratings file yet), otherwise deferred a full day so redeploys don't hammer RMP.

Refresh jobs live in [src/jobs/refresh.py](src/jobs/refresh.py) and lazy-import Playwright/FAISS so `api_server` startup stays cheap. `refresh_courses` calls the fast incremental rebuild if `courses.index` exists, otherwise falls back to the full incremental build.

### Scraper ([src/scraper/scraper.py](src/scraper/scraper.py))

`get_active_terms()` hits Banner's `getTerms` API (the source of truth for "what's registerable now") and filters out `(View Only)` entries. Container path adds `--no-sandbox --disable-dev-shm-usage` and dumps a screenshot + HTML to `course_data/_debug/` on failure. `prune_course_data()` deletes ended terms and older duplicate scrapes so the index never resurfaces ended classes.

## Environment variables

Read them through `Config`, not `os.environ`. Required: `OPENAI_API_KEY`. For the bot: `DISCORD_BOT_TOKEN`, and `API_URL` (required when `ENVIRONMENT=production` — `Config.require_api_url()` raises otherwise). On Railway `API_URL` is wired to `${{api.RAILWAY_PRIVATE_DOMAIN}}` in [railway.toml](railway.toml). Production-only: `SIERRA_DATA_DIR=/data`, `SIERRA_ENABLE_SCHEDULER=1`, `SIERRA_ADMIN_TOKEN` (protects `/admin/stats`).

## Gotchas

- Do not commit generated data (`courses.index`, `id_to_course.json`, `course_data/`, `professor_ratings.json`, `course_hashes*.json`, `analytics.sqlite`). They are per-machine / per-volume.
- `SIERRA_ENABLE_SCHEDULER` must stay off in tests and local dev unless you want a real scrape to fire at boot.
- Callers of `search_courses` must have called `embeddings_core.initialize()` first (the API's lifespan does this). Pure helpers work uninitialized.
- The bot appends the disclaimer — don't ask the LLM to include one too.
- Rate limits are per Discord user only when the client forwards `X-Discord-User`; direct API callers get per-IP.
- Bot only speaks when @-mentioned in a channel, or DM'd (no command prefix); slash commands are `/ask`, `/search`, `/clear`.
