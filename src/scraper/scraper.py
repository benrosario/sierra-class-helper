"""
Scrape Sierra College's course catalog using Playwright.

Designed to run in two environments:
  - Locally with a visible browser (--debug) for diagnosis.
  - Headless in a container (Railway cron / CI) with no TTY.

The container path adds Chromium flags that are needed on Railway and similar
hosts (--no-sandbox, --disable-dev-shm-usage) and dumps a screenshot + page
HTML to course_data/_debug/ on any failure so a postmortem is possible without
a live browser.
"""
from __future__ import annotations

import json
import logging
import math
import re
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Optional

from playwright.sync_api import Page, sync_playwright

from src.utils.paths import COURSE_DATA_DIR, DEBUG_DIR

logger = logging.getLogger(__name__)

BANNER_HOST = "https://ss.oci.sierracollege.edu"
REGISTRATION_URL = f"{BANNER_HOST}/StudentRegistrationSsb/ssb/term/termSelection?mode=search"
TERMS_API = f"{BANNER_HOST}/StudentRegistrationSsb/ssb/classSearch/getTerms"

OUTPUT_DIR = COURSE_DATA_DIR


def get_active_terms(max_terms: int = 20) -> list[dict]:
    """
    Fetch the list of currently registerable terms from Banner.

    Returns a list of dicts shaped like {"code": "202680", "description": "Fall 2026"},
    excluding anything marked "(View Only)" (past terms or terms that closed for
    registration). The list comes back newest-first.

    This is the recommended way to know what to scrape: Banner's getTerms API
    is the source of truth and updates the moment Sierra opens a new term.
    """
    params = f"?searchTerm=&offset=1&max={max_terms}&dataType=json"
    req = urllib.request.Request(
        TERMS_API + params,
        headers={"User-Agent": "Mozilla/5.0 (compatible; SierraClassHelper/1.0)"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        terms = json.loads(resp.read().decode("utf-8"))

    active = [
        {"code": t["code"], "description": t["description"]}
        for t in terms
        if "(View Only)" not in (t.get("description") or "")
    ]
    logger.info(f"Banner reports {len(active)} active term(s): {[t['description'] for t in active]}")
    return active


def term_slug(description: str) -> str:
    """
    Derive the filename slug from a term description.

    "Fall 2026" -> "fall2026". Matches the convention course_loader uses to
    extract the source_term from filenames (everything before the first underscore).
    """
    return re.sub(r"[^a-z0-9]", "", description.lower())


def process_json(course):
    """
    Extract and format relevant fields from raw course API response.
    """
    return {
        "term": course.get("termDesc"),
        "CRN": course.get("courseReferenceNumber"),
        "subject": course.get("subject"),
        "courseNumber": course.get("courseNumber"),
        "courseTitle": course.get("courseTitle"),
        "subjectDescription": course.get("subjectDescription"),
        "credits": course.get("creditHourLow"),
        "enrollment": {
            "max": course.get("maximumEnrollment"),
            "enrolled": course.get("enrollment"),
            "available": course.get("seatsAvailable"),
            "waitCapacity": course.get("waitCapacity"),
            "waitCount": course.get("waitCount"),
        },
        "instructionMethod": course.get("instructionalMethodDescription"),
        "faculty": [
            {
                "name": f.get("displayName"),
                "email": f.get("emailAddress"),
            }
            for f in course.get("faculty", [])
        ],
        "meetings": [
            {
                "days": "".join(
                    d for d, flag in {
                        "M": mt.get("monday"),
                        "T": mt.get("tuesday"),
                        "W": mt.get("wednesday"),
                        "Th": mt.get("thursday"),
                        "F": mt.get("friday"),
                    }.items() if flag
                ),
                "begin": mt.get("beginTime"),
                "end": mt.get("endTime"),
                "building": mt.get("buildingDescription"),
                "room": mt.get("room"),
                "startDate": mt.get("startDate"),
                "endDate": mt.get("endDate"),
            }
            for m in course.get("meetingsFaculty", [])
            for mt in [m.get("meetingTime", {})]
        ],
        "attributes": [a.get("description") for a in course.get("sectionAttributes", [])],
    }


def _dump_debug_artifacts(page: Page, term: str, label: str) -> None:
    """Save a full-page screenshot and the rendered HTML so a failure can be diagnosed offline."""
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    stem = f"{term}_{timestamp}_{label}"
    screenshot_path = DEBUG_DIR / f"{stem}.png"
    html_path = DEBUG_DIR / f"{stem}.html"

    try:
        page.screenshot(path=str(screenshot_path), full_page=True)
        logger.error(f"Wrote debug screenshot: {screenshot_path}")
    except Exception as e:
        logger.error(f"Failed to write debug screenshot: {e}")

    try:
        html_path.write_text(page.content(), encoding="utf-8")
        logger.error(f"Wrote debug HTML: {html_path}")
    except Exception as e:
        logger.error(f"Failed to write debug HTML: {e}")


def _select_term(page: Page, description: str) -> None:
    """
    Click the term-selection dropdown and pick the option matching `description`.

    Uses an exact text match against the dropdown's labels. Sierra's Banner
    runs select2 3.x, so the options are `li.select2-result-selectable` and
    the visible text lives in a child `.select2-result-label`.
    """
    logger.info("Opening term dropdown")
    page.locator("#select2-chosen-1").click()

    logger.info("Waiting for dropdown options to populate")
    page.wait_for_selector("li.select2-result-selectable", timeout=10000)

    logger.info(f"Selecting term by exact text: '{description}'")
    exact = re.compile(f"^{re.escape(description)}$")
    page.locator(".select2-result-label").filter(has_text=exact).first.click(timeout=5000)
    logger.info(f"Selected term: '{description}'")


def sierra_scrape(term: dict, debug: bool = False) -> dict:
    """
    Scrape course data for a single term and write it to course_data/.

    `term` is a dict like {"code": "202680", "description": "Fall 2026"} — the
    same shape get_active_terms() returns. We use the description to drive the
    dropdown click and derive a filename slug from it.

    Returns the dict of {CRN: course_data} that was written.

    Raises any Playwright exception after dumping a screenshot + HTML to
    course_data/_debug/. Also raises RuntimeError if the scrape completes but
    captured zero courses, which usually means the page structure changed.
    """
    if not isinstance(term, dict) or "description" not in term:
        raise ValueError(f"term must be a dict with at least 'description'; got: {term!r}")

    description = term["description"]
    slug = term_slug(description)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    courses_processed = 0
    total_classes = 0
    classJson: dict = {}

    def handle_response(response):
        nonlocal courses_processed
        if "ssb/searchResults/searchResults?txt_term=" not in response.url:
            return
        try:
            data = response.json()
        except Exception as e:
            logger.warning(f"Search response was not JSON-decodable: {e}")
            return
        rows = data.get("data") or []
        if not rows:
            return
        for c in rows:
            classJson[c.get("courseReferenceNumber")] = process_json(c)
        courses_processed += len(rows)
        if total_classes:
            pct = (courses_processed / total_classes) * 100
            logger.info(f"Progress: {courses_processed}/{total_classes} ({pct:.1f}%)")
        else:
            logger.info(f"Progress: captured {courses_processed} courses so far")

    # Chromium flags required for containerized hosts like Railway. Safe locally.
    launch_args = []
    if not debug:
        launch_args.extend(["--no-sandbox", "--disable-dev-shm-usage"])

    logger.info(f"Launching Chromium (headless={not debug}, args={launch_args})")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not debug, args=launch_args)
        page: Optional[Page] = None
        try:
            page = browser.new_page()

            logger.info(f"Navigating to {REGISTRATION_URL}")
            page.goto(REGISTRATION_URL, timeout=60000)

            _select_term(page, description)

            logger.info("Clicking 'Continue'")
            page.locator("#term-go").click()

            logger.info("Submitting search for all courses in term")
            page.locator("#search-go").click()

            logger.info("Waiting for results table to render")
            page.wait_for_selector("tbody tr[data-id]", timeout=30000)
            page.wait_for_timeout(3000)

            total_classes_str = page.locator("span.results-out-of").inner_text()
            total_classes = int(total_classes_str.split()[0])
            total_pages = math.ceil(total_classes / 50)
            logger.info(f"Sierra reports {total_classes} courses across {total_pages} pages")

            # Register the response handler AFTER we know total_classes, so the
            # progress percentage is meaningful.
            page.on("response", handle_response)

            logger.info("Setting results-per-page to 50")
            page.select_option("select.page-size-select", "50")
            page.wait_for_timeout(3000)

            for i in range(total_pages):
                page.wait_for_selector("tbody tr[data-id]", timeout=15000)
                page.wait_for_timeout(900)
                if i < total_pages - 1:
                    logger.info(f"Advancing to page {i + 2}/{total_pages}")
                    page.locator("button[title='Next']").click()
                page.wait_for_timeout(2000)

            logger.info(f"Pagination complete. Captured {len(classJson)} unique courses.")

            if len(classJson) == 0:
                logger.error("No courses captured — page structure may have changed.")
                _dump_debug_artifacts(page, slug, "empty_result")
                raise RuntimeError(f"Scrape for {description} produced zero courses")

            timestamp = datetime.now().astimezone().strftime("%Y-%m-%d_%H-%M-%S%z")
            filepath = OUTPUT_DIR / f"{slug}_{timestamp}.json"
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(classJson, f, indent=2, ensure_ascii=False)
            logger.info(f"Wrote {len(classJson)} courses to {filepath}")

            if debug:
                input("Press 'enter' to close...")

            return classJson

        except Exception:
            logger.exception(f"Scrape failed for {description}")
            if page is not None:
                _dump_debug_artifacts(page, slug, "exception")
            raise
        finally:
            browser.close()
