"""
RateMyProfessors scraper for Sierra College.

Fetches every professor RMP has on file for Sierra College and writes their
ratings to professor_ratings.json. Designed to run as a daily Railway cron.

This calls RMP's undocumented public GraphQL endpoint (the same one their
website uses). It is not an official API. The auth header used here is the
constant Basic token that ships in their public JS bundle. Expect to need to
refresh queries here if RMP changes their schema.
"""
import json
import logging
import urllib.request
import urllib.error
from pathlib import Path

from src.utils.paths import PROFESSOR_RATINGS_JSON

logger = logging.getLogger(__name__)

GRAPHQL_URL = "https://www.ratemyprofessors.com/graphql"
# Hardcoded basic auth shipped in RMP's public JS bundle ("test:test"). Not a secret.
AUTH_HEADER = "Basic dGVzdDp0ZXN0"
SCHOOL_NAME = "Sierra College"
SCHOOL_CITY = "Rocklin"  # disambiguates from any other "Sierra College" in the DB
OUTPUT_FILE = str(PROFESSOR_RATINGS_JSON)
PAGE_SIZE = 1000

SCHOOL_SEARCH_QUERY = """
query SchoolSearch($text: String!) {
  newSearch {
    schools(query: {text: $text}) {
      edges {
        node {
          id
          legacyId
          name
          city
          state
        }
      }
    }
  }
}
"""

TEACHER_SEARCH_QUERY = """
query TeacherSearch($count: Int!, $cursor: String, $schoolID: ID!) {
  newSearch {
    teachers(query: {text: "", schoolID: $schoolID}, first: $count, after: $cursor) {
      edges {
        cursor
        node {
          id
          legacyId
          firstName
          lastName
          avgRating
          numRatings
          avgDifficulty
          wouldTakeAgainPercent
          department
        }
      }
      pageInfo {
        hasNextPage
        endCursor
      }
      resultCount
    }
  }
}
"""


def _graphql(query: str, variables: dict) -> dict:
    """POST a GraphQL query and return the parsed `data` payload."""
    body = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    req = urllib.request.Request(
        GRAPHQL_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": AUTH_HEADER,
            # RMP returns 403 to unbranded user agents
            "User-Agent": "Mozilla/5.0 (compatible; SierraClassHelper/1.0)",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))

    if "errors" in payload:
        raise RuntimeError(f"GraphQL errors: {payload['errors']}")
    return payload["data"]


def find_school_id(name: str, city: str | None = None) -> str:
    """Look up the RMP school ID (Relay-encoded) for a school by name."""
    data = _graphql(SCHOOL_SEARCH_QUERY, {"text": name})
    edges = data["newSearch"]["schools"]["edges"]
    if not edges:
        raise RuntimeError(f"No RMP school found matching '{name}'")

    # If a city was provided, prefer the matching one; otherwise take the first hit.
    if city:
        for edge in edges:
            node = edge["node"]
            if node.get("city", "").lower() == city.lower():
                logger.info(f"Matched school: {node['name']} in {node['city']}, {node['state']} (id={node['legacyId']})")
                return node["id"]

    node = edges[0]["node"]
    logger.info(f"Using first school match: {node['name']} in {node.get('city')}, {node.get('state')} (id={node['legacyId']})")
    return node["id"]


def fetch_professors(school_id: str) -> list[dict]:
    """Paginate through every professor at the school and return raw nodes."""
    all_profs: list[dict] = []
    cursor: str | None = None

    while True:
        data = _graphql(
            TEACHER_SEARCH_QUERY,
            {"count": PAGE_SIZE, "cursor": cursor, "schoolID": school_id},
        )
        teachers = data["newSearch"]["teachers"]
        page = [edge["node"] for edge in teachers["edges"]]
        all_profs.extend(page)

        logger.info(f"Fetched {len(page)} professors (running total: {len(all_profs)} of {teachers.get('resultCount', '?')})")

        if not teachers["pageInfo"]["hasNextPage"]:
            break
        cursor = teachers["pageInfo"]["endCursor"]

    return all_profs


def _make_key(first: str, last: str) -> str:
    """Build the lookup key used in professor_ratings.json: 'last, first' lowercased."""
    return f"{last.strip()}, {first.strip()}".lower()


def _node_to_record(node: dict) -> dict:
    """Convert an RMP teacher node into our compact stored format."""
    first = node.get("firstName") or ""
    last = node.get("lastName") or ""
    legacy_id = node.get("legacyId")
    return {
        "first": first,
        "last": last,
        "rating": node.get("avgRating"),
        "difficulty": node.get("avgDifficulty"),
        "num_ratings": node.get("numRatings") or 0,
        "would_take_again_pct": node.get("wouldTakeAgainPercent"),
        "department": node.get("department"),
        "rmp_id": legacy_id,
        "url": f"https://www.ratemyprofessors.com/professor/{legacy_id}" if legacy_id else None,
    }


def refresh_ratings(output_path: str = OUTPUT_FILE) -> dict:
    """End-to-end: find school, fetch all professors, write JSON. Returns the dict written."""
    logger.info(f"Looking up '{SCHOOL_NAME}' on RateMyProfessors...")
    school_id = find_school_id(SCHOOL_NAME, SCHOOL_CITY)

    logger.info("Fetching all professors...")
    nodes = fetch_professors(school_id)

    ratings: dict[str, dict] = {}
    skipped = 0
    for node in nodes:
        first = (node.get("firstName") or "").strip()
        last = (node.get("lastName") or "").strip()
        if not first or not last:
            skipped += 1
            continue
        # If RMP has multiple entries for the same name (rare; usually one is empty),
        # keep the entry with the most ratings.
        key = _make_key(first, last)
        existing = ratings.get(key)
        if existing and (existing.get("num_ratings") or 0) >= (node.get("numRatings") or 0):
            continue
        ratings[key] = _node_to_record(node)

    logger.info(f"Writing {len(ratings)} professors to {output_path} (skipped {skipped} with missing names)")
    Path(output_path).write_text(json.dumps(ratings, indent=2, sort_keys=True), encoding="utf-8")
    return ratings


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        refresh_ratings()
    except Exception as e:
        logger.error(f"Ratings refresh failed: {e}")
        raise


if __name__ == "__main__":
    main()
