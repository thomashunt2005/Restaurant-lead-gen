"""
Tool: scrape_serper.py
WAT Layer: Tools (deterministic execution)

Finds restaurants via Serper API (Google Search results).
Runs enough queries to aim for ~3× LEADS_TARGET raw results
(so that scoring/filtering can still hit the target after drop-off).

Each query costs 1 Serper credit (10 results per query).
Default run uses up to 20 queries (~200 raw results for a 50-lead target).

Usage:
    python tools/scrape_serper.py
    python tools/scrape_serper.py --region "Surrey, UK" --leads-target 100

Required .env keys: SERPER_API_KEY, TARGET_REGION, LEADS_TARGET
Writes: .tmp/serper_raw.json
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

SERPER_URL = "https://google.serper.dev/search"

# Broad query bank — diverse intents to maximise unique restaurants found.
# {region} is substituted at runtime.
QUERY_BANK = [
    "independent restaurant {region} reservations",
    "local restaurant {region} book a table",
    "gastropub {region} bookings",
    "bistro {region} reservations",
    "italian restaurant {region} book online",
    "indian restaurant {region} reservations",
    "thai restaurant {region} book a table",
    "fine dining {region} reservations",
    "seafood restaurant {region} booking",
    "family restaurant {region} reservations",
    "chinese restaurant {region} book a table",
    "french restaurant {region} reservations",
    "greek restaurant {region} bookings",
    "japanese restaurant {region} reservations",
    "mediterranean restaurant {region} book online",
    "small restaurant {region} takes bookings",
    "owner run restaurant {region}",
    "neighbourhood restaurant {region}",
    "country pub restaurant {region} food bookings",
    "village restaurant {region} reservations",
    "best local restaurants {region}",
    "award winning restaurant {region}",
    "romantic restaurant {region} book a table",
    "sunday lunch {region} bookings",
    "afternoon tea {region} restaurant reservations",
]


def check_env():
    key = os.getenv("SERPER_API_KEY")
    if not key:
        print("ERROR: SERPER_API_KEY is not set in .env", file=sys.stderr)
        sys.exit(1)
    return key


def serper_search(api_key: str, query: str) -> list[dict]:
    """Run a single Serper search and return organic results."""
    headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}
    payload = {"q": query, "gl": "uk", "hl": "en", "num": 10}

    resp = requests.post(SERPER_URL, headers=headers, json=payload, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    return data.get("organic", [])


def extract_phone(text: str) -> str:
    """Best-effort phone extraction from a snippet string."""
    match = re.search(r"(\+44[\d\s\-]{9,}|0[\d\s\-]{9,})", text)
    if match:
        return re.sub(r"[\s\-]", "", match.group(0))
    return ""


def extract_maps_url(result: dict) -> str:
    """Pull a Google Maps URL if the result or snippet references one."""
    link = result.get("link", "")
    if "maps.google" in link or "google.com/maps" in link:
        return link
    snippet = result.get("snippet", "")
    match = re.search(r"https://maps\.google[^\s]+", snippet)
    return match.group(0) if match else ""


def extract_name(title: str) -> str:
    """
    Extract the restaurant name from a web page title.
    Splits on common separators and takes the first segment,
    then strips trailing page-function words.
    """
    # Split on separators in priority order
    for sep in [" | ", " - ", " – ", " — "]:
        if sep in title:
            title = title.split(sep)[0]
            break
    # Strip trailing ": Home", ": Menu", ": Bistro", etc.
    title = re.sub(
        r"\s*:\s*(home|menu|bookings?|reservations?|restaurant|bistro|pub|bar|cafe)$",
        "", title, flags=re.IGNORECASE,
    )
    # Strip "Book Online at " / "Welcome to " prefixes
    title = re.sub(r"^(book\s+online\s+at|welcome\s+to\s+(the\s+)?)\s*", "", title, flags=re.IGNORECASE)
    return title.strip()


def normalise_result(result: dict, query: str) -> dict:
    snippet = result.get("snippet", "")
    return {
        "source": "serper",
        "query": query,
        "name": extract_name(result.get("title", "")),
        "formatted_address": snippet.split("\n")[0],
        "phone": extract_phone(snippet),
        "website": result.get("link", ""),
        "maps_url": extract_maps_url(result),
        "snippet": snippet,
    }


def main():
    parser = argparse.ArgumentParser(description="Scrape Serper for restaurants")
    parser.add_argument("--region", default=os.getenv("TARGET_REGION", "Hampshire, UK"))
    parser.add_argument(
        "--leads-target",
        type=int,
        default=int(os.getenv("LEADS_TARGET", "50")),
        help="Target number of qualified leads (used to calculate how many raw results to collect)",
    )
    parser.add_argument(
        "--raw-multiplier",
        type=float,
        default=3.0,
        help="Collect this many × leads-target raw results before stopping (default: 3.0)",
    )
    parser.add_argument(
        "--max-queries",
        type=int,
        default=len(QUERY_BANK),
        help="Hard cap on queries regardless of target (default: all queries in bank)",
    )
    args = parser.parse_args()

    api_key = check_env()
    out_path = Path(".tmp/serper_raw.json")
    out_path.parent.mkdir(exist_ok=True)

    raw_target = int(args.leads_target * args.raw_multiplier)
    queries = [q.format(region=args.region) for q in QUERY_BANK[: args.max_queries]]

    print(f"Region:      {args.region}")
    print(f"Leads target: {args.leads_target}  ->  collecting ~{raw_target} raw results")
    print(f"Query bank:  {len(queries)} queries available")
    print()

    all_results: list[dict] = []

    for i, query in enumerate(queries):
        if len(all_results) >= raw_target:
            print(f"Raw target ({raw_target}) reached after {i} queries — stopping early.")
            break

        print(f"  [{i+1}/{len(queries)}] {query}")
        try:
            organic = serper_search(api_key, query)
            parsed = [normalise_result(r, query) for r in organic]
            all_results.extend(parsed)
            print(f"    -> {len(parsed)} results  (running total: {len(all_results)})")
        except requests.HTTPError as e:
            print(f"  ERROR: {e}", file=sys.stderr)

        time.sleep(0.5)  # Polite delay between queries

    out_path.write_text(json.dumps(all_results, indent=2, ensure_ascii=False), encoding="utf-8")
    credits_used = min(i + 1, len(queries))
    print(f"\nDone. {len(all_results)} raw results written to {out_path}")
    print(f"Serper credits used this run: ~{credits_used}")


if __name__ == "__main__":
    main()
