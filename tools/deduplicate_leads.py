"""
Tool: deduplicate_leads.py
WAT Layer: Tools (deterministic execution)

Deduplicates Serper results (multiple queries produce overlapping hits).
Normalises by name + postcode. Writes merged list to .tmp/merged_leads.json.

Usage:
    python tools/deduplicate_leads.py

Reads:  .tmp/serper_raw.json
Writes: .tmp/merged_leads.json
"""

import json
import re
import sys
from pathlib import Path


SERPER_PATH = Path(".tmp/serper_raw.json")
OUT_PATH = Path(".tmp/merged_leads.json")

# Words to strip when normalising restaurant names for dedup
NAME_NOISE = re.compile(
    r"\b(the|restaurant|cafe|café|bar|grill|kitchen|house|inn|pub|bistro|brasserie|eatery|dining)\b",
    re.IGNORECASE,
)
PUNCT = re.compile(r"[^\w\s]")
WHITESPACE = re.compile(r"\s+")

UK_POSTCODE = re.compile(
    r"\b([A-Z]{1,2}\d{1,2}[A-Z]?\s*\d[A-Z]{2})\b", re.IGNORECASE
)


def normalise_name(name: str) -> str:
    name = name.lower()
    name = PUNCT.sub(" ", name)
    name = NAME_NOISE.sub(" ", name)
    name = WHITESPACE.sub(" ", name).strip()
    return name


def extract_postcode(address: str) -> str:
    match = UK_POSTCODE.search(address)
    if match:
        return match.group(1).upper().replace(" ", "")
    return ""


def dedup_key(record: dict) -> str:
    name_key = normalise_name(record.get("name", ""))
    postcode = extract_postcode(record.get("formatted_address", ""))
    return f"{name_key}|{postcode}"


def merge_records(existing: dict, incoming: dict) -> dict:
    """Fill gaps in existing record with data from a later-seen duplicate."""
    merged = dict(existing)
    for field in ("phone", "website", "maps_url", "snippet"):
        if not merged.get(field) and incoming.get(field):
            merged[field] = incoming[field]
    return merged


def main():
    OUT_PATH.parent.mkdir(exist_ok=True)

    if not SERPER_PATH.exists():
        print(f"ERROR: {SERPER_PATH} not found. Run scrape_serper.py first.", file=sys.stderr)
        sys.exit(1)

    serper_records = json.loads(SERPER_PATH.read_text(encoding="utf-8"))
    print(f"Loaded {len(serper_records)} raw records from {SERPER_PATH}")

    merged: dict[str, dict] = {}
    for record in serper_records:
        key = dedup_key(record)
        if not key:
            continue
        if key in merged:
            merged[key] = merge_records(merged[key], record)
        else:
            merged[key] = record

    final = list(merged.values())
    duplicates_removed = len(serper_records) - len(final)

    print(f"Duplicates removed: {duplicates_removed}")
    OUT_PATH.write_text(json.dumps(final, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Done. {len(final)} unique records written to {OUT_PATH}")


if __name__ == "__main__":
    main()
