# tenders/scrapers/mca_jordan.py
"""
MCA-Jordan — Tenders Announcements
URL: http://www.mca-jordan.gov.jo/page/Default.aspx?page=Tenders_Announcements

What is this?
-------------
MCA-Jordan (Millennium Challenge Account – Jordan) is the Jordanian entity
established to implement the Jordan Compact under the Millennium Challenge
Corporation (MCC) programme — a $275M US-funded infrastructure compact focused
on water and wastewater in the Zarqa governorate.

The tenders announcements page is served as plain server-side HTML by an
ASP.NET WebForms application. No JavaScript rendering required.

HTML structure (confirmed from live page):
    #ctl00_ContentPlaceHolder1_lblContents
      table.MsoNormalTable
        thead tr   → headers (Number | Title | Date | Last Date | Documents | Addendum | Attachments)
        tbody tr   → one row per tender (may span multiple <tr> for attachments)

Column mapping (by td index):
    td[0] → Reference number (text + optional PDF link)
             e.g. "146/2016/MCC", "(21/2016)-MCC", "GPN No10"
    td[1] → Title (text + optional PDF link)
    td[2] → Publication date — formats: "1/8/2016", "20/01/2016", "31\\1\\2016", "0/0/0"
    td[3] → Deadline (Last Date) — same formats
    td[4] → Documents link text / label
    td[5] → Addendum
    td[6] → Attachments

Row de-duplication: Some entries span two consecutive <tr> elements with the
same Number and Title (rowspan pattern). We skip rows where td[0] is empty
or duplicates the previous reference.

Date quirks:
  - "0/0/0" means no date — treat as None
  - Backslash separators: "31\\1\\2016" → parse as d/m/y
  - Both D/M/YYYY and DD/MM/YYYY appear

NOTE: As of June 2026 this page shows only historical 2016 entries —
the MCA-Jordan compact concluded in 2017. The scraper is included for
completeness and to capture any future re-activation.

Base URL for relative hrefs: http://www.mca-jordan.gov.jo
"""

import logging
import re
from datetime import datetime, date
from typing import Optional

from .base import BaseScraper, TenderData

logger = logging.getLogger('tenders')

BASE_URL  = 'http://www.mca-jordan.gov.jo'
LIST_URL  = f'{BASE_URL}/page/Default.aspx?page=Tenders_Announcements'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept':          'text/html,application/xhtml+xml,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer':         BASE_URL,
}


class MCAJordanScraper(BaseScraper):
    """
    Scraper for MCA-Jordan Tenders Announcements.

    Parses a static HTML table from an ASP.NET WebForms page.
    No pagination — all tenders on a single page.
    No JavaScript rendering required.

    Row deduplication: rows where td[0] is empty (rowspan continuation)
    are skipped. Duplicate reference numbers are also skipped.
    """

    country_code = 'JO'
    source_name  = 'MCA-Jordan — Tenders'
    base_url     = BASE_URL

    def scrape(self) -> list[TenderData]:
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            print('  X  [MCAJordan] beautifulsoup4 not installed')
            return []

        from tenders.models import ScraperSource
        try:
            source = ScraperSource.objects.get(
                scraper_class='MCAJordanScraper', is_active=True,
            )
        except ScraperSource.DoesNotExist:
            print('  X  [MCAJordan] No active ScraperSource — run seed_data')
            return []
        except ScraperSource.MultipleObjectsReturned:
            source = ScraperSource.objects.filter(
                scraper_class='MCAJordanScraper', is_active=True
            ).first()

        self.session.headers.update(HEADERS)

        rel_src, cat_src = self._reload_keywords_from_db()
        rel_count = len(self._relevance_keywords)
        cat_count = sum(len(v) for v in self._category_keywords.values())

        print(f'\n{"_" * 62}')
        print(f'  SOURCE  : {source.name}')
        print(f'  SCRAPER : MCAJordanScraper')
        print(f'  MODE    : HTML scraping (ASP.NET static table)')
        print(f'  URL     : {LIST_URL}')
        print(f'  Keywords: {rel_count} relevance [{rel_src}]'
              f'  |  {cat_count} category [{cat_src}]')
        print(f'{"_" * 62}')

        # ── Fetch page ────────────────────────────────────────────────────
        try:
            resp = self.session.get(LIST_URL, timeout=self.timeout)
            resp.raise_for_status()
        except Exception as exc:
            err = str(exc)
            if '403' in err or '401' in err:
                print('  X  [MCAJordan] Blocked by WAF — server IP not whitelisted')
                logger.warning(f'[MCAJordan] WAF block: {exc}')
            else:
                print(f'  X  [MCAJordan] ERROR: {exc}')
                logger.error(f'[MCAJordan] fetch error: {exc}')
            return []

        soup = BeautifulSoup(resp.text, 'html.parser')

        # ── Find tenders table ────────────────────────────────────────────
        content = soup.select_one('#ctl00_ContentPlaceHolder1_lblContents')
        if not content:
            # Fallback: try InnerContent_Page
            content = soup.select_one('.InnerContent_Page')
        if not content:
            content = soup

        tables = content.select('table.MsoNormalTable') if content else []
        if not tables:
            tables = content.select('table') if content else []

        if not tables:
            print('  X  [MCAJordan] No table found in page content')
            logger.error('[MCAJordan] table not found')
            return []

        table = tables[0]
        rows  = table.select('tbody tr')

        if not rows:
            rows = table.select('tr')

        print(f'       > rows found: {len(rows)}')

        if rows:
            # Debug first data row
            first_data = None
            for row in rows:
                cells = row.select('td')
                if len(cells) >= 4 and cells[0].get_text(strip=True):
                    first_data = cells
                    break
            if first_data:
                print(f'  [DEBUG] ref   : {first_data[0].get_text(strip=True)[:40]}')
                print(f'  [DEBUG] title : {first_data[1].get_text(strip=True)[:60]}')
                print(f'  [DEBUG] date  : {first_data[2].get_text(strip=True)}')
                print(f'  [DEBUG] last  : {first_data[3].get_text(strip=True)}')
                print()

        # ── Parse rows ────────────────────────────────────────────────────
        results:     list[TenderData] = []
        seen_refs:   set[str]        = set()
        today        = date.today()

        for row in rows:
            cells = row.select('td')
            if len(cells) < 4:
                continue

            ref_text   = ' '.join(cells[0].get_text(strip=True).split())
            title_text = ' '.join(cells[1].get_text(strip=True).split())

            # Skip header row and empty rows
            if not ref_text or ref_text.lower() in ('number', ''):
                continue
            # Skip non-breaking space only rows
            if ref_text in ('\xa0', '&nbsp;') or not any(c.isalnum() for c in ref_text):
                continue

            # De-duplicate (rowspan continuations have same ref)
            ref_key = ref_text.strip().lower()
            if ref_key in seen_refs:
                continue
            seen_refs.add(ref_key)

            if not title_text or not any(c.isalnum() for c in title_text):
                continue

            date_text = cells[2].get_text(strip=True) if len(cells) > 2 else ''
            last_text = cells[3].get_text(strip=True) if len(cells) > 3 else ''

            pub_date = self._parse_mca_date(date_text)
            deadline = self._parse_mca_date(last_text)

            # Source URL: prefer link from ref cell, then title cell
            href = ''
            for cell in (cells[0], cells[1]):
                a = cell.select_one('a[href]')
                if a:
                    href = a['href'].strip()
                    break

            if href:
                if href.startswith('http'):
                    source_url = href
                else:
                    source_url = f'{BASE_URL}/{href.lstrip("/")}'
            else:
                source_url = LIST_URL

            # Status: if deadline is past → CLOSED; if no date → CLOSED
            # (these are all historical entries from 2016)
            if deadline and deadline >= today:
                status = 'OPEN'
                save_deadline = deadline
            else:
                status = 'CLOSED'
                # Pass None so save_tender_data doesn't skip past-deadline entries.
                # The status=CLOSED already communicates the correct state to the UI.
                save_deadline = None

            combined = f'{title_text} {ref_text}'

            # MCA-Jordan is a small, curated Jordan infrastructure source
            # — accept all entries without relevance filtering
            results.append(TenderData(
                title               = title_text,
                title_ar            = title_text,
                source_url          = source_url,
                external_id         = ref_text,
                reference_no        = ref_text,
                entity              = 'MCA-Jordan (Millennium Challenge Account)',
                entity_ar           = 'حساب تحدي الألفية — الأردن',
                description         = (
                    f'Ref: {ref_text} | Posted: {date_text} | Deadline: {last_text}'
                ),
                category            = self.detect_category(combined),
                status              = status,
                currency            = 'USD',
                estimated_value     = None,
                publication_date    = pub_date,
                submission_deadline = save_deadline,
                opening_date        = None,
            ))

        print(f'       > {len(results)} relevant out of {len(seen_refs)} unique entries')
        print(f'\n  {"=" * 58}')
        print(f'  TOTAL : {len(results)} relevant MCA-Jordan tender(s)')
        print(f'  {"=" * 58}\n')
        logger.info(f'[MCAJordan] complete — total={len(results)}')
        return results

    # ── Date parsing ──────────────────────────────────────────────────────

    @staticmethod
    def _parse_mca_date(val: str) -> Optional[date]:
        """
        Parse MCA-Jordan date formats:
          "1/8/2016"     → 1 Aug 2016   (D/M/YYYY)
          "22/8/2016"    → 22 Aug 2016
          "20/01/2016"   → 20 Jan 2016
          "31\\1\\2016"  → 31 Jan 2016  (backslash separator)
          "0/0/0"        → None
          ""             → None
        """
        if not val:
            return None
        val = val.strip().replace('\\', '/').replace('\xa0', '').strip()
        if not val or val in ('0/0/0', '0\\0\\0', '-'):
            return None
        # Try various d/m/y formats
        for fmt in ('%d/%m/%Y', '%-d/%-m/%Y', '%d/%m/%y', '%d-%m-%Y',
                    '%Y-%m-%d', '%m/%d/%Y'):
            try:
                return datetime.strptime(val, fmt).date()
            except ValueError:
                continue
        # Try splitting manually for D/M/YYYY
        parts = re.split(r'[/\\-]', val)
        if len(parts) == 3:
            try:
                d, m, y = int(parts[0]), int(parts[1]), int(parts[2])
                if y < 100:
                    y += 2000
                return date(y, m, d)
            except (ValueError, TypeError):
                pass
        return None
