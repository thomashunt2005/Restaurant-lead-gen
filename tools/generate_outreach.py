"""
Tool: generate_outreach.py
WAT Layer: Tools (deterministic execution)

⚠️  USES ANTHROPIC API CREDITS — confirm keys are set before running.

Reads scored leads and generates a personalised outreach email for each
using the Anthropic API (Claude). Writes leads + emails to
.tmp/leads_with_emails.json.

Usage:
    python tools/generate_outreach.py
    python tools/generate_outreach.py --dry-run
    python tools/generate_outreach.py --limit 5 --dry-run

Required .env keys: ANTHROPIC_API_KEY

Cost note (as of 2026):
  - claude-sonnet-4-20250514: $3/M input, $15/M output tokens
  - Each email ≈ 250 input + 220 output tokens → ~$0.004 per email
  - 50 emails ≈ $0.20 total

Reads:  .tmp/scored_leads.json
Writes: .tmp/leads_with_emails.json
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import anthropic
from dotenv import load_dotenv

load_dotenv()

IN_PATH = Path(".tmp/scored_leads.json")
OUT_PATH = Path(".tmp/leads_with_emails.json")

DEFAULT_MODEL = "claude-sonnet-4-20250514"

SYSTEM_PROMPT = """You are writing short cold outreach emails for a website chat assistant product targeting UK independent restaurants and pubs.

The product is a chat widget that sits on a restaurant's website. It collects booking requests and answers common questions (hours, parking, menu, dietary) 24/7 — so the restaurant team gets fewer phone interruptions. Staff still confirm all bookings manually. It is not a phone product and does not replace staff.

Goal: get a reply to book a short call. Not to explain the product in detail. Not to close a sale.

Rules:
- Address the restaurant by name
- One specific detail about them (location, cuisine, or something from their listing)
- Pain point: phone calls interrupting service, or missed enquiries outside opening hours
- Solution: a chat assistant on their website that captures booking requests and answers basic questions, so staff aren't interrupted as often
- Keep humans-in-control positioning — this supports staff, it doesn't replace them
- CTA: ask if they'd be open to a quick chat — no mention of trials, pricing, or offers
- Tone: casual, practical, human — like a person reaching out, not a marketing email
- Max 80 words for the email body
- Do NOT use the word "AI" more than once. Do not say "automates bookings" or imply bookings are confirmed automatically
- Write a short subject line (under 8 words) and the email body
- Return ONLY valid JSON with keys: "subject" and "body"
- No preamble, no markdown, just the JSON object"""


def build_user_prompt(lead: dict) -> str:
    name = lead.get("name", "the restaurant")
    area = lead.get("area", "")
    cuisine = lead.get("cuisine_type", "restaurant")
    website = lead.get("website", "")
    snippet = lead.get("snippet", "")
    rating = lead.get("rating", "")
    reviews = lead.get("user_ratings_total", "")

    details = []
    if cuisine and cuisine != "Restaurant":
        details.append(f"Cuisine: {cuisine}")
    if area:
        details.append(f"Area: {area}")
    if rating:
        details.append(f"Google rating: {rating} ({reviews} reviews)")
    if snippet:
        details.append(f"Listing snippet: {snippet[:200]}")
    if website:
        details.append(f"Website: {website}")

    detail_block = "\n".join(details) if details else "No additional details available."

    return f"""Write a cold outreach email for this restaurant:

Restaurant name: {name}
{detail_block}

Return JSON only: {{"subject": "...", "body": "..."}}"""


def check_env():
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        print("ERROR: ANTHROPIC_API_KEY is not set in .env", file=sys.stderr)
        sys.exit(1)
    return key


def estimate_cost(n_leads: int, model: str) -> str:
    # Rough token estimates per lead
    input_tokens = 250
    output_tokens = 220
    if "haiku" in model:
        cost = n_leads * (input_tokens * 0.25 + output_tokens * 1.25) / 1_000_000
    elif "sonnet" in model:
        cost = n_leads * (input_tokens * 3.0 + output_tokens * 15.0) / 1_000_000
    else:
        cost = n_leads * (input_tokens * 15.0 + output_tokens * 75.0) / 1_000_000
    return f"~${cost:.4f}"


def generate_email(client: anthropic.Anthropic, lead: dict, model: str) -> dict:
    """Generate email for a single lead. Returns {"subject": ..., "body": ...}."""
    user_prompt = build_user_prompt(lead)

    response = client.messages.create(
        model=model,
        max_tokens=400,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    raw = response.content[0].text.strip()

    # Strip markdown code fences if model wraps in them
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    try:
        parsed = json.loads(raw)
        return {
            "email_subject": parsed.get("subject", ""),
            "email_body": parsed.get("body", ""),
        }
    except json.JSONDecodeError:
        # Fallback: store raw text so we don't lose the work
        return {
            "email_subject": "",
            "email_body": raw,
            "_email_parse_error": True,
        }


def main():
    parser = argparse.ArgumentParser(description="Generate personalised outreach emails")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help=f"Claude model to use (default: {DEFAULT_MODEL})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print prompts without calling the API")
    parser.add_argument("--limit", type=int, default=None,
                        help="Only process first N leads (for testing)")
    parser.add_argument("--delay", type=float, default=0.5,
                        help="Seconds between API calls (default: 0.5)")
    args = parser.parse_args()

    if not IN_PATH.exists():
        print(f"ERROR: {IN_PATH} not found. Run score_leads.py first.", file=sys.stderr)
        sys.exit(1)

    leads = json.loads(IN_PATH.read_text(encoding="utf-8"))

    if args.limit:
        leads = leads[: args.limit]

    print(f"Generating emails for {len(leads)} leads")
    print(f"Model: {args.model}")
    print(f"Estimated cost: {estimate_cost(len(leads), args.model)}")

    if args.dry_run:
        print("\n--- DRY RUN — sample prompt for first lead ---")
        if leads:
            print(build_user_prompt(leads[0]))
        print("\nNo API calls made. Remove --dry-run to generate emails.")
        return

    check_env()
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    results = []
    errors = 0

    for i, lead in enumerate(leads):
        name = lead.get("name", f"Lead {i+1}")
        try:
            email = generate_email(client, lead, args.model)
            lead.update(email)
            if email.get("_email_parse_error"):
                errors += 1
                print(f"  [{i+1}/{len(leads)}] {name} - parse error, raw text saved")
            else:
                print(f"  [{i+1}/{len(leads)}] {name} - ok")
        except Exception as e:
            print(f"  [{i+1}/{len(leads)}] {name} — ERROR: {e}", file=sys.stderr)
            lead["email_subject"] = ""
            lead["email_body"] = ""
            lead["_email_error"] = str(e)
            errors += 1

        results.append(lead)
        time.sleep(args.delay)

    OUT_PATH.parent.mkdir(exist_ok=True)
    OUT_PATH.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\nDone. {len(results)} leads written to {OUT_PATH}")
    if errors:
        print(f"  WARNING: {errors} leads had generation errors - review _email_error fields")


if __name__ == "__main__":
    main()
