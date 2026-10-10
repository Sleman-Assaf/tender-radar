# tenders/scrapers/uae_portals.py
"""
UAE — Abu Dhabi ADPS + Dubai Tenders Portal
Both use parse_page() — base scraper handles URL iteration + console logging.
"""

import logging
from datetime import datetime
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .base import BaseScraper, TenderData

logger = logging.getLogger('tenders')


class UAEAbuDhabiScraper(BaseScraper):
    """
    UAE Federal Ministry of Finance — iValua procurement portal
    URL  : https://procurement.gov.ae/page.aspx/ar/rfp/request_browse_public
    AJAX : POST /ajax.aspx/ar/rfp/request_browse_public

    The page uses iValua platform. Tenders are loaded via AJAX GridView pagination.
    Each page has 15 rows. We POST the grid page index to get subsequent pages.
    """
    country_code = 'AE'
    source_name  = 'UAE Abu Dhabi ADPS'
    base_url     = 'https://procurement.gov.ae'

    LIST_URL  = 'https://procurement.gov.ae/page.aspx/ar/rfp/request_browse_public'
    AJAX_URL  = 'https://procurement.gov.ae/ajax.aspx/ar/rfp/request_browse_public'
    DETAIL_BASE = 'https://procurement.gov.ae/page.aspx/ar/bpm/process_manage_extranet'
    MAX_PAGES = 20

    _GET_HEADERS = {
        'User-Agent':      ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                             'AppleWebKit/537.36 (KHTML, like Gecko) '
                             'Chrome/147.0.0.0 Safari/537.36'),
        'Accept':          'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'ar-AE,ar;q=0.9,en-US;q=0.8',
        'Sec-Fetch-Dest':  'document',
        'Sec-Fetch-Mode':  'navigate',
        'Sec-Fetch-Site':  'none',
    }

    _AJAX_HEADERS = {
        'User-Agent':       ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                              'AppleWebKit/537.36 (KHTML, like Gecko) '
                              'Chrome/147.0.0.0 Safari/537.36'),
        'Accept':           'application/json, text/plain, */*',
        'Accept-Language':  'ar-AE,ar;q=0.9,en-US;q=0.8',
        'Content-Type':     'application/json',
        'X-Requested-With': 'XMLHttpRequest',
        'Origin':           'https://procurement.gov.ae',
        'Referer':          'https://procurement.gov.ae/page.aspx/ar/rfp/request_browse_public',
        'Sec-Fetch-Dest':   'empty',
        'Sec-Fetch-Mode':   'cors',
        'Sec-Fetch-Site':   'same-origin',
    }

    def scrape(self) -> list[TenderData]:
        import random, time, requests as req
        from bs4 import BeautifulSoup as BS4

        logger.info(f'[UAE-ADPS] Starting — {self.LIST_URL}')
        session = req.Session()

        # Step 1: GET listing page to get CSRFToken + cookies
        try:
            r0 = session.get(self.LIST_URL, headers=self._GET_HEADERS, timeout=25)
            r0.raise_for_status()
            soup0 = BS4(r0.text, 'html.parser')
            csrf_inp = soup0.find('input', {'id': 'CSRFToken'})
            csrf = csrf_inp['value'] if csrf_inp else ''
            logger.info(f'[UAE-ADPS] GET: {r0.status_code} ({len(r0.content)}b) CSRF={"ok" if csrf else "MISSING"}')
        except Exception as exc:
            logger.error(f'[UAE-ADPS] GET failed: {exc}')
            return []

        # Parse page 1 from initial GET
        all_results: list[TenderData] = []
        seen_ids: set[str] = set()

        items = self._parse_grid(soup0)
        for item in items:
            if item.external_id not in seen_ids:
                seen_ids.add(item.external_id)
                all_results.append(item)

        # Read max page index from hidden input
        max_idx_inp = soup0.find('input', {'id': 'maxpageindexbody_x_grid_grd'})
        max_page_idx = int(max_idx_inp['value']) if max_idx_inp else 0
        logger.info(f'[UAE-ADPS] p1: {len(items)} rows, max_page_idx={max_page_idx}')

        # Step 2: Paginate via AJAX
        for page_idx in range(1, min(max_page_idx + 1, self.MAX_PAGES)):
            time.sleep(random.uniform(1.5, 2.5))

            # iValua uses form-encoded POST for grid pagination
            ajax_payload = {
                'REQUEST_METHOD':                            'GET',
                'hdnCurrentPageIndexbody_x_grid_grd':        str(page_idx),
                'hdnSortExpressionbody_x_grid_grd':          '',
                'hdnSortDirectionbody_x_grid_grd':           '',
                'hdnRowCountbody_x_grid_grd':                '',
                'ajaxrowsiscountedbody_x_grid_grd':          'True',
                'body:x:grid:upgrid':                        '',
                'body:x:txtQuery':                           '',
                'body:x:selFamily':                          '',
                'body:x:prxFilterBar:x:hdnResetFilterUrlbody_x_prxFilterBar_x_cmdRazBtn': '',
                'CSRFToken':                                 csrf,
            }

            ajax_hdrs = {**self._AJAX_HEADERS}
            ajax_hdrs['Content-Type'] = 'application/x-www-form-urlencoded'
            ajax_hdrs.pop('Content-Type', None)
            ajax_hdrs['Content-Type'] = 'application/x-www-form-urlencoded; charset=UTF-8'

            try:
                r = session.post(
                    self.AJAX_URL,
                    data=ajax_payload,
                    headers=ajax_hdrs,
                    timeout=30,
                )
                r.raise_for_status()
                logger.info(f'[UAE-ADPS] p{page_idx+1}: {r.status_code} ({len(r.content)}b)')
            except Exception as exc:
                logger.error(f'[UAE-ADPS] p{page_idx+1} failed: {exc}')
                break

            # Response may be JSON with HTML inside, or plain HTML
            try:
                rdata = r.json()
                html_chunk = (rdata.get('html') or rdata.get('data') or
                               rdata.get('content') or r.text)
            except Exception:
                html_chunk = r.text

            chunk_soup = BS4(html_chunk if isinstance(html_chunk, str) else r.text, 'html.parser')
            items = self._parse_grid(chunk_soup)
            added = 0
            for item in items:
                if item.external_id not in seen_ids:
                    seen_ids.add(item.external_id)
                    all_results.append(item)
                    added += 1
            logger.info(f'[UAE-ADPS] p{page_idx+1}: {len(items)} rows, {added} new (total={len(all_results)})')
            if not items:
                break

        logger.info(f'[UAE-ADPS] Done — {len(all_results)} tenders')
        return all_results

    def _parse_grid(self, soup) -> list[TenderData]:
        """Parse iValua grid rows from HTML."""
        rows = soup.select('tr[id*="body_x_grid_grd_tr_"], tr[data-object-type="rfp"]')
        results = []
        for row in rows:
            rfp_id = row.get('data-id', '')
            cells  = row.find_all('td', {'data-iv-role': 'cell'})
            if len(cells) < 5:
                continue

            ref_no   = cells[1].get_text(strip=True) if len(cells) > 1 else ''
            entity   = cells[2].get_text(strip=True) if len(cells) > 2 else ''
            title_en = cells[3].get_text(strip=True) if len(cells) > 3 else ''
            title_ar = cells[4].get_text(strip=True) if len(cells) > 4 else ''
            pub_end  = cells[6].get_text(strip=True) if len(cells) > 6 else ''
            deadline = cells[8].get_text(strip=True) if len(cells) > 8 else ''

            title = title_ar or title_en
            if not title:
                continue

            detail_url = (f'{self.DETAIL_BASE}/{rfp_id}'
                          if rfp_id else self.LIST_URL)

            results.append(TenderData(
                title               = title,
                title_ar            = title_ar or title,
                source_url          = detail_url,
                external_id         = rfp_id or ref_no,
                reference_no        = ref_no,
                entity              = entity,
                entity_ar           = entity,
                status              = 'OPEN',
                currency            = 'AED',
                country_code        = 'AE',
                publication_date    = self._parse_date(pub_end),
                submission_deadline = self._parse_date(deadline),
            ))
        return results

    def parse_page(self, soup, tendering_url) -> list[TenderData]:
        return self._parse_grid(soup)

    def _parse_date(self, text: str) -> Optional[datetime.date]:
        if not text:
            return None
        val = text.strip()
        # Arabic AM/PM: strip them
        val = val.replace('ص', '').replace('م', '').strip()
        if '.' in val and 'T' not in val:
            val = val[:val.index('.')]
        for fmt in ['%m/%d/%Y %I:%M:%S', '%m/%d/%Y %H:%M:%S',
                    '%m/%d/%Y %I:%M',    '%m/%d/%Y',
                    '%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M',
                    '%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%d %b %Y']:
            try:
                return datetime.strptime(val[:len(fmt)+2].strip(), fmt).date()
            except ValueError:
                continue
        return None


class UAEDubaiTendersScraper(BaseScraper):
    country_code = 'AE'
    source_name  = 'UAE Dubai Tenders'
    base_url     = 'https://tenders.dubai.gov.ae'

    def parse_page(self, soup: BeautifulSoup, tendering_url) -> list[TenderData]:
        items = []
        rows = soup.select('table.tender-table tr, .tender-listing-item, .tender-row, table tr')
        for row in rows:
            cells = row.find_all(['td', 'th'])
            if len(cells) < 2 or row.find('th'):
                continue
            link = row.find('a', href=True)
            title = cells[0].get_text(strip=True)
            if not title:
                continue
            items.append(TenderData(
                title               = title,
                source_url          = urljoin(self.base_url, link['href']) if link else tendering_url.url,
                external_id         = (link['href'].split('/')[-1] if link else ''),
                entity              = cells[1].get_text(strip=True) if len(cells) > 1 else '',
                status              = 'OPEN',
                currency            = 'AED',
                submission_deadline = self._parse_date(
                    cells[3].get_text(strip=True) if len(cells) > 3 else ''
                ),
            ))
        return items

    def _parse_date(self, text: str) -> Optional[datetime.date]:
        if not text:
            return None
        val = text.strip()
        if not val:
            return None
        if '.' in val and 'T' not in val:
            val = val[:val.index('.')]
        for fmt, length in [
            ('%Y-%m-%dT%H:%M:%S', 19),
            ('%Y-%m-%dT%H:%M',    16),
            ('%Y-%m-%d',          10),
            ('%d/%m/%Y',          10),
            ('%d-%m-%Y',          10),
            ('%d %b %Y',          11),
        ]:
            try:
                return datetime.strptime(val[:length], fmt).date()
            except ValueError:
                continue
        return None
