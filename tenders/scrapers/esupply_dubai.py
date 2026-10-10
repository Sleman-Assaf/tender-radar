# tenders/scrapers/esupply_dubai.py
# Path: TenderRadar/tenders/scrapers/esupply_dubai.py
"""
eSupply Dubai -- Dubai Government Procurement Portal
https://esupply.dubai.gov.ae/esop/toolkit/negotiation/pubRfq/list.si

Flow:
  1. POST login to /esop/guest/login.do with credentials
  2. GET the RFQ list page (session cookie maintained)
  3. For each page, POST the form with page number to paginate
  4. Parse table rows: RFQ Code, Title, Project Code, Closing Date, Status, Buyer Org
  5. Build detail URL from rfqId extracted from the title link href
"""

import logging
import os
import re
from datetime import datetime
from typing import Optional

import requests
from bs4 import BeautifulSoup

from .base import BaseScraper, TenderData

logger = logging.getLogger('tenders')

BASE_URL  = 'https://esupply.dubai.gov.ae'
LOGIN_URL = f'{BASE_URL}/esop/guest/login.do'
LIST_URL   = f'{BASE_URL}/esop/toolkit/negotiation/pubRfq/list.si'
PUBLIC_URL = f'{BASE_URL}/esop/guest/go/public/opportunity/current?locale=en_GB&customLoginPage=/esupply/web/index.html'

# Credentials — set via Django settings or env vars
ESUPPLY_USERNAME = os.environ.get('ESUPPLY_USERNAME', '')
ESUPPLY_PASSWORD = os.environ.get('ESUPPLY_PASSWORD', '')

_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/147.0.0.0 Safari/537.36'
    ),
    'Accept':          'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate, br',
}

# Max pages to scrape per run (320 results / 10 per page = 32 pages)
MAX_PAGES = 32


class ESupplyDubaiScraper(BaseScraper):
    """Scraper for eSupply Dubai RFQ/RFPs Open to All Suppliers."""

    source_name  = 'eSupply Dubai -- Dubai Government Procurement Portal'
    country_code = 'AE'
    base_url     = BASE_URL

    def scrape(self) -> list[TenderData]:
        # Load credentials from Django settings if not in env
        username, password = self._get_credentials()
        if not username or not password:
            logger.error('[eSupply] No credentials configured — set ESUPPLY_USERNAME and ESUPPLY_PASSWORD')
            return []

        session = requests.Session()
        session.headers.update(_HEADERS)

        if not self._login(session, username, password):
            return []

        return self._scrape_all_pages(session)

    # ── Credentials ───────────────────────────────────────────────────────

    def _get_credentials(self):
        username = ESUPPLY_USERNAME
        password = ESUPPLY_PASSWORD
        if not username or not password:
            try:
                from django.conf import settings
                username = getattr(settings, 'ESUPPLY_USERNAME', username)
                password = getattr(settings, 'ESUPPLY_PASSWORD', password)
            except Exception:
                pass
        return username, password

    # ── Login ─────────────────────────────────────────────────────────────

    def _login(self, session: requests.Session, username: str, password: str) -> bool:
        """POST login form and verify session is established."""
        logger.info('[eSupply] Logging in...')
        data = {
            'login':           username,
            'password':        password,
            'loginNotOk':      '/esupply/web/error.html',
            'customLoginPage': '/esupply/web/index.html',
            'customGuest':     '',
        }
        try:
            r = session.post(
                LOGIN_URL, data=data,
                headers={'Referer': f'{BASE_URL}/esupply/web/index.html'},
                timeout=20, allow_redirects=True,
            )
            # Success: redirected to portal, not to error page
            if 'error.html' in r.url or 'loginNotOk' in r.url:
                logger.error('[eSupply] Login failed — check credentials')
                return False
            if r.status_code == 200 and ('toolkit' in r.url or 'home.do' in r.url or 'pubRfq' in r.url):
                logger.info('[eSupply] Login successful')
                return True
            # Some portals redirect to home after login
            if r.status_code in (200, 302):
                logger.info(f'[eSupply] Login response {r.status_code} at {r.url}')
                return True
            logger.error(f'[eSupply] Login failed — status {r.status_code}')
            return False
        except Exception as exc:
            logger.error(f'[eSupply] Login error: {exc}')
            return False

    # ── Pagination ────────────────────────────────────────────────────────

    def _scrape_all_pages(self, session: requests.Session) -> list[TenderData]:
        results = []

        # Page 1: GET
        logger.info('[eSupply] Fetching page 1')
        try:
            r = session.get(LIST_URL + '?reset=true', timeout=20)
            if r.status_code != 200:
                logger.error(f'[eSupply] List page returned {r.status_code}')
                return []
        except Exception as exc:
            logger.error(f'[eSupply] List page error: {exc}')
            return []

        soup = BeautifulSoup(r.text, 'html.parser')
        page_tenders = self._parse_table(soup)
        results.extend(page_tenders)
        logger.info(f'[eSupply] Page 1: {len(page_tenders)} tenders')

        # Detect total pages from pagination
        total_pages = self._detect_total_pages(soup)
        logger.info(f'[eSupply] Total pages: {total_pages}')

        # Pages 2..N: POST form with page number
        for page_num in range(2, min(total_pages + 1, MAX_PAGES + 1)):
            page_tenders = self._fetch_page(session, page_num)
            if not page_tenders and page_num > 2:
                logger.info(f'[eSupply] No results on page {page_num}, stopping')
                break
            results.extend(page_tenders)
            logger.info(f'[eSupply] Page {page_num}: {len(page_tenders)} tenders (total: {len(results)})')

        logger.info(f'[eSupply] Done — {len(results)} tenders')
        return results

    def _detect_total_pages(self, soup: BeautifulSoup) -> int:
        """Extract total page count from pagination buttons."""
        # Last page button: title="Go to the last page"
        last_btn = soup.find('button', title='Go to the last page')
        if last_btn:
            try:
                return int(last_btn.get_text(strip=True))
            except ValueError:
                pass
        # Fallback: count from "Showing Result X - Y of Z"
        counter = soup.select_one('.pagination-counter')
        if counter:
            m = re.search(r'of\s+(\d+)', counter.get_text())
            if m:
                total = int(m.group(1))
                return (total + 9) // 10  # ceil division by 10
        return 1

    def _fetch_page(self, session: requests.Session, page_num: int) -> list[TenderData]:
        """POST pagination form for page N."""
        data = {
            'rfqFilterManager-pubRfq.pagerComponent.page': str(page_num),
            'userAct':    'navigation',
            'filterEnabled': 'false',
        }
        try:
            r = session.post(
                LIST_URL, data=data,
                headers={'Referer': LIST_URL},
                timeout=20,
            )
            if r.status_code != 200:
                logger.warning(f'[eSupply] Page {page_num} returned {r.status_code}')
                return []
            soup = BeautifulSoup(r.text, 'html.parser')
            return self._parse_table(soup)
        except Exception as exc:
            logger.warning(f'[eSupply] Page {page_num} error: {exc}')
            return []

    # ── Parsing ───────────────────────────────────────────────────────────

    def _parse_table(self, soup: BeautifulSoup) -> list[TenderData]:
        """Parse the RFQ table rows from page HTML."""
        rows = soup.select('tbody.list-tbody tr')
        tenders = []
        for row in rows:
            td = self._parse_row(row)
            if td:
                tenders.append(td)
        return tenders

    def _parse_row(self, row) -> Optional[TenderData]:
        """Parse a single table row into TenderData."""
        # RFQ/RFP Code
        code_td = row.select_one('td.col_REFERENCE_CODE')
        rfq_code = code_td.get_text(strip=True) if code_td else ''

        # Title + detail link
        title_td = row.select_one('td.col_TITLE')
        if not title_td:
            return None
        title_a = title_td.find('a')
        if not title_a:
            return None
        title = title_a.get('title', '').replace('View Details: ', '').strip()
        if not title:
            title = title_a.get_text(strip=True)
        if not title or len(title) < 3:
            return None

        # Extract detail URL from 2nd arg of contractPayment('rfq_X', '/esop/.../initDetailRfq.do?rfqId=rfq_X')
        # The 1st arg triggers a payment check that redirects to /epay5_summary.jsp — we want the 2nd arg directly.
        href = title_a.get('href', '')
        # Build the public payment/summary page URL — accessible without login.
        # Pattern: /esop/uae-host/public/esupply/payment/epay5_summary.jsp?esop_object=RFQ&rfq_id=rfq_XXXXX
        # The first arg of contractPayment() is the rfq_id used for this URL.
        rfq_pay_m  = re.search(r"contractPayment\('(rfq_[^']+)'", href)
        rfq_id     = rfq_pay_m.group(1) if rfq_pay_m else rfq_code
        if rfq_id:
            detail_url = (
                f"{BASE_URL}/esop/uae-host/public/esupply/payment/epay5_summary.jsp"
                f"?esop_object=RFQ&rfq_id={rfq_id}"
            )
        else:
            detail_url = PUBLIC_URL

        # Project Code
        project_td = row.select_one('td.col_TENDER_CODE')
        project_code = project_td.get_text(strip=True) if project_td else ''

        # Closing date/time
        date_td = row.select_one('td.col_INTEREST_TIME_LIMIT')
        close_date = self._parse_date(date_td.get_text(strip=True) if date_td else '')

        # Status
        status_td = row.select_one('td.col_STATE')
        raw_status = status_td.get_text(strip=True).lower() if status_td else ''
        if 'running' in raw_status or 'open' in raw_status or 'active' in raw_status:
            status = 'OPEN'
        elif 'closed' in raw_status or 'expired' in raw_status:
            status = 'CLOSED'
        else:
            status = 'OPEN'

        # Buyer Organisation (entity)
        entity_td = row.select_one('td.col_COMPANY_NAME')
        entity = entity_td.get_text(strip=True) if entity_td else 'Dubai Government'

        return TenderData(
            title               = title[:300],
            source_url          = detail_url,
            external_id         = rfq_id or rfq_code,
            reference_no        = rfq_code[:100],
            entity              = entity[:200],
            entity_ar           = '',
            description         = project_code,
            category            = self._map_category(title),
            status              = status,
            currency            = 'AED',
            publication_date    = None,
            submission_deadline = close_date,
            country_code        = 'AE',
        )

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _map_category(title: str) -> str:
        t = title.lower()
        if any(w in t for w in ('consult', 'advisory', 'study', 'supervision', 'feasibility')):
            return 'CONSULTANCY'
        if any(w in t for w in ('construction', 'installation', 'civil', 'works', 'rehabilitation', 'maintenance')):
            return 'WORKS'
        if any(w in t for w in ('supply', 'equipment', 'material', 'vehicle', 'spare', 'purchase')):
            return 'GOODS'
        return 'SERVICES'

    @staticmethod
    def _parse_date(val: str) -> Optional[datetime.date]:
        if not val:
            return None
        # Format: 06/05/2026 07:00
        val = val.strip().split()[0]  # take date part only
        for fmt in ('%d/%m/%Y', '%Y-%m-%d', '%d-%b-%Y'):
            try:
                return datetime.strptime(val, fmt).date()
            except ValueError:
                continue
        return None
