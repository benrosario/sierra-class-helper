"""
The data-refresh jobs that keep the running API current.

Two things go stale and need periodic refreshing:
  - course data (enrollment, new sections) scraped from Sierra's catalog
  - professor ratings pulled from RateMyProfessors

Each function here does the slow part (hit the network, rebuild the FAISS index,
rewrite JSON on disk) and then refreshes the in-memory copy the API serves from,
so new data goes live without a restart. They're plain blocking functions: the
scheduler runs them in a worker thread (see src.api.scheduler), but they're just
as usable from a one-off script or a manual admin trigger.

The heavy dependencies (Playwright, FAISS, the embedding pipeline) are imported
lazily inside each function on purpose — importing this module should stay cheap
so the API's startup import graph doesn't drag in a browser engine it only needs
once an hour.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def refresh_courses() -> None:
    """
    Re-scrape every currently-registerable term, rebuild the embedding index
    from the fresh data, and swap that index into the live API.

    Terms are scraped independently: if one fails (Sierra changed a page, a
    network blip), we log it and move on rather than throwing away the terms
    that did succeed. The index is only rebuilt if at least one term came back,
    so a total scrape failure leaves the existing data untouched.
    """
    from src.scraper.scraper import get_active_terms, sierra_scrape, prune_course_data
    from src.embeddings import core
    from src.utils.paths import COURSES_INDEX

    terms = get_active_terms()
    if not terms:
        logger.warning("Banner returned no active terms; skipping course refresh.")
        return

    scraped = 0
    for term in terms:
        label = term.get("description", term)
        try:
            sierra_scrape(term)
            scraped += 1
        except Exception:
            logger.exception(f"Scrape failed for term {label!r}; skipping it.")

    if scraped == 0:
        logger.error("All terms failed to scrape; leaving the existing index in place.")
        return

    # Drop ended terms and older duplicate scrapes before rebuilding, so the index
    # reflects only the latest snapshot of currently-active terms.
    prune_course_data(terms)

    logger.info(f"Scraped {scraped}/{len(terms)} term(s); rebuilding the embedding index.")
    if COURSES_INDEX.exists():
        # Normal case: only re-embed courses that actually changed; enrollment-only
        # churn is a cheap metadata update with no OpenAI calls.
        from src.embeddings.fast import fast_incremental_update
        fast_incremental_update()
    else:
        # Fresh volume with no index yet (the fast path would bail here). Build the
        # whole index from scratch so the API has something to serve.
        logger.info("No existing index found; building the full index from scratch.")
        from src.embeddings.incremental import incremental_update
        incremental_update()

    # Point the live API at the freshly written index/metadata.
    core.reload_index()
    logger.info("Course refresh complete; new data is live.")


def refresh_professor_ratings() -> None:
    """
    Re-pull every Sierra professor from RateMyProfessors, then drop the API's
    cached lookup tables so the next rating request reads the new file.
    """
    from src.scraper.ratemyprofessors import refresh_ratings
    from src.utils import professor_ratings

    refresh_ratings()          # rewrites professor_ratings.json
    professor_ratings.reset_cache()  # next get_rating() reloads it from disk
    logger.info("Professor ratings refresh complete; cache cleared.")
