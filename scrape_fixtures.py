#!/usr/bin/env python3
"""
PlayHQ Fixtures & Scores Scraper using headless Playwright.

Usage:
    python scrape_fixtures.py <playhq_url>
    python scrape_fixtures.py <playhq_url> --grade "11A"
    python scrape_fixtures.py <playhq_url> --all-grades
    python scrape_fixtures.py <playhq_url> --output results.json
"""

import asyncio
import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from typing import Optional
from playwright.async_api import async_playwright, Page, Browser


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
    status: str = ""  # scheduled, completed, bye, forfeit, etc.


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


async def launch_browser(playwright) -> Browser:
    return await playwright.chromium.launch(headless=True)


async def new_page(browser: Browser) -> Page:
    page = await browser.new_page()
    await page.set_extra_http_headers({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    })
    return page


async def wait_for_content(page: Page, timeout: int = 15000):
    """Wait for the main content to render."""
    # networkidle often never fires on SPAs with analytics/websockets — use a fallback
    try:
        await page.wait_for_load_state("networkidle", timeout=timeout)
    except Exception:
        # Fallback: just wait for load + extra time for JS rendering
        await page.wait_for_load_state("load", timeout=timeout)
    # Extra buffer for JS rendering
    await page.wait_for_timeout(3000)


async def dismiss_banners(page: Page):
    """Try to dismiss app install banners or cookie popups."""
    selectors = [
        'button[aria-label*="close" i]',
        'button[aria-label*="dismiss" i]',
        'button:has-text("Close")',
        'button:has-text("Dismiss")',
        'button:has-text("No thanks")',
        '[class*="banner"] button',
        '[class*="modal"] button[class*="close"]',
    ]
    for sel in selectors:
        try:
            btn = await page.query_selector(sel)
            if btn and await btn.is_visible():
                await btn.click()
                await page.wait_for_timeout(500)
                return
        except Exception:
            continue


async def scrape_grades(page: Page, url: str) -> list[dict]:
    """Scrape the list of grades from a competition page."""
    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    await wait_for_content(page)
    await dismiss_banners(page)

    grades = []

    # PlayHQ renders grades in a table. Look for rows with "Select" links.
    rows = await page.query_selector_all("tr")
    for row in rows:
        cells = await row.query_selector_all("td")
        if not cells:
            continue

        # First cell is usually the grade name
        name = (await cells[0].inner_text()).strip()
        if not name:
            continue

        # Look for a "Select" link in this row
        link = await row.query_selector('a:has-text("Select")')
        if not link:
            link = await row.query_selector("a[href]")

        href = ""
        if link:
            href = await link.get_attribute("href") or ""
            if href and not href.startswith("http"):
                href = f"https://www.playhq.com{href}"

        if name and href:
            grades.append({"name": name, "url": href})

    # Fallback: look for anchor tags with grade-like URLs
    if not grades:
        links = await page.query_selector_all('a[href*="/fixtures"]')
        for link in links:
            text = (await link.inner_text()).strip()
            href = await link.get_attribute("href") or ""
            if href and not href.startswith("http"):
                href = f"https://www.playhq.com{href}"
            if text and href:
                grades.append({"name": text, "url": href})

    return grades


async def scrape_fixtures_from_page(page: Page, preloaded_text: str = "") -> list[Fixture]:
    """Extract fixture data from a PlayHQ page.

    Args:
        page: Playwright page object
        preloaded_text: If provided, use this text instead of re-reading from page
                       (avoids race conditions with React hydration)
    """
    fixtures = []

    if not preloaded_text:
        # Try to click "Fixture" tab if present (page might default to Ladder)
        for tab_text in ["Fixture", "Fixtures", "Draw"]:
            try:
                tab = await page.query_selector(
                    f'a:has-text("{tab_text}"), button:has-text("{tab_text}"), '
                    f'[role="tab"]:has-text("{tab_text}")'
                )
                if tab and await tab.is_visible():
                    await tab.click()
                    await page.wait_for_timeout(2000)
                    break
            except Exception:
                continue

        await dismiss_banners(page)

    text = preloaded_text or await page.inner_text("body")
    lines = [l.strip() for l in text.split("\n") if l.strip()]

    # Find where fixture data starts (skip nav/header)
    start_idx = 0
    for i, line in enumerate(lines):
        if re.match(r"^(Round\s+\d+|Semi\s*Final|Grand\s*Final)", line, re.IGNORECASE):
            start_idx = i
            break

    if start_idx == 0:
        print("  No fixture rounds found in page text.")
        return fixtures

    # Find where fixture data ends (before footer)
    end_idx = len(lines)
    for i in range(start_idx + 1, len(lines)):
        # Footer markers
        if lines[i] in ("Your Sport United", "Get PlayHQ", "© 2026 PlayHQ"):
            end_idx = i
            break
        # Org contact section
        if re.match(r"^.+@.+\..+$", lines[i]) and i > start_idx + 10:
            end_idx = i
            break

    fixture_lines = lines[start_idx:end_idx]
    print(f"  Parsing {len(fixture_lines)} lines of fixture data (lines {start_idx}-{end_idx})")

    current_round = ""
    current_date = ""
    i = 0

    while i < len(fixture_lines):
        line = fixture_lines[i]

        # Round header
        if re.match(r"^(Round\s+\d+|Semi\s*Final|Grand\s*Final|Preliminary\s*Final|Final\s*\d*)", line, re.IGNORECASE):
            current_round = line
            i += 1
            continue

        # Date header: "Sunday, 22 March 2026"
        date_match = re.match(
            r"^(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+"
            r"(\d{1,2}\s+\w+\s+\d{4})$",
            line, re.IGNORECASE,
        )
        if date_match:
            current_date = date_match.group(1)
            i += 1
            continue

        # Try fixture block: HomeTeam / HomeScore / Status / AwayTeam / AwayScore / DateTime / Venue
        # Scores can be digits (completed) or "-" (upcoming/scheduled)
        if (i + 4 < len(fixture_lines)
                and re.match(r"^(\d{1,3}|-)$", fixture_lines[i + 1])):
            home_team = line
            home_score_str = fixture_lines[i + 1]
            home_score = int(home_score_str) if home_score_str != "-" else None
            idx = i + 2

            # Optional status line (FINAL, UPCOMING, etc.)
            status = "completed" if home_score is not None else "scheduled"
            if idx < len(fixture_lines) and fixture_lines[idx].upper() in {
                "FINAL", "UPCOMING", "CANCELLED", "FORFEIT", "POSTPONED",
                "ABANDONED", "IN PROGRESS", "LIVE", "TBD",
            }:
                raw_status = fixture_lines[idx].upper()
                if raw_status == "FINAL":
                    status = "completed"
                elif raw_status == "UPCOMING":
                    status = "scheduled"
                else:
                    status = raw_status.lower()
                idx += 1

            # Away team + away score
            if (idx + 1 < len(fixture_lines)
                    and re.match(r"^(\d{1,3}|-)$", fixture_lines[idx + 1])):
                away_team = fixture_lines[idx]
                away_score_str = fixture_lines[idx + 1]
                away_score = int(away_score_str) if away_score_str != "-" else None
                idx += 2

                # DateTime line: "09:00 AM, Sun, 22 Mar 26"
                time_str = ""
                date_str = current_date
                if (idx < len(fixture_lines)
                        and re.match(r"\d{1,2}:\d{2}\s*(?:AM|PM)", fixture_lines[idx], re.IGNORECASE)):
                    dt_line = fixture_lines[idx]
                    tm = re.match(r"(\d{1,2}:\d{2}\s*(?:AM|PM))", dt_line, re.IGNORECASE)
                    if tm:
                        time_str = tm.group(1)
                    dp = re.search(r",\s*\w+,\s*(.+)$", dt_line)
                    if dp:
                        date_str = dp.group(1).strip()
                    idx += 1

                # Venue / Court line
                venue = ""
                court = ""
                if idx < len(fixture_lines) and "/" in fixture_lines[idx]:
                    parts = fixture_lines[idx].split("/", 1)
                    venue = parts[0].strip()
                    court = parts[1].strip()
                    idx += 1

                fixtures.append(Fixture(
                    round_name=current_round, date=date_str, time=time_str,
                    venue=venue, court=court,
                    home_team=home_team, away_team=away_team,
                    home_score=home_score, away_score=away_score,
                    status=status,
                ))
                i = idx
                continue

        # BYE pattern
        if "BYE" in line.upper() and len(line) < 80:
            bye_team = re.sub(r"\s*[-–]\s*BYE\b", "", line, flags=re.IGNORECASE).strip()
            if bye_team and bye_team.upper() != "BYE":
                fixtures.append(Fixture(
                    round_name=current_round, date=current_date,
                    home_team=bye_team, away_team="BYE", status="bye",
                ))
            i += 1
            continue

        i += 1

    return fixtures


async def scrape_grade_fixtures(browser: Browser, grade_url: str) -> list[Fixture]:
    """Navigate to a grade page and scrape its fixtures."""
    page = await new_page(browser)
    try:
        print(f"  Loading {grade_url} ...")
        await page.goto(grade_url, wait_until="domcontentloaded", timeout=30000)
        await wait_for_content(page)
        await dismiss_banners(page)

        # Check if we need to navigate to a "Fixtures" sub-page
        # Some grade pages default to ladder view
        fixtures_link = await page.query_selector('a[href*="fixture"], a[href*="draw"]')
        if fixtures_link:
            href = await fixtures_link.get_attribute("href")
            if href:
                if not href.startswith("http"):
                    href = f"https://www.playhq.com{href}"
                await page.goto(href, wait_until="domcontentloaded", timeout=30000)
                await wait_for_content(page)

        # Try to load all rounds if there's a "Show All" or round selector
        try:
            show_all = await page.query_selector('button:has-text("Show All"), a:has-text("Show All"), button:has-text("All Rounds")')
            if show_all and await show_all.is_visible():
                await show_all.click()
                await page.wait_for_timeout(2000)
        except Exception:
            pass

        # Take debug screenshot
        ts = int(time.time())
        screenshot_path = f"debug_fixtures_{ts}.png"
        await page.screenshot(path=screenshot_path, full_page=True)
        print(f"  Screenshot saved: {screenshot_path}")

        fixtures = await scrape_fixtures_from_page(page)
        return fixtures
    finally:
        await page.close()


async def scrape_competition(url: str, target_grade: Optional[str] = None, all_grades: bool = False, args_debug: bool = False) -> Competition:
    """Main entry point: scrape a PlayHQ competition URL for fixtures and scores."""
    comp = Competition(url=url)

    # Extract metadata from URL
    parts = url.rstrip("/").split("/")
    try:
        org_idx = parts.index("org")
        comp.organisation = parts[org_idx + 1].replace("-", " ").title()
        comp.name = parts[org_idx + 2].replace("-", " ").title()
        if org_idx + 3 < len(parts):
            comp.season = parts[org_idx + 3].replace("-", " ").title()
    except (ValueError, IndexError):
        pass

    async with async_playwright() as pw:
        browser = await launch_browser(pw)
        try:
            page = await new_page(browser)

            print(f"Loading competition page: {url}")
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await wait_for_content(page)
            await dismiss_banners(page)

            # Take a debug screenshot
            if args_debug:
                ts = int(time.time())
                await page.screenshot(path=f"debug_comp_{ts}.png", full_page=True)
                print(f"  Debug screenshot: debug_comp_{ts}.png")

            # Detect what kind of page we're on by checking page text
            body_text = await page.inner_text("body")

            has_rounds = bool(re.search(r"Round\s+\d+", body_text, re.IGNORECASE))
            has_select = bool(re.search(r"\bSelect\b", body_text))

            print(f"  Page analysis: has_rounds={has_rounds}, has_select={has_select}")
            print(f"  Body text length: {len(body_text)}")

            # If the "Fixture" tab exists but isn't active, click it
            if not has_rounds:
                for tab_text in ["Fixture", "Fixtures", "Fixtures & Ladders"]:
                    try:
                        tab = await page.query_selector(
                            f'a:has-text("{tab_text}"), [data-testid="fixtures-ladders-link"]'
                        )
                        if tab and await tab.is_visible():
                            await tab.click()
                            print(f"  Clicked '{tab_text}' tab")
                            await wait_for_content(page)
                            body_text = await page.inner_text("body")
                            has_rounds = bool(re.search(r"Round\s+\d+", body_text, re.IGNORECASE))
                            if has_rounds:
                                break
                    except Exception:
                        continue

            if has_rounds:
                # Page has fixture data — scrape directly
                print("Scraping fixtures from this page...")
                fixtures = await scrape_fixtures_from_page(page, preloaded_text=body_text)
                grade = Grade(name="(direct)", url=url, fixtures=[asdict(f) for f in fixtures])
                comp.grades.append(asdict(grade))
                await page.close()
                return comp

            # Check if this is a grade-selection page
            select_buttons = await page.query_selector_all('a:has-text("Select")')

            if not select_buttons:
                print("No fixture rounds or grade selection found on this page.")
                # Dump text for debugging
                text_lines = [l.strip() for l in body_text.split("\n") if l.strip()]
                print("  Page text (first 30 lines):")
                for line in text_lines[:30]:
                    print(f"    {line}")
                await page.close()
                return comp

            # It's a grade-list page — extract grades
            print("Found grade selection page. Extracting grades...")
            grades_data = await scrape_grades(page, url)
            await page.close()

            if not grades_data:
                print("No grades found on the page.")
                return comp

            print(f"Found {len(grades_data)} grades:")
            for g in grades_data:
                print(f"  - {g['name']}: {g['url']}")

            # Filter grades if a specific one is requested
            if target_grade:
                grades_data = [g for g in grades_data if target_grade.lower() in g["name"].lower()]
                if not grades_data:
                    print(f"Grade '{target_grade}' not found.")
                    return comp
                print(f"Filtering to grade: {grades_data[0]['name']}")

            if not all_grades and not target_grade:
                # Just list grades, don't scrape all
                for g in grades_data:
                    grade = Grade(name=g["name"], url=g["url"])
                    comp.grades.append(asdict(grade))
                print("\nUse --grade <name> to scrape a specific grade, or --all-grades to scrape all.")
                return comp

            # Scrape fixtures for selected grades
            for g in grades_data:
                print(f"\nScraping fixtures for grade: {g['name']}")
                fixtures = await scrape_grade_fixtures(browser, g["url"])
                grade = Grade(
                    name=g["name"],
                    url=g["url"],
                    fixtures=[asdict(f) for f in fixtures],
                )
                comp.grades.append(asdict(grade))
                print(f"  Found {len(fixtures)} fixtures")

        finally:
            await browser.close()

    return comp


def print_results(comp: Competition):
    """Pretty-print the scraped results."""
    print(f"\n{'='*70}")
    print(f"Competition: {comp.name}")
    print(f"Organisation: {comp.organisation}")
    if comp.season:
        print(f"Season: {comp.season}")
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


async def main():
    parser = argparse.ArgumentParser(description="Scrape PlayHQ fixtures & scores")
    parser.add_argument("url", help="PlayHQ competition or grade URL")
    parser.add_argument("--grade", "-g", help="Scrape only this grade (partial match)")
    parser.add_argument("--all-grades", "-a", action="store_true", help="Scrape all grades")
    parser.add_argument("--output", "-o", help="Save results to JSON file")
    parser.add_argument("--debug", "-d", action="store_true", help="Save debug screenshots")

    args = parser.parse_args()

    comp = await scrape_competition(args.url, target_grade=args.grade, all_grades=args.all_grades, args_debug=args.debug)

    print_results(comp)

    if args.output:
        with open(args.output, "w") as f:
            json.dump(asdict(comp), f, indent=2)
        print(f"\nResults saved to {args.output}")


if __name__ == "__main__":
    asyncio.run(main())
