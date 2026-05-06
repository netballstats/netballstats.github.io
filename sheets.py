"""Google Sheets push helper.

Authenticates via OAuth (reads from ~/.config/gspread/credentials.json).
First run opens a browser tab for Google account authorisation; subsequent
runs use the cached token at ~/.config/gspread/authorized_user.json.
"""

from typing import Sequence

import gspread


def get_client() -> gspread.Client:
    return gspread.oauth()


def push_rows(
    spreadsheet_id: str,
    worksheet_name: str,
    headers: Sequence[str],
    rows: Sequence[Sequence],
) -> None:
    """Replace the worksheet's contents with headers + rows. Creates the tab if missing."""
    client = get_client()
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
