# tenders/scrapers/dewa.py
# Path: TenderRadar/tenders/scrapers/dewa.py
"""
DEWA -- Dubai Electricity & Water Authority
https://www.dewa.gov.ae/en/supplier/services/list-of-tender-documents

Flow:
  1. GET list page with a requests.Session -> establishes ASP.NET_SessionId cookie
     + extracts per-tender CSRF tokens from HTML
  2. For each tender, POST tenderNumber + __RequestVerificationToken
     using the SAME session (cookies carried automatically)
     -> server returns PDF as 200 + content-disposition: attachment
  3. Save PDF to MEDIA_ROOT/dewa_ads/<tender_no>.pdf and store the media URL
  4. Playwright fallback if requests are blocked
"""

import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

from .base import BaseScraper, TenderData

logger = logging.getLogger('tenders')

BASE_URL = 'https://www.dewa.gov.ae'
LIST_URL = f'{BASE_URL}/en/supplier/services/list-of-tender-documents'
POST_URL = f'{BASE_URL}/api/sitecore/TenderDocumentList/ViewAttachment'

_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/147.0.0.0 Safari/537.36'
    ),
    'Accept':          'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate, br',
    'Referer':         LIST_URL,
    'Origin':          BASE_URL,
    'sec-fetch-dest':  'document',
    'sec-fetch-mode':  'navigate',
    'sec-fetch-site':  'same-origin',
    'sec-fetch-user':  '?1',
    'upgrade-insecure-requests': '1',
}


def _get_media_dir() -> Path:
    try:
        from django.conf import settings
        base = Path(settings.MEDIA_ROOT)
    except Exception:
        base = Path('media')
    d = base / 'dewa_ads'
    d.mkdir(parents=True, exist_ok=True)
    return d


def _get_media_url(filename: str) -> str:
    try:
        from django.conf import settings
        media_url = getattr(settings, 'MEDIA_URL', '/media/')
    except Exception:
        media_url = '/media/'
    return f'{media_url}dewa_ads/{filename}'


class DEWAScraper(BaseScraper):
    """Scraper for DEWA open tenders — saves advertisement PDFs locally."""

    source_name  = 'DEWA -- Dubai Electricity & Water Authority'
    country_code = 'AE'
    base_url     = BASE_URL

    def scrape(self) -> list[TenderData]:
        session = requests.Session()
        session.headers.update(_HEADERS)

        html = self._fetch_html(session)
        if not html:
            return []

        soup = BeautifulSoup(html, 'html.parser')
        blocks = soup.select('div.tender-result')
        logger.info(f'[DEWA] Found {len(blocks)} tender blocks')

        if not blocks:
            logger.warning('[DEWA] No .tender-result blocks found')
            return []

        results = []
        for block in blocks:
            tender = self._parse_block(block, session)
            if tender:
                results.append(tender)

        logger.info(f'[DEWA] Done -- {len(results)} tenders')
        return results

    def _fetch_html(self, session: requests.Session) -> Optional[str]:
        """GET list page — establishes session cookies (ASP.NET_SessionId etc.)."""
        logger.info(f'[DEWA] Fetching {LIST_URL}')
        try:
            r = session.get(LIST_URL, timeout=20)
            if r.status_code == 200 and 'tender-result' in r.text:
                logger.info(f'[DEWA] Got HTML via session ({len(r.content):,} bytes)')
                return r.text
            logger.info(f'[DEWA] Session GET returned {r.status_code} -- trying Playwright')
        except requests.RequestException as exc:
            logger.info(f'[DEWA] Session GET error: {exc} -- trying Playwright')

        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True, args=['--no-sandbox'])
                page = browser.new_page(user_agent=_HEADERS['User-Agent'])
                page.goto(LIST_URL, wait_until='domcontentloaded', timeout=30_000)
                page.wait_for_selector('div.tender-result', timeout=10_000)
                html = page.content()
                # Carry Playwright cookies into the requests session
                for ck in page.context.cookies():
                    session.cookies.set(
                        ck['name'], ck['value'],
                        domain=ck.get('domain', '.dewa.gov.ae')
                    )
                browser.close()
            logger.info(f'[DEWA] Got HTML via Playwright ({len(html):,} bytes)')
            return html
        except Exception as exc:
            logger.error(f'[DEWA] Playwright error: {exc}')
            return None

    def _fetch_advertisement_url(
        self,
        tender_no: str,
        csrf_token: str,
        session: requests.Session,
    ) -> str:
        """
        POST tenderNumber + per-tender CSRF token using the established session.
        Server returns 200 + content-disposition: attachment + PDF bytes.
        Saves PDF to media/dewa_ads/<tender_no>.pdf and returns its URL.
        """
        if not tender_no:
            return LIST_URL

        # Return cached URL if already downloaded
        media_dir = _get_media_dir()
        pdf_name  = f'{tender_no}.pdf'
        pdf_path  = media_dir / pdf_name
        if pdf_path.exists() and pdf_path.stat().st_size > 1000:
            logger.debug(f'[DEWA] PDF cached: {tender_no}')
            return _get_media_url(pdf_name)

        try:
            data = {
                'tenderNumber':               tender_no,
                '__RequestVerificationToken': csrf_token,
            }
            post_headers = {
                'Content-Type': 'application/x-www-form-urlencoded',
                'Referer':      LIST_URL,
                'sec-fetch-dest': 'document',
                'sec-fetch-mode': 'navigate',
                'sec-fetch-site': 'same-origin',
                'sec-fetch-user': '?1',
            }
            r = session.post(POST_URL, data=data, headers=post_headers,
                             timeout=20, stream=True)

            if r.status_code == 200:
                ct = r.headers.get('content-type', '')
                cd = r.headers.get('content-disposition', '')
                if 'pdf' in ct.lower() or 'attachment' in cd.lower() or 'octet-stream' in ct.lower():
                    pdf_path.write_bytes(r.content)
                    logger.info(f'[DEWA] Saved PDF {tender_no} ({len(r.content):,} bytes)')
                    return _get_media_url(pdf_name)

            logger.debug(f'[DEWA] ViewAttachment {r.status_code} for {tender_no}')
        except Exception as exc:
            logger.debug(f'[DEWA] ViewAttachment failed for {tender_no}: {exc}')

        return LIST_URL

    def _parse_block(self, block, session: requests.Session) -> Optional[TenderData]:
        """Parse a single .tender-result div."""

        def get_value(label: str) -> str:
            for dt in block.find_all('dt'):
                if label.lower() in dt.get_text(strip=True).lower():
                    dd = dt.find_next_sibling('dd')
                    if dd:
                        return dd.get_text(strip=True)
            return ''

        # ── Tender number (text node, not the link) ────────────────────────
        tender_no = ''
        tender_no_dd = block.find('dd', class_='tender-result__value')
        if tender_no_dd:
            for content in tender_no_dd.children:
                text = content.strip() if isinstance(content, str) else ''
                if text:
                    tender_no = text
                    break

        # ── CSRF token — the hidden input inside this tender's block ───────
        csrf_input = block.find('input', {'name': '__RequestVerificationToken'})
        csrf_token = csrf_input.get('value', '') if csrf_input else ''

        # ── Title ──────────────────────────────────────────────────────────
        title = get_value('Name of Tenderer')
        if not title or len(title) < 4:
            return None

        # ── Status ─────────────────────────────────────────────────────────
        raw_status = get_value('Tender Status').lower()
        status = 'CLOSED' if 'closed' in raw_status else 'OPEN'

        # ── Dates ──────────────────────────────────────────────────────────
        pub_raw  = get_value('Floating Date')
        dead_raw = get_value('Closing Date')

        # ── Source URL: POST -> get PDF -> save locally -> return media URL ─
        source_url = self._fetch_advertisement_url(tender_no, csrf_token, session)

        return TenderData(
            title               = title[:300],
            source_url          = source_url,
            external_id         = tender_no or title[:80],
            reference_no        = tender_no[:100],
            entity              = 'Dubai Electricity & Water Authority (DEWA)',
            entity_ar           = 'هيئة كهرباء ومياه دبي (ديوا)',
            description         = '',
            category            = self._map_category('', title),
            status              = status,
            currency            = 'AED',
            publication_date    = self._parse_date(pub_raw),
            submission_deadline = self._parse_date(dead_raw),
            country_code        = 'AE',
        )

    @staticmethod
    def _map_category(raw_cat: str, title: str = '') -> str:
        t = (raw_cat + ' ' + title).lower()
        if any(w in t for w in (
            'consult', 'advisory', 'study', 'survey', 'supervision',
            'feasibility', 'engineering service', 'hiring of consultancy',
        )):
            return 'CONSULTANCY'
        if any(w in t for w in (
            'installation', 'commissioning', 'construction', 'works',
            'civil', 'cable', 'pipeline', 'network', 'rehabilitation',
            'testing & commissioning', 'supply installation',
        )):
            return 'WORKS'
        if any(w in t for w in (
            'supply of', 'purchase of', 'supply,', 'hiring of',
            'spare', 'equipment', 'material', 'vehicle', 'truck',
            'meter', 'transformer', 'cable ', 'fuse', 'pump',
        )):
            return 'GOODS'
        return 'SERVICES'

    @staticmethod
    def _parse_date(val: str) -> Optional[datetime.date]:
        if not val:
            return None
        for fmt in ('%d-%b-%Y', '%d/%m/%Y', '%Y-%m-%d', '%d %b %Y', '%B %d, %Y'):
            try:
                return datetime.strptime(val.strip(), fmt).date()
            except ValueError:
                continue
        return None
