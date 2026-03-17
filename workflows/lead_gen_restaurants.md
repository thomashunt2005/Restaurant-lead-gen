# Workflow: Lead Generation — Restaurant AI Booking Assistant

## Objective
Find UK restaurants that would benefit from an AI booking assistant, qualify them against defined criteria, and produce a Google Sheet containing scored leads with personalised outreach emails ready to send.

## Required Inputs
All values in `.env`. Verify these are populated before running anything:
- `SERPER_API_KEY` — Serper.dev API key (sole scraping source)
- `ANTHROPIC_API_KEY` — Anthropic API key (Claude, for email generation)
- `GOOGLE_SHEET_ID` — Target Google Sheet ID for final export
- `TARGET_REGION` — Location to search (default: `Hampshire, UK`)
- `LEADS_TARGET` — Number of qualified leads to aim for (default: `50`)

Auth file for Google Sheets (Service Account):
- `service_account.json` — Service account key file (see Step 6 setup below)
- Gitignored — never commit this file

## Qualification Criteria
A restaurant passes scoring if it meets most of these signals:

| Signal | What we're looking for |
|--------|----------------------|
| Independent / small chain | Not a large franchise (McDonald's, Nando's, Pizza Express, etc.) |
| Takes reservations | Phone number listed, or "bookings" / "reservations" mentioned on website/listing |
| Likely understaffed | No chain front-of-house team; owner-operated signals (single location, small review count) |
| UK-based | Address clearly in UK; phone starts with 01/02/07/+44 |
| Active online presence | Has a website, Google listing with reviews, or social media |
| Not on enterprise booking platform | No mention of SevenRooms, Resy, or OpenTable Pro in listing or website |

Scoring is additive (0–6). Leads scoring ≥ 3 are kept. Score and reasoning written to output.

## Tool Execution Order

```
Step 1 → tools/scrape_serper.py       → .tmp/serper_raw.json
Step 2 → tools/deduplicate_leads.py   → .tmp/merged_leads.json
Step 3 → tools/enrich_leads.py        → .tmp/merged_leads.json  (enriched in-place, ⚠️ uses Serper credits)
Step 4 → tools/score_leads.py         → .tmp/scored_leads.json
Step 5 → tools/clean_leads.py         → .tmp/scored_leads.json  (cleaned in-place, no API calls)
Step 6 → tools/generate_outreach.py   → .tmp/leads_with_emails.json  ⚠️ uses Anthropic credits
Step 7 → tools/export_to_sheets.py    → Google Sheet (final deliverable)
```

## Step-by-Step Instructions

### Step 1 — Scrape Serper
```bash
python tools/scrape_serper.py
```
- Runs search queries via Serper API targeting `TARGET_REGION`
- Automatically calculates how many queries to run: collects ~3× `LEADS_TARGET` raw results (so scoring/filtering can still hit the target)
- Query bank has 25 templates covering diverse cuisines, intents, and keywords — stops early if raw target is reached
- Captures: name, address snippet, phone (if in snippet), website, maps URL, search snippet
- Writes raw results to `.tmp/serper_raw.json`
- **Rate limit**: Serper free tier = 2,500 searches/month. For LEADS_TARGET=50, uses ~15 credits per run.

### Step 2 — Deduplicate
```bash
python tools/deduplicate_leads.py
```
- Reads `.tmp/serper_raw.json`
- Multiple queries often return the same restaurant — deduplicates by normalised name + postcode
- If a name appears more than once, keeps the richest record (fills gaps from later duplicates)
- Writes `.tmp/merged_leads.json`
- Logs: total in, duplicates removed, unique out

### Step 3 — Enrich Leads ⚠️ Serper Credits
> For ~150 deduplicated leads, this step uses ~150 Serper credits. Run `--limit 5` to test first.

```bash
python tools/enrich_leads.py
python tools/enrich_leads.py --limit 10           # test on 10 leads
python tools/enrich_leads.py --skip-existing      # skip leads already have phone + website
```
- For each lead, runs a targeted Serper search: `"[name]" [area] restaurant`
- Extracts from results: phone number, restaurant website, description/snippet for personalisation
- Skips aggregator sites (TripAdvisor, OpenTable, Deliveroo, etc.) when picking website
- Writes enriched records back to `.tmp/merged_leads.json` in-place
- **Rate limit**: 1 Serper credit per lead. Includes 0.4s delay between calls.

### Step 4 — Score Leads
```bash
python tools/score_leads.py
python tools/score_leads.py --min-score 4   # stricter filter
```
- Reads `.tmp/merged_leads.json`
- Applies qualification criteria (see table above), 1 point each (max 6)
- Disqualifies franchise names immediately (score set to 0, removed)
- Leads scoring < 3 (or `--min-score`) are filtered out
- Adds `qualification_score`, `qualification_reason`, `area`, `cuisine_type` fields
- Writes `.tmp/scored_leads.json`, sorted by score descending

### Step 5 — Clean Leads
```bash
python tools/clean_leads.py
python tools/clean_leads.py --dry-run    # preview removals without writing
python tools/clean_leads.py --verbose    # show every removed record + reason
```
- Reads `.tmp/scored_leads.json` and writes back in-place — no API calls
- **Junk filter**: removes aggregator list pages, articles, YouTube results, hotel chains, etc. using regex patterns on the name
- **Name cleaner**: strips page-title artifacts (`: Home`, `Info and Reservations`, `Book Online at`, em-dash subtitles, trailing location tags)
- **Dedup**: removes records that become identical after name cleaning
- Logs counts for each removal type
- Run `--verbose` to audit every removal before spending Anthropic credits

### Step 6 — Generate Outreach Emails ⚠️ Anthropic Credits
> **STOP — confirm Anthropic API key is set and you're ready to spend credits before this step.**
> Use `--dry-run` to preview the prompt without making any API calls.

```bash
python tools/generate_outreach.py --dry-run     # preview prompt, no API calls
python tools/generate_outreach.py --limit 3     # test on 3 leads
python tools/generate_outreach.py               # full run
```
- Reads `.tmp/scored_leads.json` (post-clean)
- Reads `.tmp/scored_leads.json`
- Sends each lead to Claude (claude-sonnet-4-20250514) with restaurant details
- Email rules enforced in prompt:
  - Open with restaurant name + specific detail
  - Pain point: missed bookings out of hours, staff on phones during service
  - Solution: AI receptionist, 24/7, hands off to staff when needed
  - CTA: free 2-week trial, no setup fee
  - Tone: warm, direct, human — not mass marketing
  - Max 150 words
- Adds `email_subject` and `email_body` to each lead record
- Writes `.tmp/leads_with_emails.json`
- **Cost**: ~$0.004 per email (claude-sonnet-4-20250514). 50 emails ≈ $0.20.

### Step 7 — Export to Google Sheet
```bash
python tools/export_to_sheets.py
```
- Reads `.tmp/leads_with_emails.json`
- Authenticates via Service Account (`service_account.json`) — fully headless, no browser
- Clears and rewrites the target sheet (tab: "Leads")
- Columns: `Restaurant Name | Area | Phone | Website | Google Maps URL | Cuisine Type | Seating Estimate | Qualification Score | Qualification Reason | Email Subject | Email Body`
- Bolds header row

## Google Sheets Service Account Setup (one-time)
1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create or select a project
3. **Enable Google Sheets API**: APIs & Services → Library → search "Google Sheets API" → Enable
4. **Create Service Account**: APIs & Services → Credentials → Create Credentials → Service Account
   - Give it any name (e.g. `lead-gen`) → Create and Continue → Done
5. **Download key**: Click the service account → Keys tab → Add Key → Create new key → JSON
   - Save the downloaded file as `service_account.json` in this project root
6. **Share the Sheet**: Open your Google Sheet → Share → paste the service account email
   (format: `name@project-id.iam.gserviceaccount.com`) → set to **Editor** → Share
7. Run `python tools/export_to_sheets.py` — no browser required

## Edge Cases & Known Quirks

- **Serper snippets are thin**: Many Serper results only include a short snippet, not a structured address. `enrich_leads.py` is the main source of phone/website. Run it before scoring.
- **Franchise slips through scoring**: Add the chain name to `FRANCHISE_BLOCKLIST` in `score_leads.py` and re-run from Step 4.
- **Low qualified lead count**: Lower `--min-score` to 2, or expand `TARGET_REGION` (e.g. add "Surrey, UK") and re-run from Step 1.
- **Serper credit budget**: For LEADS_TARGET=50 with enrichment: ~15 (scrape) + ~150 (enrich) = ~165 credits per full run. Check your Serper dashboard before running.
- **Service account 403 error**: The Sheet hasn't been shared with the service account email. Repeat Step 6 of setup above.
- **Email JSON parse error**: Claude occasionally wraps JSON in markdown fences. The script strips these automatically. If `_email_parse_error` appears in output, check the raw `email_body` field — the text is usually valid, just malformatted.

## Output
Final deliverable: Google Sheet at `GOOGLE_SHEET_ID`, tab "Leads"

Intermediate files (all in `.tmp/`, all disposable):
- `serper_raw.json`
- `merged_leads.json`  ← also written by enrich_leads.py
- `scored_leads.json`
- `leads_with_emails.json`

## Self-Improvement Notes
> Update this section when you discover new constraints, better search terms, or improved scoring logic.

### Run 1 — Hampshire, UK (March 2026)

**Serper returns page titles, not restaurant names.**
The `name` field in scrape_serper.py extracts from the web page `<title>` tag. This means aggregator sites (TripAdvisor top-10 lists, OpenTable, YouTube videos) slip through with names like "THE 10 BEST Restaurants in Hampshire" or "Gordon Tries To Save The Biggest SH*THOLE In Hampshire". After scraping 159 raw results and deduplicating to 142, roughly 37 were non-restaurant pages requiring manual filtering.

**Mitigation applied:** Added a post-score regex filter and name cleaner (inline Python) to remove obvious junk before running `generate_outreach.py`. This is a candidate for a dedicated `tools/clean_leads.py` tool on the next build cycle.

**Franchise blocklist gap:** Frankie & Benny's and Rick Stein (established chain) slipped through scoring. Both added to the blocklist in `score_leads.py` on the next iteration. Check and expand `FRANCHISE_BLOCKLIST` before each run.

**Windows encoding:** Python on Windows defaults to cp1252 for stdout and file writes. All tools now explicitly pass `encoding="utf-8"` to `write_text()`. Run all scripts with `PYTHONIOENCODING=utf-8` prefix to avoid terminal print errors (Unicode arrows/dashes in print statements).

**Name artifacts:** Some records have page-title noise like ": Home", "Info and Reservations", "— Italian Restaurant" appended. The name extraction in `scrape_serper.py` (`split(" - ")[0].split(" | ")[0]`) doesn't catch all variants. Consider stripping common suffixes in `normalise_result()`.

**Credit usage for 50-lead target (Hampshire):**
- Step 1 (scrape): ~21 Serper credits (stopped early at 20 queries)
- Step 3 (enrich): ~142 Serper credits (1 per lead)
- Total: ~163 Serper credits per full run

**Final qualified leads:** 63 clean leads after filtering (target was 50 — comfortably met).
