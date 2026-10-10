# tenders/scrapers/worldbank_rfx.py
"""
World Bank RFxNow Scraper
Source: https://wbgeprocure-rfxnow.worldbank.org/rfxnow/public/advertisement/index.html

Platform: RFxNow (Backbone.js SPA by C20G)

The public page renders advertisements via Backbone.js — the HTML source is empty,
the browser executes JS which calls the REST API and populates a Backgrid table.
The API returns {} when called directly without a browser session (JSESSIONID).

Strategy: Use Playwright (headless Chromium) to:
  1. Load the public page — JS runs, gets session cookie, calls API
  2. Intercept the API response to get the raw JSON data
  3. Parse the JSON into TenderData objects

Fallback: If interception fails, parse the rendered <table> HTML.

Requirements:
    pip install playwright
    playwright install chromium
"""

import logging
import re
from datetime import datetime
from typing import Optional

from .base import BaseScraper, TenderData

logger = logging.getLogger("tenders")

BASE_URL    = "https://wbgeprocure-rfxnow.worldbank.org"
PUBLIC_PAGE = f"{BASE_URL}/rfxnow/public/advertisement/index.html"
DETAIL_BASE = f"{BASE_URL}/rfxnow/public/advertisement/details.html"

# Substrings matched against API response URLs for interception
API_PATH_PATTERNS = [
    "advertisement/active",
    "advertisement/activePublic",
    "advertisement/publicActive",
    "advertisement/list",
    "advertisement/search",
    "/api/advertisement",
]

PAGE_TIMEOUT  = 30_000   # ms
GRID_SELECTOR = "#advertisementsGrid table"


class WorldBankRFxScraper(BaseScraper):
    """
    Playwright-based scraper for World Bank RFxNow eProcurement.
    Uses headless Chromium to render the JS SPA and intercept the API response.
    Falls back to parsing the rendered HTML table if interception fails.
    """

    country_code = "WB"
    source_name  = "WorldBankRFx"
    base_url     = BASE_URL

    def scrape(self) -> list[TenderData]:
        from tenders.models import ScraperSource

        try:
            source = ScraperSource.objects.get(
                scraper_class="WorldBankRFxScraper", is_active=True
            )
        except ScraperSource.DoesNotExist:
            print("  x  [WorldBankRFx] ScraperSource not configured - run seed_data")
            return []
        except ScraperSource.MultipleObjectsReturned:
            source = ScraperSource.objects.filter(
                scraper_class="WorldBankRFxScraper", is_active=True
            ).first()

        rel_src, cat_src = self._reload_keywords_from_db()
        rel_count = len(self._relevance_keywords)
        cat_count = sum(len(v) for v in self._category_keywords.values())

        print(f'\n{"─" * 62}')
        print(f"  SOURCE  : {source.name}")
        print(f"  SCRAPER : WorldBankRFxScraper  (Playwright/Chromium)")
        print(f"  PAGE    : {PUBLIC_PAGE}")
        print(f"  Keywords: {rel_count} relevance [{rel_src}]"
              f"  |  {cat_count} category [{cat_src}]")
        print(f'{"─" * 62}')

        items = self._scrape_with_playwright()

        if not items:
            print("  x  No advertisements found.")
            logger.error("[WorldBankRFx] scrape returned 0 items")
            return []

        print(f"\n  [DEBUG] Total advertisements: {len(items)}")
        s = items[0]
        print(f"  [DEBUG] Sample title   : {s.title[:80]!r}")
        print(f"  [DEBUG] Sample ref_no  : {s.reference_no!r}")
        print(f"  [DEBUG] Sample pub_date: {s.publication_date}")
        print(f"  [DEBUG] Sample deadline: {s.submission_deadline}")

        results: list[TenderData] = []
        for t in items:
            combined = f"{t.title} {t.description or ''} {t.entity or ''}"
            if self.detect_relevance(combined):
                results.append(t)

        print(f'\n  {"═" * 58}')
        print(f"  TOTAL : {len(results)} relevant tender(s)  <-  WorldBankRFx")
        print(f'  {"═" * 58}\n')
        logger.info(f"[WorldBankRFx] scrape() complete - total={len(results)}")
        return results

    # ── Playwright ────────────────────────────────────────────────────────────

    def _scrape_with_playwright(self) -> list[TenderData]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            print("  x  Playwright not installed.")
            print("     Run: pip install playwright && playwright install chromium")
            logger.error("[WorldBankRFx] Playwright not installed")
            return []

        intercepted: list[list] = []   # holds raw JSON list from API

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox"]
            )
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                )
            )
            page = context.new_page()

            def on_response(resp):
                url = resp.url
                if not any(pat in url for pat in API_PATH_PATTERNS):
                    return
                if resp.status != 200:
                    return
                try:
                    body = resp.json()
                    if isinstance(body, list) and body:
                        print(f"\n  [API] list({len(body)}) from {url}")
                        intercepted.append(body)
                    elif isinstance(body, dict) and body:
                        for key in ("advertisements", "data", "results",
                                    "items", "content", "records"):
                            if key in body and isinstance(body[key], list) and body[key]:
                                print(f"\n  [API] dict[{key!r}]({len(body[key])}) from {url}")
                                intercepted.append(body[key])
                                break
                except Exception:
                    pass

            page.on("response", on_response)

            print(f"       Loading page...", end="  ", flush=True)
            try:
                page.goto(PUBLIC_PAGE, wait_until="networkidle", timeout=PAGE_TIMEOUT)
                print("OK")
            except Exception as exc:
                print(f"WARN: {exc} — trying to parse anyway")
                logger.warning(f"[WorldBankRFx] Page load timeout: {exc}")

            # Extra wait for deferred Backbone fetch
            try:
                page.wait_for_selector(GRID_SELECTOR, timeout=10_000)
                print("       Grid table rendered OK")
            except Exception:
                print("       Grid selector timeout — continuing")

            if intercepted:
                data = intercepted[-1]   # use most recent API response
                browser.close()
                print(f"       Using intercepted API data ({len(data)} items)")
                return self._parse_json(data)

            # Fallback: parse rendered HTML
            print("       No API response intercepted — parsing rendered HTML")
            html = page.content()
            browser.close()

        from bs4 import BeautifulSoup
        return self._parse_html_table(BeautifulSoup(html, "html.parser"))

    # ── JSON parser ───────────────────────────────────────────────────────────

    def _parse_json(self, data: list) -> list[TenderData]:
        items: list[TenderData] = []
        for n in data:
            if not isinstance(n, dict):
                continue
            notice_id = str(n.get("id") or n.get("advertisementId") or "").strip()
            if not notice_id:
                continue
            title = (
                n.get("advertisementTitle") or n.get("title") or n.get("name") or ""
            ).strip()
            if not title or len(title) < 3:
                continue

            ref_no     = str(n.get("referenceNumber") or n.get("refNo") or notice_id).strip()
            entity     = str(n.get("organizationName") or n.get("organisation") or
                             n.get("department") or "World Bank Group").strip()
            notice_type = str(n.get("advertisementType") or n.get("rfxType") or "").strip()

            deadline = self._parse_rfx_date(
                n.get("closingDate") or n.get("closingDateTime") or
                n.get("submissionDate") or n.get("dueDate") or ""
            )
            pub_date = self._parse_rfx_date(
                n.get("openDate") or n.get("publishDate") or n.get("postedDate") or ""
            )

            desc_raw = str(n.get("description") or n.get("scope") or "").strip()
            if "<" in desc_raw:
                try:
                    from bs4 import BeautifulSoup as _BS
                    desc_raw = _BS(desc_raw, "html.parser").get_text(" ", strip=True)
                except Exception:
                    pass
            description = (desc_raw or title)[:1000]

            cats_raw = n.get("categories") or n.get("category") or []
            if isinstance(cats_raw, list):
                cat_text = " ".join(
                    str(c.get("categoryName") or c.get("name") or "")
                    for c in cats_raw if isinstance(c, dict)
                )
            else:
                cat_text = str(cats_raw)

            category   = self.detect_category(f"{title} {notice_type} {cat_text}")
            raw_status = str(n.get("status") or n.get("advertisementStatus") or "").upper()
            status     = "CLOSED" if raw_status in ("CLOSED", "CANCELLED", "AWARDED") else "OPEN"

            items.append(TenderData(
                title               = title[:300],
                source_url          = f"{DETAIL_BASE}?id={notice_id}",
                external_id         = notice_id,
                reference_no        = ref_no[:200],
                description         = description,
                entity              = entity[:200],
                category            = category,
                status              = status,
                currency            = "USD",
                publication_date    = pub_date,
                submission_deadline = deadline,
                country_code        = "WB",
            ))
        return items

    # ── HTML table parser (fallback) ──────────────────────────────────────────

    def _parse_html_table(self, soup) -> list[TenderData]:
        """
        Parse the Backgrid-rendered table from #advertisementsGrid.

        Confirmed column layout from live screenshot:
            col 0: Procurement Number  (ref_no + external_id)
            col 1: Procurement         (title)
            col 2: Publication Date    ("4/14/2026 12:00 AM EDT")
            col 3: EOI Deadline        ("4/27/2026 11:59 PM EDT")
        """
        grid = soup.select_one("#advertisementsGrid")
        if not grid:
            print("  x  #advertisementsGrid not found in rendered HTML")
            return []

        rows = grid.select("table tbody tr")
        print(f"  [HTML] {len(rows)} table rows found")
        if not rows:
            return []

        headers = [th.get_text(strip=True) for th in grid.select("table thead th")]
        print(f"  [HTML] Columns: {headers}")

        def col_text(row, idx):
            cells = row.find_all("td")
            return cells[idx].get_text(" ", strip=True) if idx < len(cells) else ""

        # Map confirmed WB RFxNow headers to column indices.
        # Headers: [Procurement Number, Procurement, Publication Date, EOI Deadline]
        # Use longest-keyword-first to avoid "procurement" matching "Procurement Number"
        headers_lower = [h.lower() for h in headers]
        taken = set()

        def find_col(keywords, default):
            for kw in sorted(keywords, key=len, reverse=True):
                for ci, h in enumerate(headers_lower):
                    if ci not in taken and kw in h:
                        taken.add(ci)
                        return ci
            if default not in taken:
                taken.add(default)
            return default

        idx_ref  = find_col(["procurement number", "ref no", "number"], 0)
        idx_title= find_col(["procurement", "title", "description"],     1)
        idx_pub  = find_col(["publication", "publish", "open date"],     2)
        idx_dead = find_col(["deadline", "eoi", "closing", "due"],       3)

        items: list[TenderData] = []
        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 2:
                continue

            ref_no = col_text(row, idx_ref).strip()
            title  = col_text(row, idx_title).strip()
            if not title or len(title) < 3:
                continue

            pub_date = self._parse_rfx_date(col_text(row, idx_pub))
            deadline = self._parse_rfx_date(col_text(row, idx_dead))

            # Detail URL: look for <a> in row, else build from ref_no
            link       = row.find("a", href=True)
            if link:
                href = link["href"]
                source_url = f"{BASE_URL}{href}" if href.startswith("/") else href
            else:
                source_url = f"{DETAIL_BASE}?id={ref_no}" if ref_no else PUBLIC_PAGE

            id_match  = re.search(r"[?&]id=([^&]+)", source_url)
            notice_id = id_match.group(1) if id_match else ref_no or title[:40]

            items.append(TenderData(
                title               = title[:300],
                source_url          = source_url,
                external_id         = notice_id,
                reference_no        = ref_no[:200],
                description         = title,
                entity              = "World Bank Group",
                category            = self.detect_category(title),
                status              = "OPEN",
                currency            = "USD",
                publication_date    = pub_date,
                submission_deadline = deadline,
                country_code        = "WB",
            ))
        return items

    # ── Date parser ───────────────────────────────────────────────────────────

    @staticmethod
    def _parse_rfx_date(text) -> Optional[datetime.date]:
        """
        Parse RFxNow WB date formats:
            "4/14/2026 12:00 AM EDT"   <- WB RFxNow table format
            "4/27/2026 11:59 PM EDT"   <- EOI deadline format
            "2026-05-15T00:00:00"      <- ISO datetime
            "2026-05-15"               <- ISO date only
        """
        if not text:
            return None
        if isinstance(text, (int, float)):
            try:
                return datetime.fromtimestamp(text / 1000).date()
            except Exception:
                return None
        # Strip timezone abbreviation (EDT, EST, UTC, etc.)
        import re as _re
        val = _re.sub(r"\s+[A-Z]{2,4}$", "", str(text).strip()).strip()
        if not val:
            return None
        for fmt, length in [
            # WB RFxNow table: "4/14/2026 12:00 AM"  (after stripping TZ)
            ("%m/%d/%Y %I:%M %p", None),
            # ISO with milliseconds + Z
            ("%Y-%m-%dT%H:%M:%S.%fZ", None),
            # ISO + Z
            ("%Y-%m-%dT%H:%M:%SZ",    20),
            # ISO no Z
            ("%Y-%m-%dT%H:%M:%S",     19),
            # ISO date only
            ("%Y-%m-%d",              10),
            # "15-Apr-2026"
            ("%d-%b-%Y",              11),
            # US date only "4/14/2026"
            ("%m/%d/%Y",              10),
            # EU date only
            ("%d/%m/%Y",              10),
        ]:
            try:
                sliced = val[:length] if length else val
                return datetime.strptime(sliced, fmt).date()
            except ValueError:
                continue
        return None
