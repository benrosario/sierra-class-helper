"""
Command-line entry point for the Sierra College course scraper.

Which terms get scraped is decided at runtime by Banner's getTerms API
(get_active_terms), not a hardcoded list — the moment Sierra opens a new term
for registration it shows up here automatically.

Two ways to run:

  Headless (Railway cron, CI, automation):
    python -m src.scraper.cli                 # scrape every active term
    python -m src.scraper.cli --term fall2026  # scrape one term by slug

  Interactive (local exploration):
    python -m src.scraper.cli --interactive          # pick a term from a menu
    python -m src.scraper.cli --term fall2026 --debug  # visible browser

The headless default exists so automation can invoke this without a TTY.
"""
from __future__ import annotations

import argparse
import logging
import sys

from src.scraper.scraper import get_active_terms, sierra_scrape, term_slug, prune_course_data

logger = logging.getLogger(__name__)


def _scrape_one(term: dict, debug: bool) -> bool:
    """Scrape a single term dict, returning True on success. Never raises."""
    logger.info("=" * 60)
    logger.info(f"Scraping term: {term['description']}")
    logger.info("=" * 60)
    try:
        sierra_scrape(term, debug=debug)
        return True
    except Exception:
        logger.exception(f"Scrape failed for term: {term['description']}")
        return False


def _select_term(terms: list[dict], slug: str) -> dict | None:
    """Find the active term whose description slug matches `slug` (e.g. 'fall2026')."""
    target = slug.strip().lower()
    for term in terms:
        if term_slug(term["description"]) == target:
            return term
    return None


def _interactive(terms: list[dict], debug: bool) -> None:
    """Print a numbered menu of active terms and scrape the one the user picks."""
    print("Sierra Class Scraper")
    print("Author - Ben Rosario\n")
    print("Active terms:")
    for i, term in enumerate(terms, start=1):
        print(f"  {i}. {term['description']}  ({term_slug(term['description'])})")

    while True:
        choice = input("\nPick a term number ('exit' to quit): ").strip().lower()
        if choice == "exit":
            return
        if choice.isdigit() and 1 <= int(choice) <= len(terms):
            _scrape_one(terms[int(choice) - 1], debug=debug)
            return
        print("Invalid choice — enter one of the numbers above, or 'exit'.")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="sierra-scraper",
        description="Scrape Sierra College course schedule data.",
    )
    parser.add_argument(
        "--term",
        metavar="SLUG",
        help="Scrape one term by its slug (e.g. 'fall2026'). If omitted, scrapes "
             "every active term. Run with --interactive to see the available slugs.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Run the browser in headed mode (local only).",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Pick a term from a menu instead of scraping all of them.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    # The term list is the source of truth for everything below.
    terms = get_active_terms()
    if not terms:
        logger.error("Banner reported no active terms; nothing to scrape.")
        sys.exit(1)

    if args.interactive:
        _interactive(terms, debug=args.debug)
        return

    if args.term:
        term = _select_term(terms, args.term)
        if term is None:
            available = ", ".join(term_slug(t["description"]) for t in terms)
            logger.error(f"No active term matches '{args.term}'. Available: {available}")
            sys.exit(1)
        ok = _scrape_one(term, debug=args.debug)
        sys.exit(0 if ok else 1)

    # Default: scrape every active term, reporting any that failed.
    logger.info(f"Will scrape {len(terms)} term(s): {[t['description'] for t in terms]}")
    failed = [t["description"] for t in terms if not _scrape_one(t, debug=args.debug)]

    # Now that every active term has been refreshed, drop stale files (ended terms,
    # older duplicate scrapes) so a rebuild doesn't resurrect classes that are over.
    prune_course_data(terms)

    if failed:
        logger.error(f"FAILED terms: {failed}")
        sys.exit(1)

    logger.info("All terms scraped successfully.")


if __name__ == "__main__":
    main()
