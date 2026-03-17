"""
Tool: score_leads.py
WAT Layer: Tools (deterministic execution)

Reads merged leads and scores each against qualification criteria.
Leads scoring < 3 are filtered out. Results sorted by score descending.
Writes qualified leads to .tmp/scored_leads.json.

Usage:
    python tools/score_leads.py
    python tools/score_leads.py --min-score 4

Reads:  .tmp/merged_leads.json
Writes: .tmp/scored_leads.json
"""

import argparse
import json
import re
import sys
from pathlib import Path


IN_PATH = Path(".tmp/merged_leads.json")
OUT_PATH = Path(".tmp/scored_leads.json")

# -----------------------------------------------------------------------
# Blocklists — add franchise names here as you encounter them
# -----------------------------------------------------------------------
FRANCHISE_BLOCKLIST = {
    # Fast food
    "mcdonald's", "mcdonalds", "burger king", "kfc", "nando's", "nandos",
    "subway", "five guys", "papa john", "papajohn", "little caesars",
    "domino's", "dominoes", "pizza hut",
    # Coffee chains
    "costa coffee", "starbucks", "caffe nero", "caffè concerto", "pret a manger", "pret",
    # Casual dining chains
    "pizza express", "wagamama", "leon", "honest burgers",
    "bill's", "bills", "côte", "cote brasserie", "prezzo", "bella italia",
    "zizzi", "ask italian", "chiquito", "frankie & benny", "frankie and benny",
    "harvester", "toby carvery", "beefeater", "brewers fayre",
    "ego mediterranean",
    # Bakery / fast casual
    "greggs", "itsu", "wasabi", "yo sushi", "eat",
    # Pub chains
    "wetherspoons", "j d wetherspoon", "weather spoons", "pub & kitchen",
    "greene king", "mitchells & butlers", "village hotel", "village hotels",
    # Established restaurant groups (not small independents)
    "rick stein", "gordon ramsay", "heston blumenthal", "marco pierre white",
    "hartnett holder",  # Lime Wood Hotel — high-end, well-staffed
}

# Signals that suggest enterprise booking platforms are already in use
ENTERPRISE_BOOKING_SIGNALS = [
    "sevenrooms", "resy", "opentable pro", "tock", "eatapp",
]

# Google Places types that suggest it's NOT a sit-down restaurant
NON_RESTAURANT_TYPES = {
    "convenience_store", "grocery_or_supermarket", "supermarket",
    "food", "store", "shopping_mall", "bakery", "cafe",
    "night_club", "bar",
}

UK_PHONE_PATTERN = re.compile(r"^(\+44|0)(1|2|7)\d{8,9}$")
POSTCODE_PATTERN = re.compile(r"[A-Z]{1,2}\d{1,2}[A-Z]?\s*\d[A-Z]{2}", re.IGNORECASE)


# -----------------------------------------------------------------------
# Scoring functions — each returns (points: int, reason: str)
# -----------------------------------------------------------------------

def score_independent(record: dict) -> tuple[int, str]:
    """Not a franchise / large chain."""
    name = record.get("name", "").lower()
    for franchise in FRANCHISE_BLOCKLIST:
        if franchise in name:
            return -99, f"Franchise detected: {record['name']}"
    # Extra heuristic: very high review count suggests a chain
    reviews = record.get("user_ratings_total") or 0
    if reviews > 2000:
        return 0, "High review count suggests chain"
    return 1, "Appears independent"


def score_takes_reservations(record: dict) -> tuple[int, str]:
    """Phone number present OR reservation keywords in snippet/website."""
    phone = record.get("phone", "")
    snippet = (record.get("snippet", "") + " " + record.get("website", "")).lower()

    has_phone = bool(phone and UK_PHONE_PATTERN.match(re.sub(r"[\s\-()]", "", phone)))
    has_booking_signal = any(
        kw in snippet for kw in ("reserv", "book", "booking", "table", "opentable", "resy")
    )

    if has_phone and has_booking_signal:
        return 1, "Phone number + booking signals"
    if has_phone:
        return 1, "Phone number listed"
    if has_booking_signal:
        return 1, "Booking signals in listing"
    return 0, "No reservation signals found"


def score_likely_understaffed(record: dict) -> tuple[int, str]:
    """Single location, moderate review count — signals owner-operated."""
    reviews = record.get("user_ratings_total") or 0
    # 20–500 reviews: active but small
    if 20 <= reviews <= 500:
        return 1, f"Review count ({reviews}) suggests small independent"
    if reviews < 20:
        return 1, "Very low review count — likely new or small"
    return 0, f"High review count ({reviews}) — may have larger team"


def score_uk_based(record: dict) -> tuple[int, str]:
    """Address contains UK postcode or phone is UK format."""
    address = record.get("formatted_address", "")
    phone = re.sub(r"[\s\-()]", "", record.get("phone", ""))

    has_postcode = bool(POSTCODE_PATTERN.search(address))
    has_uk_phone = bool(phone and UK_PHONE_PATTERN.match(phone))

    if has_postcode or has_uk_phone:
        return 1, "UK address/phone confirmed"
    if "uk" in address.lower() or "united kingdom" in address.lower():
        return 1, "UK address confirmed"
    return 0, "Could not confirm UK location"


def score_online_presence(record: dict) -> tuple[int, str]:
    """Has a website or Google listing with reviews."""
    has_website = bool(record.get("website"))
    reviews = record.get("user_ratings_total") or 0
    rating = record.get("rating") or 0

    if has_website and reviews > 0:
        return 1, f"Website + {reviews} Google reviews (rating: {rating})"
    if has_website:
        return 1, "Has website"
    if reviews > 0:
        return 1, f"{reviews} Google reviews"
    return 0, "No website or Google reviews found"


def score_not_enterprise_booking(record: dict) -> tuple[int, str]:
    """No signals of SevenRooms / Resy / OpenTable Pro."""
    combined = (
        record.get("website", "") + " " +
        record.get("snippet", "") + " " +
        record.get("name", "")
    ).lower()

    for signal in ENTERPRISE_BOOKING_SIGNALS:
        if signal in combined:
            return 0, f"Enterprise booking platform detected: {signal}"
    return 1, "No enterprise booking platform detected"


SCORERS = [
    score_independent,
    score_takes_reservations,
    score_likely_understaffed,
    score_uk_based,
    score_online_presence,
    score_not_enterprise_booking,
]


def score_lead(record: dict) -> dict:
    total = 0
    reasons = []
    disqualified = False

    for scorer in SCORERS:
        points, reason = scorer(record)
        if points == -99:
            disqualified = True
            reasons.append(f"DISQUALIFIED: {reason}")
            break
        total += points
        reasons.append(reason)

    record["qualification_score"] = 0 if disqualified else total
    record["qualification_reason"] = " | ".join(reasons)
    record["disqualified"] = disqualified
    return record


def infer_area(record: dict) -> str:
    """Extract town/city from address."""
    address = record.get("formatted_address", "")
    parts = [p.strip() for p in address.split(",")]
    # Typically: Street, Town, County, Postcode, Country
    # Return second-to-last non-postcode, non-country part
    candidates = [p for p in parts if not POSTCODE_PATTERN.search(p)
                  and p.lower() not in ("uk", "united kingdom", "england")]
    if len(candidates) >= 2:
        return candidates[-2]
    if candidates:
        return candidates[-1]
    return ""


def infer_cuisine(record: dict) -> str:
    """Best-effort cuisine from Google Places types or name."""
    types = record.get("types", [])
    type_map = {
        "japanese_restaurant": "Japanese",
        "chinese_restaurant": "Chinese",
        "italian_restaurant": "Italian",
        "indian_restaurant": "Indian",
        "thai_restaurant": "Thai",
        "french_restaurant": "French",
        "mediterranean_restaurant": "Mediterranean",
        "mexican_restaurant": "Mexican",
        "seafood_restaurant": "Seafood",
        "steak_house": "Steakhouse",
        "sushi_restaurant": "Japanese",
        "pizza_restaurant": "Pizza",
        "vegetarian_restaurant": "Vegetarian",
        "vegan_restaurant": "Vegan",
    }
    for t in types:
        if t in type_map:
            return type_map[t]
    return "Restaurant"


def main():
    parser = argparse.ArgumentParser(description="Score and filter restaurant leads")
    parser.add_argument("--min-score", type=int, default=3,
                        help="Minimum qualification score to keep a lead (default: 3)")
    parser.add_argument("--max-leads", type=int, default=None,
                        help="Cap output at N leads (takes highest scoring)")
    args = parser.parse_args()

    if not IN_PATH.exists():
        print(f"ERROR: {IN_PATH} not found. Run deduplicate_leads.py first.", file=sys.stderr)
        sys.exit(1)

    records = json.loads(IN_PATH.read_text(encoding="utf-8"))
    print(f"Scoring {len(records)} leads...")

    scored = [score_lead(r) for r in records]

    # Filter: remove disqualified and below min-score
    qualified = [r for r in scored if not r["disqualified"] and r["qualification_score"] >= args.min_score]
    filtered_count = len(scored) - len(qualified)

    # Sort by score descending
    qualified.sort(key=lambda r: r["qualification_score"], reverse=True)

    # Enrich with derived fields
    for r in qualified:
        r.setdefault("area", infer_area(r))
        r.setdefault("cuisine_type", infer_cuisine(r))
        r.setdefault("seating_estimate", "")  # Populated manually or via future enrichment

    if args.max_leads:
        qualified = qualified[: args.max_leads]

    OUT_PATH.parent.mkdir(exist_ok=True)
    OUT_PATH.write_text(json.dumps(qualified, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\nResults:")
    print(f"  Total input:    {len(records)}")
    print(f"  Disqualified:   {sum(1 for r in scored if r['disqualified'])}")
    print(f"  Below min score: {filtered_count - sum(1 for r in scored if r['disqualified'])}")
    print(f"  Qualified leads: {len(qualified)}")
    print(f"\nWritten to {OUT_PATH}")

    # Score distribution
    from collections import Counter
    dist = Counter(r["qualification_score"] for r in qualified)
    for score in sorted(dist, reverse=True):
        print(f"  Score {score}: {dist[score]} leads")


if __name__ == "__main__":
    main()
