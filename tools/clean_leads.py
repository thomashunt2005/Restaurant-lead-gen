"""
Tool: clean_leads.py
WAT Layer: Tools (deterministic execution)

Runs between score_leads.py and generate_outreach.py.
No API calls — pure text processing.

Does three things:
  1. Junk filter  — removes aggregator pages, list articles, YouTube results, etc.
  2. Name cleaner — strips web page title artifacts (": Home", "Info and Reservations", etc.)
  3. Dedup        — removes records that become identical after name cleaning

Reads and writes .tmp/scored_leads.json in-place.

Usage:
    python tools/clean_leads.py
    python tools/clean_leads.py --dry-run     # show what would be removed without writing
    python tools/clean_leads.py --verbose     # print every removed record and reason

Reads:  .tmp/scored_leads.json
Writes: .tmp/scored_leads.json  (cleaned, in-place)
"""

import argparse
import json
import re
import sys
from pathlib import Path

IN_OUT_PATH = Path(".tmp/scored_leads.json")

# ---------------------------------------------------------------------------
# Junk filter — page titles that are clearly not a restaurant
# Region-agnostic patterns so this works for any TARGET_REGION.
# ---------------------------------------------------------------------------
JUNK_PATTERNS = [
    # List / roundup articles
    (r"^(the\s+)?\d+\s+best\b",                 "numbered list article"),
    (r"^top\s+\d+\b",                            "numbered list article"),
    (r"\d+\s+(best|top|great|good)\b",           "numbered list article"),
    (r"\bbest\s+\w+\s+restaurants\b",            "aggregator list"),
    (r"\bplaces\s+to\s+eat\b",                   "aggregator list"),
    (r"\bplaces\s+to\s+eat\s+and\s+drink\b",     "aggregator list"),
    (r"^restaurants\s+in\b",                     "aggregator list"),
    (r"^restaurants\s+&\s+places\b",             "aggregator list"),
    (r"^diners.{0,5}choice\b",                   "OpenTable badge page"),
    # Page-function titles (not restaurant names)
    (r"^bookings?$",                             "booking page (no name)"),
    (r"^order\b.*\breserve\b",                   "order/reserve CTA page"),
    (r"^reservation$",                           "generic page name"),
    (r"^book\s+a\s+table$",                      "generic page name"),
    # Aggregator / review platforms by URL pattern caught in name
    (r"\bnear\s+(me|[a-z]+,)",                   "location aggregator"),
    (r"\bupdated\s+\d{4}\b",                     "TripAdvisor list page"),
    # Media / editorial
    (r"\bramsay.s\s+kitchen\b",                  "TV show content"),
    (r"\bgordon\s+(tries|ramsay)\b",             "TV show content"),
    (r"\bwhat\s+really\s+happened\b",            "article/video"),
    (r"\bowner\s+catches\b",                     "article/video"),
    (r"\bforced\s+to\s+move\b",                  "news article"),
    (r"\bdiscovering\s+the\s+charm\b",           "blog article"),
    # Hotel chains / clearly non-independent
    (r"\bvillage\s+hotel(s)?\b",                 "chain hotel"),
    (r"\bfour\s+seasons\b",                      "chain hotel"),
    (r"\bmarriott\b",                            "chain hotel"),
    (r"\bhilton\b",                              "chain hotel"),
    (r"\bpremier\s+inn\b",                       "chain hotel"),
    (r"\bholiday\s+inn\b",                       "chain hotel"),
    # Private / group dining aggregators
    (r"private\s+&?\s*group\s+dining",           "group dining aggregator"),
    (r"\d+\s+private\b",                         "group dining aggregator"),
    # Generic descriptors with no proper name
    (r"^pub$",                                   "too generic"),
    (r"^bar$",                                   "too generic"),
    (r"^restaurant$",                            "too generic"),
    (r"^cafe$",                                  "too generic"),
    (r"^bistro$",                                "too generic"),
    (r"^british\s+restaurant\s+in\b",            "generic descriptor"),
    (r"^(fine\s+dining|coastal\s+fine.dining)\s+restaurant\s+in\b", "generic descriptor"),
    (r"^child\s+friendly\s+restaurants\b",       "aggregator list"),
    (r"^family.friendly\s+dining\b",             "aggregator list"),
    (r"^mediterranean\s+restaurants$",           "too generic"),
    (r"^freshly\s+cooked\b",                     "snippet captured as name"),
    (r"^literally\b",                            "snippet captured as name"),
    (r"^hi\.\s",                                 "forum post"),
    (r"^www\.",                                  "domain captured as name"),
    (r"^https?://",                              "URL captured as name"),
    # Single city/location with no restaurant name
    (r"^[a-z]+\s*,?\s*(hampshire|surrey|uk|england)$", "location only"),
]

COMPILED_JUNK = [(re.compile(p, re.IGNORECASE), reason) for p, reason in JUNK_PATTERNS]

# ---------------------------------------------------------------------------
# Name cleaner — strips page-title noise that isn't part of the restaurant name
# ---------------------------------------------------------------------------
# Prefixes to strip from the start of names
NAME_PREFIX_STRIP = re.compile(
    r"^(book\s+online\s+at|welcome\s+to\s+(the\s+)?|visit\s+|"
    r"authentic\s+(?=\w+\s+food))",
    re.IGNORECASE,
)

# Suffixes and patterns to strip from the end of names
NAME_SUFFIX_STRIP = re.compile(
    r"\s*[:\-–—|]\s*("
    r"home|menu|bookings?|reservations?|info\s+and\s+reservations?|"
    r"restaurant\s+info.*|pub\s+and\s+dining|pub\s+&\s+dining|"
    r"seasonal\s+dining.*|bistro$|"
    r"book\s+a\s+table|book\s+online.*|order\s+online.*|"
    r"family.run.*|bustling.*|"
    r"italian\s+restaurant$|indian\s+restaurant$|thai\s+restaurant$|"
    r"chinese\s+restaurant$|french\s+restaurant$|greek\s+restaurant$|"
    r"japanese\s+restaurant$|seafood\s+restaurant$|"
    r"gastropub.*|a\s+dog\s+friendly.*"
    r").*$",
    re.IGNORECASE,
)

# Strip trailing em-dash + text (often page subtitles)
EM_DASH_SUFFIX = re.compile(r"\s*[—–]\s*.+$")

# Strip trailing "Reservations" or "& Takeaway Reservations" at end
TRAILING_RESERVATIONS = re.compile(r"\s+Reservations?\s*$", re.IGNORECASE)

# Strip city/county suffix if it follows a comma (e.g. "The Robin Hood, Hampshire")
# Only strip if it looks like just a location tag, not part of the name
LOCATION_SUFFIX = re.compile(
    r",\s+(hampshire|surrey|kent|sussex|dorset|wiltshire|berkshire|"
    r"southampton|portsmouth|winchester|bournemouth|basingstoke|"
    r"farnborough|alton|alresford|romsey|ringwood|emsworth|lymington|"
    r"new\s+forest|united\s+kingdom|england|uk)\s*$",
    re.IGNORECASE,
)


def is_junk(name: str) -> tuple[bool, str]:
    for pattern, reason in COMPILED_JUNK:
        if pattern.search(name):
            return True, reason
    return False, ""


def clean_name(name: str) -> str:
    name = NAME_PREFIX_STRIP.sub("", name)
    name = NAME_SUFFIX_STRIP.sub("", name)
    name = EM_DASH_SUFFIX.sub("", name)
    name = TRAILING_RESERVATIONS.sub("", name)
    name = LOCATION_SUFFIX.sub("", name)
    return name.strip()


def normalise_for_dedup(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def main():
    parser = argparse.ArgumentParser(description="Filter and clean restaurant leads")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be removed without writing")
    parser.add_argument("--verbose", action="store_true",
                        help="Print every removed record with reason")
    args = parser.parse_args()

    if not IN_OUT_PATH.exists():
        print(f"ERROR: {IN_OUT_PATH} not found. Run score_leads.py first.", file=sys.stderr)
        sys.exit(1)

    leads = json.loads(IN_OUT_PATH.read_text(encoding="utf-8"))
    print(f"Input: {len(leads)} leads")

    removed_junk = 0
    removed_dedup = 0
    seen_names: set[str] = set()
    final: list[dict] = []

    for lead in leads:
        original_name = lead["name"]

        # Step 1: junk filter on original name
        junk, reason = is_junk(original_name)
        if junk:
            removed_junk += 1
            if args.verbose:
                print(f"  JUNK [{reason}]: {original_name[:70]}")
            continue

        # Step 2: clean the name
        cleaned = clean_name(original_name)
        if not cleaned:
            removed_junk += 1
            if args.verbose:
                print(f"  JUNK [empty after clean]: {original_name[:70]}")
            continue

        # Step 3: junk filter again on cleaned name (catches cases like "Book Online at X"
        # where stripping prefix leaves "X" which may still be junk)
        junk, reason = is_junk(cleaned)
        if junk:
            removed_junk += 1
            if args.verbose:
                print(f"  JUNK post-clean [{reason}]: {original_name[:70]}")
            continue

        lead["name"] = cleaned

        # Step 4: dedup by normalised cleaned name
        norm = normalise_for_dedup(cleaned)
        if norm in seen_names:
            removed_dedup += 1
            if args.verbose:
                print(f"  DEDUP: {cleaned[:70]}")
            continue
        seen_names.add(norm)

        final.append(lead)

    print(f"  Removed (junk/aggregator): {removed_junk}")
    print(f"  Removed (duplicate after clean): {removed_dedup}")
    print(f"Output: {len(final)} clean leads")

    if args.dry_run:
        print("\nDry run — nothing written.")
        return

    IN_OUT_PATH.write_text(json.dumps(final, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Written to {IN_OUT_PATH}")


if __name__ == "__main__":
    main()
