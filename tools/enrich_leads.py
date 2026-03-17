"""
Tool: enrich_leads.py
WAT Layer: Tools (deterministic execution)

For each deduplicated lead, runs a targeted Serper search to find:
  - Phone number
  - Website
  - Additional detail useful for personalisation (cuisine note, description)

Each lead costs 1 Serper credit. For 150 leads = 150 credits.
Results are written back to .tmp/merged_leads.json (in-place enrichment)
so that score_leads.py can read the enriched data without changes.

Use --limit to test on a small batch before running the full set.

Usage:
    python tools/enrich_leads.py
    python tools/enrich_leads.py --limit 10

Required .env keys: SERPER_API_KEY
Reads:  .tmp/merged_leads.json
Writes: .tmp/merged_leads.json  (enriched, in-place)
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
IN_OUT_PATH = Path(".tmp/merged_leads.json")

UK_PHONE_RE = re.compile(r"(\+44[\d\s\-]{9,}|0[\d\s\-]{9,})")
URL_RE = re.compile(r"https?://[^\s\)\"'>]+")


def check_env():
    key = os.getenv("SERPER_API_KEY")
    if not key:
        print("ERROR: SERPER_API_KEY is not set in .env", file=sys.stderr)
        sys.exit(1)
    return key


def build_query(lead: dict) -> str:
    name = lead.get("name", "")
    address = lead.get("formatted_address", "")
    # Extract first part of address as area (before first comma)
    area = address.split(",")[0].strip() if address else ""
    return f'"{name}" {area} restaurant'


def serper_search(api_key: str, query: str) -> dict:
    headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}
    payload = {"q": query, "gl": "uk", "hl": "en", "num": 5}
    resp = requests.post(SERPER_URL, headers=headers, json=payload, timeout=10)
    resp.raise_for_status()
    return resp.json()


def extract_phone(text: str) -> str:
    match = UK_PHONE_RE.search(text)
    if match:
        return re.sub(r"[\s\-]", "", match.group(0))
    return ""


def best_website(organic: list[dict], name: str) -> str:
    """
    Prefer results where the domain looks like the restaurant's own site.
    Skip known aggregators.
    """
    aggregators = {
        "tripadvisor", "opentable", "google", "yelp", "facebook",
        "instagram", "twitter", "deliveroo", "just-eat", "uber",
        "timeout", "squaremeal", "hardens", "bookatable",
    }
    name_words = set(re.sub(r"[^\w]", " ", name.lower()).split())

    for result in organic:
        link = result.get("link", "")
        domain = re.sub(r"https?://(www\.)?", "", link).split("/")[0].lower()
        if any(agg in domain for agg in aggregators):
            continue
        # Bonus: domain contains a word from the restaurant name
        domain_words = set(re.sub(r"[^\w]", " ", domain).split())
        if name_words & domain_words:
            return link
    # Fall back to first non-aggregator result
    for result in organic:
        link = result.get("link", "")
        domain = re.sub(r"https?://(www\.)?", "", link).split("/")[0].lower()
        if not any(agg in domain for agg in aggregators):
            return link
    return ""


def enrich_lead(lead: dict, data: dict) -> dict:
    """Fill in missing fields from Serper search results."""
    organic = data.get("organic", [])
    knowledge_graph = data.get("knowledgeGraph", {})

    # Combine all text for phone extraction
    all_text = " ".join(
        r.get("snippet", "") + " " + r.get("title", "") for r in organic
    )
    all_text += " " + json.dumps(knowledge_graph)

    # Phone — fill only if missing
    if not lead.get("phone"):
        # Knowledge graph has the cleanest phone
        kg_phone = knowledge_graph.get("phone", "")
        if kg_phone:
            lead["phone"] = re.sub(r"[\s\-]", "", kg_phone)
        else:
            found = extract_phone(all_text)
            if found:
                lead["phone"] = found

    # Website — fill only if missing
    if not lead.get("website"):
        kg_website = knowledge_graph.get("website", "")
        if kg_website:
            lead["website"] = kg_website
        else:
            site = best_website(organic, lead.get("name", ""))
            if site:
                lead["website"] = site

    # Snippet — enrich with knowledge graph description if available
    if not lead.get("snippet") and knowledge_graph.get("description"):
        lead["snippet"] = knowledge_graph["description"]

    # Maps URL — knowledge graph often has a direct maps link
    if not lead.get("maps_url") and knowledge_graph.get("maps"):
        lead["maps_url"] = knowledge_graph["maps"]

    # Cuisine type hint from knowledge graph type
    if not lead.get("cuisine_hint") and knowledge_graph.get("type"):
        lead["cuisine_hint"] = knowledge_graph["type"]

    return lead


def main():
    parser = argparse.ArgumentParser(description="Enrich leads with targeted Serper searches")
    parser.add_argument("--limit", type=int, default=None,
                        help="Only enrich first N leads (for testing)")
    parser.add_argument("--delay", type=float, default=0.4,
                        help="Seconds between Serper calls (default: 0.4)")
    parser.add_argument("--skip-existing", action="store_true",
                        help="Skip leads that already have both phone and website")
    args = parser.parse_args()

    if not IN_OUT_PATH.exists():
        print(f"ERROR: {IN_OUT_PATH} not found. Run deduplicate_leads.py first.", file=sys.stderr)
        sys.exit(1)

    api_key = check_env()
    leads = json.loads(IN_OUT_PATH.read_text(encoding="utf-8"))

    to_enrich = leads if not args.limit else leads[: args.limit]

    if args.skip_existing:
        needs_enrich = [l for l in to_enrich if not (l.get("phone") and l.get("website"))]
        skip_count = len(to_enrich) - len(needs_enrich)
        print(f"Skipping {skip_count} leads already have phone + website")
        to_enrich = needs_enrich

    print(f"Enriching {len(to_enrich)} leads (~{len(to_enrich)} Serper credits)")

    errors = 0
    for i, lead in enumerate(to_enrich):
        name = lead.get("name", f"Lead {i+1}")
        query = build_query(lead)
        try:
            data = serper_search(api_key, query)
            enrich_lead(lead, data)
            print(f"  [{i+1}/{len(to_enrich)}] {name} — ✓")
        except requests.HTTPError as e:
            print(f"  [{i+1}/{len(to_enrich)}] {name} — ERROR: {e}", file=sys.stderr)
            errors += 1

        time.sleep(args.delay)

    # Write enriched leads back to merged_leads.json
    IN_OUT_PATH.write_text(json.dumps(leads, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\nDone. {len(leads)} leads written back to {IN_OUT_PATH}")
    if errors:
        print(f"  {errors} enrichment errors — those leads unchanged")


if __name__ == "__main__":
    main()
