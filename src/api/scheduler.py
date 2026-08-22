"""
In-process background scheduler for the API service.

Why this lives inside the API process instead of a separate cron service:
Railway volumes can't be shared between services, so a standalone scraper
container would write course data to a disk the API never sees. Running the
refresh jobs here means they write to the same volume the API reads from.

The jobs themselves are blocking (Playwright scrape + embedding rebuild), so we
hand each run off to a worker thread via asyncio.to_thread — the event loop
stays free to answer requests while a refresh grinds away in the background.

Enabled only when SIERRA_ENABLE_SCHEDULER=1 (set on the api service in prod;
left off for local dev and tests). The FastAPI lifespan calls start_scheduler()
on boot and stop_scheduler() on shutdown.
"""
from __future__ import annotations

import asyncio
import logging

from src.jobs.refresh import refresh_courses, refresh_professor_ratings
from src.utils.paths import PROFESSOR_RATINGS_JSON

logger = logging.getLogger(__name__)

# Enrollment shifts constantly during registration, so courses refresh hourly.
# RMP ratings barely move, so once a day is plenty.
COURSE_REFRESH_INTERVAL = 60 * 60          # 1 hour, in seconds
RATINGS_REFRESH_INTERVAL = 24 * 60 * 60    # 1 day, in seconds

# Let the app finish booting and answer the first health checks before kicking
# off a heavy scrape on a fresh deploy.
STARTUP_GRACE = 30  # seconds

# Background tasks we own, so stop_scheduler() can cancel them.
_tasks: list[asyncio.Task] = []


async def _run_job(name: str, job) -> None:
    """Run one blocking job in a worker thread and log how it went."""
    logger.info(f"[{name}] starting")
    try:
        await asyncio.to_thread(job)
        logger.info(f"[{name}] done")
    except Exception:
        # A failed run must never break the loop — we'll just try again next tick.
        logger.exception(f"[{name}] failed")


async def _run_periodically(name: str, job, interval: float, initial_delay: float) -> None:
    """
    Run `job` forever: once after `initial_delay`, then every `interval` seconds.

    The wait happens *after* each run, so a slow job can never overlap its own
    next run — the next tick only starts once the current one returns.
    """
    await asyncio.sleep(initial_delay)
    await _run_job(name, job)
    while True:
        await asyncio.sleep(interval)
        await _run_job(name, job)


def start_scheduler() -> None:
    """Launch the refresh loops as background tasks on the running event loop."""
    if _tasks:
        logger.warning("Scheduler already running; ignoring duplicate start_scheduler().")
        return

    # A redeploy should pick up current enrollment, so courses refresh shortly
    # after boot and then hourly.
    _tasks.append(asyncio.create_task(
        _run_periodically(
            "course-refresh", refresh_courses,
            interval=COURSE_REFRESH_INTERVAL, initial_delay=STARTUP_GRACE,
        )
    ))

    # Ratings change slowly, and re-scraping RMP on every deploy is wasteful — so
    # an existing deploy waits a full day before its first run. The exception is
    # a brand-new volume with no ratings file yet: fetch those right away so
    # professor lookups aren't blank for 24 hours.
    ratings_delay = 0 if not PROFESSOR_RATINGS_JSON.exists() else RATINGS_REFRESH_INTERVAL
    _tasks.append(asyncio.create_task(
        _run_periodically(
            "ratings-refresh", refresh_professor_ratings,
            interval=RATINGS_REFRESH_INTERVAL, initial_delay=ratings_delay,
        )
    ))

    logger.info("Background scheduler started (courses: hourly, ratings: daily).")


def stop_scheduler() -> None:
    """Cancel the refresh loops on shutdown."""
    if not _tasks:
        return
    for task in _tasks:
        task.cancel()
    _tasks.clear()
    logger.info("Background scheduler stopped.")
