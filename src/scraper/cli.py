"""
Command-line entry point for the Sierra College course scraper.

Two ways to run:

  Headless (Railway cron, CI, automation):
    python -m src.scraper.cli                  # scrape every term in TERM_MAP
    python -m src.scraper.cli --term spring2026

  Interactive (local exploration):
    python -m src.scraper.cli --interactive    # original prompt-based flow
    python -m src.scraper.cli --term spring2026 --debug   # visible browser

The headless default exists so update_courses.py and Railway cron can invoke
this script without any TTY. Previously the CLI called input() at import time,
which crashes immediately on Railway (no stdin).
"""
from __future__ import annotations

import argparse
import logging
import sys

from src.scraper.scraper import TERM_MAP, sierra_scrape

logger = logging.getLogger(__name__)


def _scrape_one(term: str, debug: bool) -> bool:
    logger.info("=" * 60)
    logger.info(f"Scraping term: {term}")
    logger.info("=" * 60)
    try:
        sierra_scrape(term, debug=debug)
        return True
    except Exception:
        logger.exception(f"Scrape failed for term: {term}")
        return False


def _interactive(debug: bool) -> None:
    """Original prompt-driven flow, kept for local exploration."""
    print("Sierra Class Scraper - v1.0")
    print("Author - Ben Rosario")
    print("\n'exit' to escape.")
    print("Type 'help' for available terms.")

    while True:
        user_input = input("\nenter term: ").strip().lower()

        if user_input in TERM_MAP:
            sierra_scrape(user_input, debug=debug)
            return
        elif user_input == "debug":
            debug = not debug
            print(f"Debug mode: {debug}")
        elif user_input == "help":
            print("\nType 'debug' to toggle debug mode\n")
            print("Allowed parameters:")
            for term in TERM_MAP:
                print(f"  {term}")
        elif user_input == "exit":
            return
        else:
            print("Invalid input")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="sierra-scraper",
        description="Scrape Sierra College course schedule data.",
    )
    parser.add_argument(
        "--term",
        choices=list(TERM_MAP.keys()),
        help="A specific term to scrape. If omitted, scrapes every term in TERM_MAP.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Run the browser in headed mode (local only).",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Prompt-driven mode for local exploration.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    if args.interactive:
        _interactive(args.debug)
        return

    terms = [args.term] if args.term else list(TERM_MAP.keys())
    logger.info(f"Will scrape terms: {terms}")

    failed = [term for term in terms if not _scrape_one(term, debug=args.debug)]

    if failed:
        logger.error(f"FAILED terms: {failed}")
        sys.exit(1)

    logger.info("All terms scraped successfully.")


if __name__ == "__main__":
    main()
