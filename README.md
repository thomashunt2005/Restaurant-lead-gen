# Restaurant Lead Generation & AI Outreach Pipeline

An automated lead generation system that finds independent UK 
restaurants, scores them as prospects, and generates personalised 
outreach emails using the Anthropic Claude API — all piped into 
a Google Sheet for easy management.

Built as a companion tool for an AI restaurant booking assistant 
currently in active sales to UK clients.

## What it does

1. **Scrapes** — finds independent restaurants via Serper API
2. **Cleans & deduplicates** — removes chains, duplicates, 
   incomplete entries
3. **Enriches** — pulls additional business details
4. **Scores** — ranks leads by likelihood to convert
5. **Generates outreach** — writes personalised cold emails 
   for each lead using Claude API
6. **Exports** — pushes everything to a Google Sheet via 
   Service Account

## Tech stack

- Python
- Anthropic Claude API — email generation
- Serper API — restaurant discovery
- Google Sheets API — output and management
- Service Account authentication

## Setup

1. Clone the repo
2. Install dependencies:
```
   pip install -r requirements.txt
```
3. Create a `.env` file in the root with your API keys:
```
   ANTHROPIC_API_KEY=your-key-here
   SERPER_API_KEY=your-key-here
```
4. Add your Google Service Account JSON file to the root 
   and update the path in `export_to_sheets.py`
5. Run the pipeline:
```
   python tools/scrape_serper.py
   python tools/clean_leads.py
   python tools/deduplicate_leads.py
   python tools/enrich_leads.py
   python tools/score_leads.py
   python tools/generate_outreach.py
   python tools/export_to_sheets.py
```

## Results

Generated 63 qualified leads with personalised outreach emails 
on first run.

## Author

Thomas Hunt — self-taught developer and AI builder based in 
Winchester, UK.  
[LinkedIn](https://linkedin.com/in/thomas-hunt-42a90b30b) · 
[GitHub](https://github.com/thomashunt2005)
