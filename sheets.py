"""Google Sheets push helper.

Authenticates with a Google service-account JSON credentials file and
pushes (replaces) the contents of a single worksheet/tab.

Setup:
  1. Create a service account in Google Cloud, download its JSON key.
  2. Enable the Google Sheets API (and Drive API) for the project.
  3. Share the target spreadsheet with the service-account email.
  4. Set GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json
     (or pass --credentials to playhq_api.py).
"""

import os
from typing import Sequence

import gspread
from google.oauth2.service_account import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def get_client(credentials_path: str = "") -> gspread.Client:
    creds_path = credentials_path or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
    if not creds_path:
        raise RuntimeError(
            "No credentials. Set GOOGLE_APPLICATION_CREDENTIALS or pass --credentials."
        )
    if not os.path.exists(creds_path):
        raise FileNotFoundError(f"Credentials file not found: {creds_path}")
    creds = Credentials.from_service_account_file(creds_path, scopes=SCOPES)
    return gspread.authorize(creds)


def push_rows(
    spreadsheet_id: str,
    worksheet_name: str,
    headers: Sequence[str],
    rows: Sequence[Sequence],
    credentials_path: str = "",
) -> None:
    """Replace the worksheet's contents with headers + rows. Creates the tab if missing."""
    client = get_client(credentials_path)
    sh = client.open_by_key(spreadsheet_id)

    try:
        ws = sh.worksheet(worksheet_name)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(
            title=worksheet_name,
            rows=max(len(rows) + 10, 100),
            cols=max(len(headers), 10),
        )

    ws.clear()
    data = [list(headers)] + [list(r) for r in rows]
    ws.update(values=data, range_name="A1")
    print(f"  Wrote {len(rows)} rows to '{worksheet_name}' "
          f"(spreadsheet {spreadsheet_id}).")
