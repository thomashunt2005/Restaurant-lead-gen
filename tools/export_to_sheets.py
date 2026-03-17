"""
Tool: export_to_sheets.py
WAT Layer: Tools (deterministic execution)

Pushes final leads + emails into a Google Sheet.
Auth: Service Account via service_account.json (no browser, fully headless).

Formatting applied automatically:
  - Frozen header row
  - Filter dropdowns on all columns
  - Column widths sized to content
  - Text wrap + expanded row height on Email Body column
  - Score column colour-coded (green → yellow by score)
  - Alternating row shading for readability

Usage:
    python tools/export_to_sheets.py
    python tools/export_to_sheets.py --sheet-id SHEET_ID --tab "Leads"

Required .env keys: GOOGLE_SHEET_ID
Required file: service_account.json (see setup steps below)

--- Service Account Setup (one-time) ---
1. Go to console.cloud.google.com
2. Create or select a project
3. Enable the Google Sheets API (APIs & Services → Library → search "Sheets")
4. Go to APIs & Services → Credentials → Create Credentials → Service Account
5. Give it any name, click Create
6. On the Keys tab → Add Key → JSON → download the file
7. Rename it service_account.json and place it in this project root
8. Open your Google Sheet → Share → paste the service account email
   (looks like name@project.iam.gserviceaccount.com) → Editor access
9. Run this script — no browser required

Reads:  .tmp/leads_with_emails.json
Writes: Google Sheet at GOOGLE_SHEET_ID, tab "Leads"
"""

import argparse
import json
import os
import sys
from pathlib import Path

import gspread
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials

load_dotenv()

IN_PATH = Path(".tmp/leads_with_emails.json")
SERVICE_ACCOUNT_PATH = Path("service_account.json")

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# (field_key, header_label, column_width_px)
COLUMNS = [
    ("name",                 "Restaurant Name",      200),
    ("area",                 "Area",                 120),
    ("phone",                "Phone",                130),
    ("website",              "Website",              200),
    ("maps_url",             "Google Maps URL",      200),
    ("cuisine_type",         "Cuisine Type",         120),
    ("seating_estimate",     "Seating Estimate",      90),
    ("qualification_score",  "Qualification Score",   80),
    ("qualification_reason", "Qualification Reason", 260),
    ("email_subject",        "Email Subject",        260),
    ("email_body",           "Email Body",           480),
]

# Score → background colour (hex, no #)
SCORE_COLOURS = {
    6: "c6efce",  # green
    5: "e2efda",  # light green
    4: "ffeb9c",  # yellow
    3: "fce4d6",  # light orange
}
SCORE_COL_INDEX = next(i for i, c in enumerate(COLUMNS) if c[0] == "qualification_score")
EMAIL_BODY_COL_INDEX = next(i for i, c in enumerate(COLUMNS) if c[0] == "email_body")


def check_env(sheet_id_arg: str | None) -> str:
    sheet_id = sheet_id_arg or os.getenv("GOOGLE_SHEET_ID")
    if not sheet_id:
        print("ERROR: GOOGLE_SHEET_ID is not set in .env or --sheet-id", file=sys.stderr)
        sys.exit(1)
    return sheet_id


def get_client() -> gspread.Client:
    if not SERVICE_ACCOUNT_PATH.exists():
        print(
            "ERROR: service_account.json not found.\n"
            "  Follow the setup steps in this file's docstring to create one.",
            file=sys.stderr,
        )
        sys.exit(1)
    creds = Credentials.from_service_account_file(str(SERVICE_ACCOUNT_PATH), scopes=SCOPES)
    return gspread.authorize(creds)


def ensure_tab(spreadsheet: gspread.Spreadsheet, tab_name: str) -> gspread.Worksheet:
    try:
        return spreadsheet.worksheet(tab_name)
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=tab_name, rows=1000, cols=len(COLUMNS))
        print(f"Created tab: {tab_name}")
        return ws


def hex_to_rgb(hex_str: str) -> dict:
    """Convert a 6-char hex string to a Sheets API RGB dict (0.0–1.0 floats)."""
    r = int(hex_str[0:2], 16) / 255
    g = int(hex_str[2:4], 16) / 255
    b = int(hex_str[4:6], 16) / 255
    return {"red": r, "green": g, "blue": b}


def apply_formatting(spreadsheet: gspread.Spreadsheet, ws: gspread.Worksheet, leads: list[dict]):
    """Apply all formatting in a single batchUpdate call for speed."""
    sheet_id = ws.id
    n_rows = len(leads) + 1  # +1 for header

    requests = []

    # 1. Freeze header row
    requests.append({
        "updateSheetProperties": {
            "properties": {
                "sheetId": sheet_id,
                "gridProperties": {"frozenRowCount": 1},
            },
            "fields": "gridProperties.frozenRowCount",
        }
    })

    # 2. Column widths
    for col_idx, (_, _, width) in enumerate(COLUMNS):
        requests.append({
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": col_idx,
                    "endIndex": col_idx + 1,
                },
                "properties": {"pixelSize": width},
                "fields": "pixelSize",
            }
        })

    # 3. Header styling: bold, dark background, white text
    requests.append({
        "repeatCell": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": 0,
                "endRowIndex": 1,
            },
            "cell": {
                "userEnteredFormat": {
                    "backgroundColor": hex_to_rgb("2d4059"),
                    "textFormat": {
                        "bold": True,
                        "foregroundColor": {"red": 1, "green": 1, "blue": 1},
                    },
                    "verticalAlignment": "MIDDLE",
                }
            },
            "fields": "userEnteredFormat(backgroundColor,textFormat,verticalAlignment)",
        }
    })

    # 4. Alternating row shading on data rows
    white = {"red": 1.0, "green": 1.0, "blue": 1.0}
    stripe = hex_to_rgb("f5f7fa")
    for row_idx in range(1, n_rows):
        bg = stripe if row_idx % 2 == 0 else white
        requests.append({
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": row_idx,
                    "endRowIndex": row_idx + 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": len(COLUMNS),
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": bg,
                        "verticalAlignment": "TOP",
                    }
                },
                "fields": "userEnteredFormat(backgroundColor,verticalAlignment)",
            }
        })

    # 5. Score column colour-coding (overrides alternating colour on that cell)
    for row_idx, lead in enumerate(leads, start=1):
        score = lead.get("qualification_score", 0)
        hex_colour = SCORE_COLOURS.get(score)
        if hex_colour:
            requests.append({
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": row_idx,
                        "endRowIndex": row_idx + 1,
                        "startColumnIndex": SCORE_COL_INDEX,
                        "endColumnIndex": SCORE_COL_INDEX + 1,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": hex_to_rgb(hex_colour),
                            "horizontalAlignment": "CENTER",
                            "textFormat": {"bold": True},
                        }
                    },
                    "fields": "userEnteredFormat(backgroundColor,horizontalAlignment,textFormat)",
                }
            })

    # 6. Email body column: wrap text, row height 120px
    requests.append({
        "repeatCell": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": 1,
                "endRowIndex": n_rows,
                "startColumnIndex": EMAIL_BODY_COL_INDEX,
                "endColumnIndex": EMAIL_BODY_COL_INDEX + 1,
            },
            "cell": {
                "userEnteredFormat": {
                    "wrapStrategy": "WRAP",
                    "verticalAlignment": "TOP",
                }
            },
            "fields": "userEnteredFormat(wrapStrategy,verticalAlignment)",
        }
    })
    requests.append({
        "updateDimensionProperties": {
            "range": {
                "sheetId": sheet_id,
                "dimension": "ROWS",
                "startIndex": 1,
                "endIndex": n_rows,
            },
            "properties": {"pixelSize": 120},
            "fields": "pixelSize",
        }
    })

    # 7. Add filter to header row
    requests.append({
        "setBasicFilter": {
            "filter": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 0,
                    "endRowIndex": n_rows,
                    "startColumnIndex": 0,
                    "endColumnIndex": len(COLUMNS),
                }
            }
        }
    })

    spreadsheet.batch_update({"requests": requests})
    print("Formatting applied.")


def main():
    parser = argparse.ArgumentParser(description="Export leads to Google Sheets")
    parser.add_argument("--sheet-id", default=None, help="Google Sheet ID (overrides .env)")
    parser.add_argument("--tab", default="Leads", help="Sheet tab name (default: Leads)")
    args = parser.parse_args()

    if not IN_PATH.exists():
        print(f"ERROR: {IN_PATH} not found. Run generate_outreach.py first.", file=sys.stderr)
        sys.exit(1)

    leads = json.loads(IN_PATH.read_text(encoding="utf-8"))
    sheet_id = check_env(args.sheet_id)

    print(f"Exporting {len(leads)} leads -> Sheet: {sheet_id}, Tab: {args.tab}")

    client = get_client()
    spreadsheet = client.open_by_key(sheet_id)
    ws = ensure_tab(spreadsheet, args.tab)

    # Build rows
    headers = [col[1] for col in COLUMNS]
    rows = [headers]
    for lead in leads:
        row = [str(lead.get(col[0], "") or "") for col in COLUMNS]
        rows.append(row)

    # Write data
    ws.clear()
    ws.update(rows, "A1")
    print(f"Data written: {len(leads)} leads ({len(rows)} rows).")

    # Format
    apply_formatting(spreadsheet, ws, leads)

    sheet_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}"
    print(f"Done. Open sheet: {sheet_url}")


if __name__ == "__main__":
    main()
