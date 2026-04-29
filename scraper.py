import requests
from bs4 import BeautifulSoup
import sqlite3
import time
import re
import sys
import argparse
import asyncio
from urllib.parse import urljoin, urlparse
from typing import List, Dict, Optional
from playwright.async_api import async_playwright

class WebScraper:
    def __init__(self, db_path: str = "scraped_data.db", use_playwright: bool = True):
        self.db_path = db_path
        self.use_playwright = use_playwright

        if not use_playwright:
            self.session = requests.Session()
            self.session.headers.update({
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            })

        self.init_database()

    def init_database(self):
        """Initialize SQLite database with tables for competition data"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS competitions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                organization TEXT,
                scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS grades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                competition_id INTEGER,
                name TEXT NOT NULL,
                gender TEXT,
                age_group TEXT,
                url TEXT,
                FOREIGN KEY (competition_id) REFERENCES competitions (id)
            )
        ''')

        conn.commit()
        conn.close()

    def extract_org_and_competition_from_url(self, url: str) -> tuple:
        """Extract organization and competition name from PlayHQ URL"""
        try:
            path_parts = url.split('/')
            org_idx = -1

            for i, part in enumerate(path_parts):
                if part == 'org':
                    org_idx = i
                    break

            if org_idx >= 0 and len(path_parts) > org_idx + 2:
                org_name = path_parts[org_idx + 1].replace('-', ' ').title()
                comp_name = path_parts[org_idx + 2].replace('-', ' ').title()
                comp_name = re.sub(r'\b\d{4}\b', lambda m: m.group(), comp_name)
                return org_name, comp_name

            return "Unknown Organization", "Unknown Competition"
        except:
            return "Unknown Organization", "Unknown Competition"

    def parse_gender_age_from_grade(self, grade_name: str) -> tuple:
        """Extract gender and age group from grade name"""
        grade_lower = grade_name.lower()

        gender = None
        if 'women' in grade_lower or 'ladies' in grade_lower or 'girls' in grade_lower:
            gender = 'Female'
        elif 'men' in grade_lower or 'boys' in grade_lower:
            gender = 'Male'
        elif 'mixed' in grade_lower or 'open' in grade_lower:
            gender = 'Mixed'

        age_group = None

        # Check for specific age group patterns
        if re.match(r'^u\d{1,2}$', grade_lower):  # U12, U13
            age_group = grade_name.upper()
        elif grade_lower in ['junior', 'intermediate', 'senior']:
            age_group = grade_name.title()
        else:
            # For grade names like 11A, Cadet 1, etc., try to extract age info
            age_patterns = [
                (r'under\s*(\d+)', r'U\1'),
                (r'(\d+)\s*and\s*under', r'U\1'),
                (r'^(\d{1,2})[a-z]', r'U\1'),  # 11A -> U11
                (r'cadet', 'Junior'),
                (r'inters', 'Intermediate')
            ]

            for pattern, replacement in age_patterns:
                match = re.search(pattern, grade_lower)
                if match:
                    if r'\1' in replacement:
                        age_group = replacement.replace(r'\1', match.group(1))
                    else:
                        age_group = replacement
                    break

        return gender, age_group

    async def scrape_playhq_competition_async(self, url: str, delay: float = 1.0) -> Optional[Dict]:
        """Scrape PlayHQ competition page using Playwright"""
        try:
            await asyncio.sleep(delay)

            if self.use_playwright:
                async with async_playwright() as p:
                    browser = await p.chromium.launch(headless=True)
                    page = await browser.new_page()

                    await page.set_extra_http_headers({
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
                    })

                    await page.goto(url, wait_until='domcontentloaded', timeout=30000)

                    await page.wait_for_timeout(3000)

                    # Try to dismiss the NetballHQ App banner
                    try:
                        # Look for common banner close button selectors
                        banner_selectors = [
                            'button[aria-label="Close App Banner"]',
                            '[data-testid="close-banner"]',
                            '.banner-close',
                            '.close-banner',
                            'button[aria-label*="close"]',
                            'button[aria-label*="dismiss"]',
                            '.modal-close',
                            '[class*="close"]',
                            'button:has-text("Close")',
                            'button:has-text("Dismiss")',
                            '[role="button"]:has-text("×")'
                        ]

                        for selector in banner_selectors:
                            try:
                                close_button = await page.query_selector(selector)
                                if close_button:
                                    await close_button.click()
                                    print(f"Dismissed banner using selector: {selector}")
                                    await page.wait_for_timeout(1000)
                                    break
                            except:
                                continue

                    except Exception as e:
                        print(f"Could not dismiss banner: {e}")

                    await page.wait_for_timeout(2000)

                    # Take screenshot for debugging
                    screenshot_path = f"debug_screenshot_{int(time.time())}.png"
                    await page.screenshot(path=screenshot_path, full_page=True)
                    print(f"Screenshot saved: {screenshot_path}")

                    html_content = await page.content()
                    await browser.close()

                    soup = BeautifulSoup(html_content, 'html.parser')
            else:
                response = self.session.get(url, timeout=10)
                response.raise_for_status()
                soup = BeautifulSoup(response.content, 'html.parser')

            organization, competition_name = self.extract_org_and_competition_from_url(url)

            grades = []

            grade_selectors = [
                'td',  # Table cells that might contain grade names
                'tr td:first-child',  # First column of table rows
                'a[href*="/grade/"]',
                'a[href*="/division/"]',
                'div[class*="grade"]',
                'div[class*="division"]',
                'li[class*="grade"]',
                'button[class*="grade"]',
                'div[data-testid*="grade"]',
                '.grade-link',
                '.division-link'
            ]

            found_grades = set()

            # First, look for grade elements with associated URLs (like Select buttons)
            grade_rows = soup.find_all(['tr', 'div'], class_=lambda x: x and ('grade' in x.lower() or 'row' in x.lower()))

            for row in grade_rows:
                # Look for grade text and Select button in the same row
                grade_text_elem = None
                select_button = None

                # Find potential grade text
                for text_elem in row.find_all(string=True):
                    text_clean = text_elem.strip()
                    if (text_clean and len(text_clean) < 20 and
                        (re.match(r'^\d{1,2}[A-Z]$', text_clean) or
                         re.match(r'^Cadet\s\d+$', text_clean) or
                         re.match(r'^Inters\s\d+$', text_clean) or
                         text_clean.lower() in ['u12', 'u13'])):
                        grade_text_elem = text_clean
                        break

                # Find Select button with href
                select_links = row.find_all('a', string=re.compile(r'Select', re.IGNORECASE))
                if select_links:
                    select_button = select_links[0]

                if grade_text_elem and select_button and grade_text_elem not in found_grades:
                    grade_url = select_button.get('href', '')
                    if grade_url and not grade_url.startswith('http'):
                        grade_url = urljoin(url, grade_url)

                    gender, age_group = self.parse_gender_age_from_grade(grade_text_elem)
                    grades.append({
                        'name': grade_text_elem,
                        'gender': gender,
                        'age_group': age_group,
                        'url': grade_url
                    })
                    found_grades.add(grade_text_elem)
                    print(f"  Found grade with URL: {grade_text_elem} -> {grade_url}")

            # Fallback: search for grade patterns in all elements
            for selector in grade_selectors:
                elements = soup.select(selector)
                print(f"Found {len(elements)} elements with selector: {selector}")

                for element in elements:
                    grade_text = element.get_text().strip()
                    if grade_text and len(grade_text) < 20 and grade_text not in found_grades:
                        # Look for grade patterns like 11A, 11B, 13A, etc.
                        if (re.match(r'^\d{1,2}[A-Z]$', grade_text) or  # 11A, 12B pattern
                            any(keyword in grade_text.lower() for keyword in ['open', 'junior', 'senior']) or
                            re.match(r'^u\d{1,2}', grade_text.lower())):  # U11, U13 pattern

                            # Try to find associated Select link nearby
                            grade_url = None
                            parent = element.parent
                            if parent:
                                select_link = parent.find('a', string=re.compile(r'Select', re.IGNORECASE))
                                if select_link:
                                    grade_url = select_link.get('href', '')
                                    if grade_url and not grade_url.startswith('http'):
                                        grade_url = urljoin(url, grade_url)

                            gender, age_group = self.parse_gender_age_from_grade(grade_text)
                            grades.append({
                                'name': grade_text,
                                'gender': gender,
                                'age_group': age_group,
                                'url': grade_url
                            })
                            found_grades.add(grade_text)
                            print(f"  Found grade: {grade_text} -> {grade_url or 'No URL'}")

            if not grades:
                print("No grades found with standard selectors, trying broader search...")
                all_links = soup.find_all('a', href=True)
                for link in all_links:
                    href = link.get('href', '')
                    grade_text = link.get_text().strip()
                    if (('grade' in href or 'division' in href) and
                        grade_text and len(grade_text) < 100 and
                        grade_text not in found_grades):
                        gender, age_group = self.parse_gender_age_from_grade(grade_text)
                        grades.append({
                            'name': grade_text,
                            'gender': gender,
                            'age_group': age_group
                        })
                        found_grades.add(grade_text)

                # Also try to find all Select/View buttons and match them to nearby grade text
                select_patterns = [
                    soup.find_all('a', string=re.compile(r'Select', re.IGNORECASE)),
                    soup.find_all('a', string=re.compile(r'View', re.IGNORECASE)),
                    soup.find_all('button', string=re.compile(r'Select', re.IGNORECASE)),
                    soup.find_all('a', href=re.compile(r'/grade/')),
                    soup.find_all('a', href=re.compile(r'/division/'))
                ]

                all_select_buttons = []
                for pattern_results in select_patterns:
                    all_select_buttons.extend(pattern_results)

                # Remove duplicates
                all_select_buttons = list(set(all_select_buttons))
                print(f"Found {len(all_select_buttons)} Select/View/Grade buttons")

                for select_btn in all_select_buttons:
                    select_url = select_btn.get('href', '')
                    if select_url and not select_url.startswith('http'):
                        select_url = urljoin(url, select_url)

                    # Look for grade text near this Select button
                    parent_elem = select_btn.parent
                    if parent_elem:
                        # Check parent and grandparent elements for grade text
                        for check_elem in [parent_elem, parent_elem.parent if parent_elem.parent else None]:
                            if check_elem:
                                elem_text = check_elem.get_text()
                                grade_matches = re.findall(r'\b(?:\d{1,2}[A-Z]|Cadet\s\d+|Inters\s\d+|U1[23])\b', elem_text)
                                for grade_match in grade_matches:
                                    if grade_match not in found_grades:
                                        gender, age_group = self.parse_gender_age_from_grade(grade_match)
                                        grades.append({
                                            'name': grade_match,
                                            'gender': gender,
                                            'age_group': age_group,
                                            'url': select_url
                                        })
                                        found_grades.add(grade_match)
                                        print(f"  Matched Select button to grade: {grade_match} -> {select_url}")

                # Search through all text elements for grade patterns
                all_text_elements = soup.find_all(string=True)
                for text in all_text_elements:
                    text_clean = text.strip()
                    if text_clean and len(text_clean) < 20 and text_clean not in found_grades:
                        # Look for specific grade patterns
                        if (re.match(r'^\d{1,2}[A-Z]$', text_clean) or  # 11A, 12B pattern
                            re.match(r'^[A-Z]\d{1,2}[A-Z]?$', text_clean) or  # A11, B12A pattern
                            re.match(r'^Cadet\s\d+$', text_clean) or  # Cadet 1, Cadet 2, etc.
                            re.match(r'^Inters\s\d+$', text_clean) or  # Inters 1, Inters 2, etc.
                            text_clean.lower() in ['open']):  # Open grades

                            # Try to find Select button near this text
                            grade_url = None
                            text_parent = text.parent if hasattr(text, 'parent') else None
                            if text_parent:
                                # Look for Select link in same row/parent
                                select_link = text_parent.find('a', string=re.compile(r'Select', re.IGNORECASE))
                                if not select_link and text_parent.parent:
                                    select_link = text_parent.parent.find('a', string=re.compile(r'Select', re.IGNORECASE))

                                if select_link:
                                    grade_url = select_link.get('href', '')
                                    if grade_url and not grade_url.startswith('http'):
                                        grade_url = urljoin(url, grade_url)

                            gender, age_group = self.parse_gender_age_from_grade(text_clean)
                            grades.append({
                                'name': text_clean,
                                'gender': gender,
                                'age_group': age_group,
                                'url': grade_url
                            })
                            found_grades.add(text_clean)
                            print(f"  Found grade in text: {text_clean} -> {grade_url or 'No URL'}")

                # Also check all elements with specific patterns
                all_elements = soup.find_all()
                for element in all_elements:
                    element_text = element.get_text().strip()
                    if element_text and len(element_text) < 20 and element_text not in found_grades:
                        if re.match(r'^\d{1,2}[A-Z]$', element_text):  # 11A, 12B pattern
                            gender, age_group = self.parse_gender_age_from_grade(element_text)
                            grades.append({
                                'name': element_text,
                                'gender': gender,
                                'age_group': age_group
                            })
                            found_grades.add(element_text)
                            print(f"  Found grade in element {element.name}: {element_text}")

            print(f"Total grades found: {len(grades)}")
            for grade in grades[:5]:
                print(f"  - {grade['name']}")

            return {
                'url': url,
                'competition_name': competition_name,
                'organization': organization,
                'grades': grades
            }

        except Exception as e:
            print(f"Error scraping PlayHQ competition {url}: {e}")
            return None

    def scrape_playhq_competition(self, url: str, delay: float = 1.0) -> Optional[Dict]:
        """Synchronous wrapper for async scraping method"""
        return asyncio.run(self.scrape_playhq_competition_async(url, delay))

    async def navigate_to_grade_page_async(self, grade_url: str, grade_name: str) -> bool:
        """Navigate to a specific grade page and take a screenshot"""
        try:
            if not grade_url:
                print(f"No URL available for grade: {grade_name}")
                return False

            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()

                await page.set_extra_http_headers({
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
                })

                await page.goto(grade_url, wait_until='domcontentloaded', timeout=30000)
                await page.wait_for_timeout(3000)

                # Try to dismiss any banner
                try:
                    close_button = await page.query_selector('button[aria-label="Close App Banner"]')
                    if close_button:
                        await close_button.click()
                        await page.wait_for_timeout(1000)
                except:
                    pass

                await page.wait_for_timeout(2000)

                # Take screenshot
                screenshot_path = f"grade_{grade_name.replace(' ', '_')}_{int(time.time())}.png"
                await page.screenshot(path=screenshot_path, full_page=True)
                print(f"Screenshot of {grade_name} saved: {screenshot_path}")

                await browser.close()
                return True

        except Exception as e:
            print(f"Error navigating to grade page {grade_name}: {e}")
            return False

    def navigate_to_grade_page(self, grade_url: str, grade_name: str) -> bool:
        """Synchronous wrapper for navigating to grade page"""
        return asyncio.run(self.navigate_to_grade_page_async(grade_url, grade_name))

    def save_competition_to_database(self, data: Dict):
        """Save competition data to SQLite database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        try:
            cursor.execute('SELECT id FROM competitions WHERE url = ?', (data['url'],))
            existing = cursor.fetchone()

            if existing:
                competition_id = existing[0]
                cursor.execute('''
                    UPDATE competitions
                    SET name = ?, organization = ?, scraped_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                ''', (data['competition_name'], data['organization'], competition_id))
            else:
                cursor.execute('''
                    INSERT INTO competitions (url, name, organization)
                    VALUES (?, ?, ?)
                ''', (data['url'], data['competition_name'], data['organization']))
                competition_id = cursor.lastrowid

            cursor.execute('DELETE FROM grades WHERE competition_id = ?', (competition_id,))

            for grade in data['grades']:
                cursor.execute('''
                    INSERT INTO grades (competition_id, name, gender, age_group, url)
                    VALUES (?, ?, ?, ?, ?)
                ''', (competition_id, grade['name'], grade['gender'], grade['age_group'], grade.get('url')))

            conn.commit()
            print(f"Saved competition: {data['competition_name']} with {len(data['grades'])} grades")

        except Exception as e:
            print(f"Error saving competition to database: {e}")
            conn.rollback()
        finally:
            conn.close()

    def scrape_competitions(self, urls: List[str], delay: float = 1.0):
        """Scrape multiple PlayHQ competition URLs and store in database"""
        for url in urls:
            print(f"Scraping competition: {url}")
            data = self.scrape_playhq_competition(url, delay)
            if data:
                self.save_competition_to_database(data)

    def get_competitions(self, limit: Optional[int] = None) -> List[Dict]:
        """Retrieve competition data from database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        query = '''
            SELECT c.url, c.name, c.organization, c.scraped_at,
                   COUNT(g.id) as grade_count
            FROM competitions c
            LEFT JOIN grades g ON c.id = g.competition_id
            GROUP BY c.id, c.url, c.name, c.organization, c.scraped_at
            ORDER BY c.scraped_at DESC
        '''
        if limit:
            query += f' LIMIT {limit}'

        cursor.execute(query)
        rows = cursor.fetchall()

        result = []
        for row in rows:
            result.append({
                'url': row[0],
                'name': row[1],
                'organization': row[2],
                'scraped_at': row[3],
                'grade_count': row[4]
            })

        conn.close()
        return result

    def get_grades_for_competition(self, competition_url: str) -> List[Dict]:
        """Get all grades for a specific competition"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute('''
            SELECT g.name, g.gender, g.age_group
            FROM grades g
            JOIN competitions c ON g.competition_id = c.id
            WHERE c.url = ?
            ORDER BY g.name
        ''', (competition_url,))

        rows = cursor.fetchall()
        result = []
        for row in rows:
            result.append({
                'name': row[0],
                'gender': row[1],
                'age_group': row[2]
            })

        conn.close()
        return result

    def close(self):
        """Close the session"""
        if not self.use_playwright and hasattr(self, 'session'):
            self.session.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Scrape PlayHQ competition data')
    parser.add_argument('url', help='PlayHQ competition URL to scrape')
    parser.add_argument('--db', default='playhq_competitions.db', help='Database file path')

    args = parser.parse_args()

    scraper = WebScraper(args.db)

    try:
        print(f"Scraping competition: {args.url}")
        scraper.scrape_competitions([args.url], delay=1.0)

        competitions = scraper.get_competitions(limit=1)
        if competitions:
            comp = competitions[0]
            print(f"\nCompetition: {comp['name']}")
            print(f"Organization: {comp['organization']}")
            print(f"Grade count: {comp['grade_count']}")

            grades = scraper.get_grades_for_competition(comp['url'])
            print(f"\nAll {len(grades)} grades:")
            for i, grade in enumerate(grades, 1):
                gender_str = f" ({grade['gender']})" if grade['gender'] else ""
                age_str = f" - {grade['age_group']}" if grade['age_group'] else ""
                print(f"{i:2d}. {grade['name']}{gender_str}{age_str}")

            # Navigate to 11A grade page if it exists
            grade_11a = next((g for g in grades if g['name'] == '11A'), None)
            if grade_11a:
                print(f"\nNavigating to 11A grade page...")
                # Get the URL from database
                conn = sqlite3.connect(scraper.db_path)
                cursor = conn.cursor()
                cursor.execute('SELECT url FROM grades WHERE name = ? AND competition_id IN (SELECT id FROM competitions WHERE url = ?)',
                             ('11A', comp['url']))
                result = cursor.fetchone()
                conn.close()

                if result and result[0]:
                    scraper.navigate_to_grade_page(result[0], '11A')
                else:
                    print("No URL found for 11A grade")

    finally:
        scraper.close()