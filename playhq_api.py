#!/usr/bin/env python3
"""
PlayHQ GraphQL API client -- replaces Playwright-based scraping.

Usage:
    # From a PlayHQ URL (auto-detects org/season/grade):
    python playhq_api.py <playhq_url>
    python playhq_api.py <playhq_url> --grade "11A"
    python playhq_api.py <playhq_url> --all-grades

    # Direct by ID:
    python playhq_api.py --org-id 414b289d
    python playhq_api.py --season-id deaa06cd
    python playhq_api.py --grade-id 15299ca4

    # Save to JSON:
    python playhq_api.py <url> --all-grades --output results.json
"""

import argparse
import json
import re
import sys
from dataclasses import dataclass, field, asdict
from typing import Optional
import requests

API_URL = "https://api.playhq.com/graphql"

# ---------------------------------------------------------------------------
# GraphQL queries (trimmed to only the fields we need)
# ---------------------------------------------------------------------------

Q_DISCOVER_COMPETITIONS = """
query discoverCompetitions($organisationID: ID!, $organisationCode: String!) {
  discoverCompetitions(organisationID: $organisationID) {
    id
    name
    seasons(organisationID: $organisationID) {
      id
      name
      startDate
      endDate
      status { name value __typename }
      __typename
    }
    __typename
  }
  discoverOrganisation(code: $organisationCode) {
    id
    name
    __typename
  }
}
"""

Q_GRADE_LIST = """
query gradeListDiscoverSeason($id: String!) {
  discoverSeason(seasonID: $id) {
    id
    name
    competition { id name type __typename }
    grades {
      id
      name
      gender { name value __typename }
      age { name value __typename }
      __typename
    }
    __typename
  }
}
"""

Q_GRADE_LADDER = """
query discoverGrade($gradeID: ID!) {
  discoverGrade(gradeID: $gradeID) {
    id
    name
    ladder {
      standings {
        team { id name __typename }
        played
        won
        lost
        drawn
        byes
        pointsFor
        pointsAgainst
        pointsDifference
        forfeits
        bonusPoints
        __typename
      }
      __typename
    }
    __typename
  }
}
"""

Q_GRADE_FIXTURES = """
query gradeAllRounds($gradeID: ID!) {
  discoverGradeFixture(gradeID: $gradeID) {
    id
    name
    isFinalsRound
    byes {
      id
      name
      __typename
    }
    games {
      id
      home {
        ... on DiscoverTeam { id name __typename }
        ... on ProvisionalTeam { name __typename }
        __typename
      }
      away {
        ... on DiscoverTeam { id name __typename }
        ... on ProvisionalTeam { name __typename }
        __typename
      }
      result {
        winner { name value __typename }
        outcome { name value __typename }
        home {
          outcome { name value __typename }
          statistics { count type { value __typename } __typename }
          __typename
        }
        away {
          outcome { name value __typename }
          statistics { count type { value __typename } __typename }
          __typename
        }
        __typename
      }
      status { name value __typename }
      date
      allocation {
        time
        court {
          name
          venue { name suburb __typename }
          __typename
        }
        __typename
      }
      __typename
    }
    __typename
  }
}
"""

# ---------------------------------------------------------------------------
# Data classes (same as scrape_fixtures.py for compatibility)
# ---------------------------------------------------------------------------

@dataclass
class Fixture:
    round_name: str = ""
    date: str = ""
    time: str = ""
    venue: str = ""
    court: str = ""
    home_team: str = ""
    away_team: str = ""
    home_score: Optional[int] = None
    away_score: Optional[int] = None
    status: str = ""


@dataclass
class Grade:
    name: str = ""
    url: str = ""
    fixtures: list = field(default_factory=list)


@dataclass
class Competition:
    url: str = ""
    name: str = ""
    organisation: str = ""
    season: str = ""
    grades: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def gql(query: str, variables: dict, endpoint: str = API_URL,
        tenant: str = "netball-australia") -> dict:
    """Execute a GraphQL query and return the data dict."""
    resp = requests.post(
        endpoint,
        json={"query": query, "variables": variables},
        headers={
            "Content-Type": "application/json",
            "tenant": tenant,
            "Origin": "https://www.playhq.com",
            "Referer": "https://www.playhq.com/",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        },
        timeout=30,
    )
    if resp.status_code != 200:
        print(f"HTTP {resp.status_code} from {endpoint}", file=sys.stderr)
        print(f"Response headers: {dict(resp.headers)}", file=sys.stderr)
        print(f"Response body: {resp.text[:500]}", file=sys.stderr)
        resp.raise_for_status()
    body = resp.json()
    if "errors" in body:
        print(f"GraphQL errors: {json.dumps(body['errors'], indent=2)}", file=sys.stderr)
    return body.get("data", {})


def get_score(team_result: dict) -> Optional[int]:
    """Extract TOTAL_SCORE from a game team result."""
    if not team_result:
        return None
    for stat in team_result.get("statistics", []):
        if stat.get("type", {}).get("value") == "TOTAL_SCORE":
            return stat["count"]
    return None


# ---------------------------------------------------------------------------
# URL parsing -- extract IDs from PlayHQ URLs
# ---------------------------------------------------------------------------

def parse_playhq_url(url: str) -> dict:
    """Parse a PlayHQ URL to extract what we can.

    URL patterns (hex = 8-char ID):
      /org/{org-slug}/{org-hex}                                org page
      /org/{org-slug}/{comp-slug}/{season-hex}                 season page
      /org/{org-slug}/{comp-slug}/{season-hex}/grades/{grade-hex}/fixture
    Falls back to fetching __NEXT_DATA__ for anything not directly in the path.
    """
    info = {"url": url, "grade_id": None, "season_id": None, "org_id": None}

    # Grade ID -- always after /grades/
    m = re.search(r"/grades/([0-9a-f]{8})", url)
    if m:
        info["grade_id"] = m.group(1)

    # Org ID -- /org/{slug}/{hex} with hex as the segment right after the slug
    m = re.search(r"/org/[^/]+/([0-9a-f]{8})(?:[/?#]|$)", url)
    if m:
        info["org_id"] = m.group(1)

    # Season ID -- /org/{slug}/{comp-slug}/{hex} (one level deeper than org)
    m = re.search(r"/org/[^/]+/[^/]+/([0-9a-f]{8})(?:[/?#]|$)", url)
    if m:
        info["season_id"] = m.group(1)

    # Try to get IDs from __NEXT_DATA__
    try:
        resp = requests.get(url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0"
        })
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', resp.text)
        if m:
            next_data = json.loads(m.group(1))
            props = next_data.get("props", {}).get("pageProps", {})

            # Walk the props to find IDs
            _extract_ids_from_props(props, info)

            # Also check query params
            query = next_data.get("query", {})
            if "seasonId" in query:
                info["season_id"] = query["seasonId"]
            if "gradeId" in query:
                info["grade_id"] = query["gradeId"]
            if "organisationCode" in query:
                info["org_id"] = query["organisationCode"]
    except Exception as e:
        print(f"Warning: could not fetch __NEXT_DATA__ from URL: {e}", file=sys.stderr)

    return info


def _extract_ids_from_props(obj, info: dict):
    """Recursively walk props to find org/season/grade IDs."""
    if isinstance(obj, dict):
        # Check for known key patterns
        if "discoverGrade" in obj and isinstance(obj["discoverGrade"], dict):
            grade = obj["discoverGrade"]
            if not info["grade_id"] and "id" in grade:
                info["grade_id"] = grade["id"]
            season = grade.get("season", {})
            if not info["season_id"] and "id" in season:
                info["season_id"] = season["id"]
            comp = season.get("competition", {})
            org = comp.get("organisation", {})
            if not info["org_id"] and "id" in org:
                info["org_id"] = org["id"]

        if "discoverSeason" in obj and isinstance(obj["discoverSeason"], dict):
            season = obj["discoverSeason"]
            if not info["season_id"] and "id" in season:
                info["season_id"] = season["id"]
            comp = season.get("competition", {})
            org = comp.get("organisation", {})
            if not info["org_id"] and "id" in org:
                info["org_id"] = org["id"]

        if "discoverOrganisation" in obj and isinstance(obj["discoverOrganisation"], dict):
            org = obj["discoverOrganisation"]
            if not info["org_id"] and "id" in org:
                info["org_id"] = org["id"]

        for v in obj.values():
            _extract_ids_from_props(v, info)
    elif isinstance(obj, list):
        for item in obj:
            _extract_ids_from_props(item, info)


# ---------------------------------------------------------------------------
# High-level fetchers
# ---------------------------------------------------------------------------

def fetch_competitions(org_id: str) -> list[dict]:
    """Fetch all competitions and seasons for an organisation."""
    data = gql(Q_DISCOVER_COMPETITIONS, {
        "organisationID": org_id,
        "organisationCode": org_id,
    })
    org = data.get("discoverOrganisation", {})
    comps = data.get("discoverCompetitions", [])
    return {"organisation": org, "competitions": comps}


def fetch_grades(season_id: str) -> dict:
    """Fetch all grades for a season."""
    data = gql(Q_GRADE_LIST, {"id": season_id})
    return data.get("discoverSeason", {})


def fetch_grade_fixtures(grade_id: str) -> list[dict]:
    """Fetch all rounds/fixtures for a grade. Returns list of rounds."""
    data = gql(Q_GRADE_FIXTURES, {"gradeID": grade_id})
    return data.get("discoverGradeFixture", [])


def fetch_grade_ladder(grade_id: str) -> list[dict]:
    """Fetch ladder pools for a grade. Returns list of LadderPool dicts."""
    data = gql(Q_GRADE_LADDER, {"gradeID": grade_id})
    return (data.get("discoverGrade") or {}).get("ladder", [])


# ---------------------------------------------------------------------------
# Sheet-ready row builders
# ---------------------------------------------------------------------------

COMPETITIONS_HEADERS = [
    "Competition Name",
    "Competition ID",
    "Season Name",
    "Season ID",
    "Start Date",
    "End Date",
    "Status",
]

GRADES_HEADERS = [
    "Grade Name",
    "Grade ID",
    "Gender",
    "Age",
    "Season Name",
    "Season ID",
    "Competition Name",
    "Competition ID",
]

FIXTURES_HEADERS = [
    "Grade Name",
    "Grade ID",
    "Round",
    "Date",
    "Time",
    "Venue",
    "Court",
    "Home Team",
    "Away Team",
    "Home Score",
    "Away Score",
    "Status",
]

LADDER_HEADERS = [
    "Grade Name",
    "Grade ID",
    "Position",
    "Team",
    "Team ID",
    "Played",
    "Won",
    "Lost",
    "Drawn",
    "Byes",
    "Points For",
    "Points Against",
    "Points Difference",
    "Forfeits",
    "Bonus Points",
]


def competitions_rows(org_id: str) -> tuple[str, list[list]]:
    """Return (org_name, rows) ready for Google Sheets.

    One row per (competition, season). Columns match COMPETITIONS_HEADERS.
    """
    result = fetch_competitions(org_id)
    org_name = result["organisation"].get("name", "")
    rows: list[list] = []
    for c in result["competitions"]:
        cname = c.get("name", "")
        cid = c.get("id", "")
        for s in c.get("seasons", []) or []:
            status = (s.get("status") or {}).get("name", "")
            rows.append([
                cname,
                cid,
                s.get("name", ""),
                s.get("id", ""),
                s.get("startDate", "") or "",
                s.get("endDate", "") or "",
                status,
            ])
    return org_name, rows


def grades_rows(season_id: str) -> list[list]:
    """Return rows ready for Google Sheets. One row per grade in the season."""
    data = fetch_grades(season_id)
    season_name = data.get("name", "")
    comp = data.get("competition") or {}
    comp_name = comp.get("name", "")
    comp_id = comp.get("id", "")
    rows: list[list] = []
    for g in data.get("grades", []) or []:
        rows.append([
            g.get("name", ""),
            g.get("id", ""),
            (g.get("gender") or {}).get("name", ""),
            (g.get("age") or {}).get("name", ""),
            season_name,
            season_id,
            comp_name,
            comp_id,
        ])
    return rows


def fixtures_rows(grade_id: str, grade_name: str = "") -> list[list]:
    """Return rows ready for Google Sheets. One row per fixture in the grade."""
    rounds_data = fetch_grade_fixtures(grade_id)
    fixtures = rounds_to_fixtures(rounds_data)
    return [
        [
            grade_name, grade_id,
            f.round_name, f.date, f.time, f.venue, f.court,
            f.home_team, f.away_team,
            f.home_score if f.home_score is not None else "",
            f.away_score if f.away_score is not None else "",
            f.status,
        ]
        for f in fixtures
    ]


def comp_to_fixtures_rows(comp: Competition) -> list[list]:
    """Convert an already-fetched Competition to fixture rows (no extra API calls)."""
    rows: list[list] = []
    for grade in comp.grades:
        gname = grade.get("name", "")
        gid = grade.get("url", "")  # url field stores the grade ID
        for f in grade.get("fixtures", []):
            rows.append([
                gname, gid,
                f.get("round_name", ""), f.get("date", ""), f.get("time", ""),
                f.get("venue", ""), f.get("court", ""),
                f.get("home_team", ""), f.get("away_team", ""),
                f["home_score"] if f.get("home_score") is not None else "",
                f["away_score"] if f.get("away_score") is not None else "",
                f.get("status", ""),
            ])
    return rows


def all_ladder_rows(season_id: str) -> list[list]:
    """Fetch ladder for every grade in the season and return combined rows."""
    data = fetch_grades(season_id)
    grades = data.get("grades", []) or []
    rows: list[list] = []
    for g in grades:
        gid = g.get("id", "")
        gname = g.get("name", "")
        print(f"  Fetching ladder for {gname} ({gid})...")
        for pool in fetch_grade_ladder(gid):
            for pos, s in enumerate(pool.get("standings", []), start=1):
                team = s.get("team") or {}
                rows.append([
                    gname, gid, pos,
                    team.get("name", ""), team.get("id", ""),
                    s.get("played", 0),
                    s.get("won", 0), s.get("lost", 0), s.get("drawn", 0),
                    s.get("byes", 0),
                    s.get("pointsFor", 0), s.get("pointsAgainst", 0), s.get("pointsDifference", 0),
                    s.get("forfeits", 0), s.get("bonusPoints", 0),
                ])
    return rows


# ---------------------------------------------------------------------------
# Convert API data to Fixture objects
# ---------------------------------------------------------------------------

def rounds_to_fixtures(rounds_data: list[dict]) -> list[Fixture]:
    """Convert gradeAllRounds response to list of Fixture objects."""
    fixtures = []
    for rnd in rounds_data:
        round_name = rnd.get("name", "")

        # Byes
        for bye_team in rnd.get("byes", []):
            fixtures.append(Fixture(
                round_name=round_name,
                home_team=bye_team.get("name", ""),
                away_team="BYE",
                status="bye",
            ))

        # Games
        for game in rnd.get("games", []):
            home = game.get("home", {})
            away = game.get("away", {})
            result = game.get("result") or {}
            alloc = game.get("allocation") or {}
            court_info = alloc.get("court") or {}
            venue_info = court_info.get("venue") or {}
            status_info = game.get("status") or {}

            status_val = status_info.get("value", "").upper()
            if status_val == "FINAL":
                status = "completed"
            elif status_val in ("UPCOMING", "SCHEDULED"):
                status = "scheduled"
            else:
                status = status_val.lower() if status_val else "scheduled"

            home_score = get_score(result.get("home"))
            away_score = get_score(result.get("away"))

            venue_name = venue_info.get("name", "")
            suburb = venue_info.get("suburb", "")
            venue_str = f"{venue_name}, {suburb}" if suburb else venue_name

            fixtures.append(Fixture(
                round_name=round_name,
                date=game.get("date", ""),
                time=alloc.get("time", ""),
                venue=venue_str,
                court=court_info.get("name", ""),
                home_team=home.get("name", ""),
                away_team=away.get("name", ""),
                home_score=home_score,
                away_score=away_score,
                status=status,
            ))

    return fixtures


# ---------------------------------------------------------------------------
# Main workflow (mirrors scrape_fixtures.py interface)
# ---------------------------------------------------------------------------

def scrape_competition(url: str = "", target_grade: Optional[str] = None,
                       all_grades: bool = False,
                       org_id: str = "", season_id: str = "",
                       grade_id: str = "") -> Competition:
    """Fetch competition data via the GraphQL API."""
    comp = Competition(url=url)

    # Resolve IDs from URL if needed
    if url and not (org_id or season_id or grade_id):
        print(f"Resolving IDs from URL...")
        info = parse_playhq_url(url)
        org_id = org_id or info.get("org_id", "")
        season_id = season_id or info.get("season_id", "")
        grade_id = grade_id or info.get("grade_id", "")
        print(f"  org={org_id}  season={season_id}  grade={grade_id}")

    # If we have a grade ID directly, just fetch its fixtures
    if grade_id and not (target_grade or all_grades):
        print(f"Fetching fixtures for grade {grade_id}...")
        rounds_data = fetch_grade_fixtures(grade_id)
        fixtures = rounds_to_fixtures(rounds_data)
        grade = Grade(name=grade_id, fixtures=[asdict(f) for f in fixtures])
        comp.grades.append(asdict(grade))
        print(f"  Found {len(fixtures)} fixtures across {len(rounds_data)} rounds")
        return comp

    # Get season ID if we only have org ID
    if org_id and not season_id:
        print(f"Fetching competitions for org {org_id}...")
        result = fetch_competitions(org_id)
        org_info = result["organisation"]
        comp.organisation = org_info.get("name", "")
        comps = result["competitions"]

        print(f"  Org: {comp.organisation}")
        print(f"  Found {len(comps)} competitions:")
        for c in comps:
            seasons = c.get("seasons", [])
            print(f"    - {c['name']}:")
            for s in seasons:
                status_str = s.get("status", {}).get("name", "?")
                print(f"        {s['name']} [{s['id']}] ({status_str})")

        if not season_id:
            print("\nProvide --season-id to drill into a specific season.")
            return comp

    # Get grades for the season
    if season_id:
        print(f"Fetching grades for season {season_id}...")
        season_data = fetch_grades(season_id)
        comp.name = season_data.get("competition", {}).get("name", "")
        comp.season = season_data.get("name", "")
        grades_list = season_data.get("grades", [])

        print(f"  Competition: {comp.name}")
        print(f"  Season: {comp.season}")
        print(f"  Found {len(grades_list)} grades:")
        for g in grades_list:
            gender = g.get("gender", {})
            age = g.get("age", {})
            gender_str = gender.get("name", "") if gender else ""
            age_str = age.get("name", "") if age else ""
            print(f"    - {g['name']} ({gender_str} {age_str}) [id: {g['id']}]")

        # Filter if target grade specified
        if target_grade:
            grades_list = [g for g in grades_list if target_grade.lower() in g["name"].lower()]
            if not grades_list:
                print(f"Grade '{target_grade}' not found.")
                return comp
            print(f"\nFiltering to: {grades_list[0]['name']}")

        if not all_grades and not target_grade:
            for g in grades_list:
                grade = Grade(name=g["name"], url=g["id"])
                comp.grades.append(asdict(grade))
            print("\nUse --grade <name> to scrape a specific grade, or --all-grades for all.")
            return comp

        # Fetch fixtures for selected grades
        for g in grades_list:
            gid = g["id"]
            gname = g["name"]
            print(f"\nFetching fixtures for {gname} ({gid})...")
            rounds_data = fetch_grade_fixtures(gid)
            fixtures = rounds_to_fixtures(rounds_data)
            grade = Grade(name=gname, url=gid, fixtures=[asdict(f) for f in fixtures])
            comp.grades.append(asdict(grade))
            print(f"  {len(fixtures)} fixtures across {len(rounds_data)} rounds")

    return comp


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def print_results(comp: Competition):
    """Pretty-print results (same format as scrape_fixtures.py)."""
    print(f"\n{'='*70}")
    print(f"Competition: {comp.name}")
    if comp.organisation:
        print(f"Organisation: {comp.organisation}")
    if comp.season:
        print(f"Season: {comp.season}")
    if comp.url:
        print(f"URL: {comp.url}")
    print(f"{'='*70}")

    for grade_data in comp.grades:
        fixtures = grade_data.get("fixtures", [])
        print(f"\n  Grade: {grade_data['name']}  ({len(fixtures)} fixtures)")
        print(f"  {'-'*60}")

        if not fixtures:
            print("  No fixtures found.")
            continue

        current_round = ""
        for f in fixtures:
            if f["round_name"] and f["round_name"] != current_round:
                current_round = f["round_name"]
                print(f"\n    {current_round}")

            date_str = f["date"] or ""
            time_str = f["time"] or ""
            if time_str:
                # Format HH:MM:SS to HH:MM
                time_str = time_str[:5]
            dt = f"{date_str} {time_str}".strip()

            court = f.get("court", "")
            court_col = f"{court:12s}" if court else f"{'':12s}"

            if f["status"] == "bye":
                print(f"    {dt:20s} {court_col} {f['home_team']:30s}  BYE")
            elif f["home_score"] is not None:
                print(
                    f"    {dt:20s} {court_col} {f['home_team']:30s} {f['home_score']:3d} - "
                    f"{f['away_score']:<3d} {f['away_team']}"
                )
            else:
                print(f"    {dt:20s} {court_col} {f['home_team']:30s}  v  {f['away_team']}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="PlayHQ GraphQL API client")
    parser.add_argument("url", nargs="?", default="", help="PlayHQ URL (auto-detects type)")
    parser.add_argument("--grade", "-g", help="Scrape only this grade (partial match)")
    parser.add_argument("--all-grades", "-a", action="store_true", help="Scrape all grades")
    parser.add_argument("--output", "-o", help="Save results to JSON file")

    parser.add_argument("--org-id", default="", help="Organisation ID (hex)")
    parser.add_argument("--season-id", default="", help="Season ID (hex)")
    parser.add_argument("--grade-id", default="", help="Grade ID (hex)")

    parser.add_argument("--push-to-sheet", help="Google Sheets spreadsheet ID to push results to")
    parser.add_argument("--worksheet", default="Competitions", help="Worksheet/tab name (default: Competitions)")
    parser.add_argument("--ladder-worksheet", default="Ladder", help="Worksheet name for ladder data (default: Ladder)")

    args = parser.parse_args()

    if not args.url and not (args.org_id or args.season_id or args.grade_id):
        parser.print_help()
        sys.exit(1)

    # Resolve URL once so push code can branch on what was requested.
    org_id = args.org_id
    season_id = args.season_id
    grade_id = args.grade_id
    if args.url and not (org_id or season_id or grade_id):
        info = parse_playhq_url(args.url)
        org_id = info.get("org_id") or ""
        season_id = info.get("season_id") or ""
        grade_id = info.get("grade_id") or ""

    comp = scrape_competition(
        url=args.url,
        target_grade=args.grade,
        all_grades=args.all_grades,
        org_id=org_id,
        season_id=season_id,
        grade_id=grade_id,
    )

    print_results(comp)

    if args.output:
        with open(args.output, "w") as f:
            json.dump(asdict(comp), f, indent=2)
        print(f"\nResults saved to {args.output}")

    if args.push_to_sheet:
        from sheets import push_rows
        if org_id and not season_id and not grade_id:
            print(f"\nPushing competitions to Google Sheet '{args.worksheet}'...")
            org_name, rows = competitions_rows(org_id)
            push_rows(
                args.push_to_sheet,
                args.worksheet,
                COMPETITIONS_HEADERS,
                rows,
            )
        elif season_id and args.all_grades and not grade_id:
            print(f"\nPushing all fixtures to Google Sheet '{args.worksheet}'...")
            rows = comp_to_fixtures_rows(comp)
            push_rows(
                args.push_to_sheet,
                args.worksheet,
                FIXTURES_HEADERS,
                rows,
            )
            print(f"\nPushing ladder to Google Sheet '{args.ladder_worksheet}'...")
            ladder_rows = all_ladder_rows(season_id)
            push_rows(
                args.push_to_sheet,
                args.ladder_worksheet,
                LADDER_HEADERS,
                ladder_rows,
            )
        elif season_id and not grade_id:
            print(f"\nPushing grades to Google Sheet '{args.worksheet}'...")
            rows = grades_rows(season_id)
            push_rows(
                args.push_to_sheet,
                args.worksheet,
                GRADES_HEADERS,
                rows,
            )
        elif grade_id:
            print(f"\nPushing fixtures to Google Sheet '{args.worksheet}'...")
            rows = comp_to_fixtures_rows(comp)
            push_rows(
                args.push_to_sheet,
                args.worksheet,
                FIXTURES_HEADERS,
                rows,
            )
        else:
            print("--push-to-sheet supports org-level (competitions), season-level (grades), and grade-level (fixtures) views.",
                  file=sys.stderr)


if __name__ == "__main__":
    main()
