# PlayHQ → Google Sheets

Pull netball fixtures and scores from PlayHQ's GraphQL API into a Google Sheet.

See `DESIGN.md` for the data model and CLI surface. This README covers setup and the current end-to-end command for the **competitions list** step (one row per competition × season).

---

## Install

```
pip install -r requirements.txt
```

---

## Google Cloud setup (one-time)

You need a Google Cloud project, the Sheets + Drive APIs enabled, and a service-account key. The service account is what `playhq_api.py` authenticates as when writing to your sheet.

### 1. Create / select a Cloud project

In [Cloud Console](https://console.cloud.google.com/), pick a project from the top-left project picker.

> **Heads-up:** if you're signed in with a corporate Google Workspace account, any project you create lands under that organisation and inherits its org policies — including, often, `iam.disableServiceAccountKeyCreation`, which blocks step 3 below. To avoid this, sign in with a personal `@gmail.com` account and create the project there. In the project picker, the **Organisation** column should read **No organization**.

### 2. Enable Sheets API + Drive API

**APIs & Services → Library** → search "Google Sheets API" → **Enable**. Repeat for "Google Drive API".

(Drive API is used by `gspread` for spreadsheet metadata operations.)

### 3. Create a service account and download its JSON key

- **IAM & Admin → Service Accounts → Create service account**.
- Name: e.g. `playhq-sheets-writer`. Note the generated email — you'll need it in step 5.
- Skip the optional "Grant access" steps and click **Done**.
- Click into the new service account → **Keys** tab → **Add Key → Create new key → JSON → Create**.
- Move the downloaded JSON **outside this repo** (e.g. `~/.config/playhq/service-account.json`). The `.gitignore` covers common filenames as a safety net, but keep it out of the working tree to be sure.

**If "Create new key" is disabled:** your project is under a Workspace org with key creation blocked by policy. Either:
- Move the project to a personal `@gmail.com` account (recreate, the simpler route), or
- Use OAuth user credentials instead of a service account (ask the maintainer; small code change to `sheets.py` required).

### 4. Create the target Google Sheet

In Google Sheets, create a new spreadsheet. Copy its ID from the URL:

```
https://docs.google.com/spreadsheets/d/<SPREADSHEET_ID>/edit
```

### 5. Share the sheet with the service account

In the sheet, click **Share**, paste the service-account email from step 3, set role to **Editor**, uncheck "Notify people", and **Share**.

This step is the most commonly missed one. Without it, you'll get `SpreadsheetNotFound` even though the ID is correct.

---

## Usage

```
python3 playhq_api.py "<PLAYHQ_ORG_URL>" \
  --push-to-sheet <SPREADSHEET_ID> \
  --credentials /path/to/service-account.json
```

Or set the credentials path once and drop the flag:

```
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
python3 playhq_api.py "<PLAYHQ_ORG_URL>" --push-to-sheet <SPREADSHEET_ID>
```

The script writes to a tab named `Competitions` (override with `--worksheet <name>`). The tab is created if missing, then cleared and replaced. One row per (competition, season).

### Example

```
python3 playhq_api.py \
  "https://www.playhq.com/netball-australia/org/ku-ring-gai-netball-association/827654c0" \
  --push-to-sheet 1AbC...xyz \
  --credentials ~/.config/playhq/service-account.json
```

Without `--push-to-sheet` the script just prints the competitions/seasons to stdout — useful for sanity-checking.

---

## Common errors

| Error | Cause |
|---|---|
| `SpreadsheetNotFound` | Sheet not shared with the service account email (step 5), or wrong ID. |
| `403 ... has not been used in project` | Sheets/Drive API not enabled (step 2), or wrong Cloud project selected. |
| `gspread.exceptions.APIError: PERMISSION_DENIED` | Same as above — usually missed share. |
| `No credentials. Set GOOGLE_APPLICATION_CREDENTIALS or pass --credentials.` | Either set the env var or pass `--credentials path/to/key.json`. |
