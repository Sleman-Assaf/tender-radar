# tenders/scrapers/afdb.py
# Path: TenderRadar/tenders/scrapers/afdb.py
"""
AfDB — African Development Bank Procurement Notices
https://www.afdb.org/en/projects-and-operations/procurement

Scrapes paginated HTML listing pages (?page=0, ?page=1, ...).
Each page contains 18 procurement notices in a Bootstrap grid.

Structure (from live page):
  .views-field-field-publication-date .date-display-single  → date
  .views-field-title .field-content a                        → title + URL
  View header: "Displaying 1 - 18 of 11603"
  Pagination:  ?page=0 ... ?page=644
"""

import logging
import re
from datetime import datetime
from typing import Optional

import requests
from bs4 import BeautifulSoup

from .base import BaseScraper, TenderData

logger = logging.getLogger('tenders')

BASE_URL    = 'https://www.afdb.org'
LISTING_URL = 'https://www.afdb.org/en/projects-and-operations/procurement'
MAX_PAGES   = 10      # 10 pages × 18 items = ~180 notices per run

_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept':          'text/html,application/xhtml+xml,*/*',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer':         BASE_URL,
}

# Title prefix → category
_PREFIX_CAT = {
    'EOI':  'CONSULTANCY',   # Expression of Interest
    'AMI':  'CONSULTANCY',   # Appel à Manifestation d'Intérêt
    'GPN':  'SERVICES',      # General Procurement Notice
    'PPM':  'WORKS',         # Procurement Plan / works
    'AAO':  'GOODS',         # Appel d'Offres (goods/works)
    'IFB':  'WORKS',         # Invitation for Bids
    'RFP':  'CONSULTANCY',   # Request for Proposals
    'RFQ':  'GOODS',         # Request for Quotations
    'ICB':  'WORKS',         # International Competitive Bidding
}


def _category_from_title(title: str) -> str:
    prefix = title.split('-')[0].strip().upper()
    return _PREFIX_CAT.get(prefix, 'SERVICES')


class AfDBScraper(BaseScraper):
    """AfDB Procurement page HTML scraper."""

    source_name  = 'AfDB — African Development Bank Procurement'
    country_code = 'ZZ'
    base_url     = BASE_URL

    def scrape(self) -> list[TenderData]:
        results  = []
        seen_ids = set()

        for page in range(0, MAX_PAGES):
            items = self._fetch_page(page)
            if not items:
                logger.info(f'[AfDB] Page {page}: no items — stopping')
                break

            new_count = 0
            for t in items:
                if t.external_id not in seen_ids:
                    seen_ids.add(t.external_id)
                    results.append(t)
                    new_count += 1

            print(f'       > page {page + 1:>3}  → {new_count} notices', flush=True)
            logger.info(f'[AfDB] Page {page}: {len(items)} parsed, {new_count} new')

            # Stop if last page returned fewer than 10 items
            if len(items) < 10:
                break

        print(f'\n  TOTAL: {len(results)} AfDB procurement notices')
        logger.info(f'[AfDB] Done — {len(results)} total')
        return results

    def _fetch_page(self, page: int) -> list[TenderData]:
        params = {'page': page} if page > 0 else {}
        try:
            r = requests.get(
                LISTING_URL,
                params=params,
                headers=_HEADERS,
                timeout=30,
            )
            r.raise_for_status()
        except Exception as exc:
            logger.warning(f'[AfDB] Page {page} fetch error: {exc}')
            return []

        return self._parse_html(r.text)

    def _parse_html(self, html: str) -> list[TenderData]:
        soup  = BeautifulSoup(html, 'html.parser')
        items = []

        # Each notice is in a Bootstrap column inside #views-bootstrap-grid-1
        grid = soup.select_one('#views-bootstrap-grid-1')
        if not grid:
            # Fallback: find all title links that look like procurement notices
            grid = soup

        cols = grid.select('[class*="col-xs-12"]') or grid.select('[class*="col-md-4"]')

        if not cols:
            logger.warning('[AfDB] No grid columns found in HTML')
            return []

        for col in cols:
            # Title + URL
            title_el = col.select_one('.views-field-title .field-content a')
            if not title_el:
                title_el = col.select_one('a[href*="/en/documents/"]')
            if not title_el:
                continue

            title = title_el.get_text(strip=True)
            if not title or len(title) < 5:
                continue

            href = title_el.get('href', '')
            if href and not href.startswith('http'):
                href = BASE_URL + href

            # Publication date
            date_el  = col.select_one('.views-field-field-publication-date .date-display-single')
            pub_date = None
            if date_el:
                pub_date = self._parse_date(date_el.get_text(strip=True))

            # External ID from URL slug
            slug    = href.rstrip('/').split('/')[-1]
            ext_id  = slug or title[:80]

            # Category from title prefix (EOI, AMI, GPN, AAO…)
            category = _category_from_title(title)

            # Country — try to extract from title ("EOI - Morocco - ...")
            country_code = self._extract_country(title)

            items.append(TenderData(
                title               = title[:300],
                source_url          = href or LISTING_URL,
                external_id         = ext_id[:200],
                reference_no        = slug[:100],
                entity              = 'African Development Bank',
                entity_ar           = 'البنك الأفريقي للتنمية',
                category            = category,
                status              = 'OPEN',
                publication_date    = pub_date,
                submission_deadline = None,
                country_code        = country_code,
            ))

        return items

    def _extract_country(self, title: str) -> str:
        """Best-effort country extraction from title like 'EOI - Morocco - ...'"""
        parts = [p.strip() for p in title.split('-')]
        if len(parts) >= 2:
            candidate = parts[1].strip().lower()
            # Map common AfDB country names to codes
            country_map = {
                'morocco': 'MA', 'algeria': 'DZ', 'tunisia': 'TN', 'egypt': 'EG',
                'libya': 'LY', 'mauritania': 'MR', 'nigeria': 'NG', 'ghana': 'GH',
                'kenya': 'KE', 'ethiopia': 'ET', 'tanzania': 'TZ', 'uganda': 'UG',
                'rwanda': 'RW', 'senegal': 'SN', 'mali': 'ML', 'niger': 'NE',
                'burkina faso': 'BF', "côte d'ivoire": 'CI', 'cameroon': 'CM',
                'mozambique': 'MZ', 'zambia': 'ZM', 'zimbabwe': 'ZW', 'angola': 'AO',
                'south africa': 'ZA', 'madagascar': 'MG', 'malawi': 'MW', 'namibia': 'NA',
                'botswana': 'BW', 'sudan': 'SD', 'south sudan': 'SS', 'somalia': 'SO',
                'djibouti': 'DJ', 'eritrea': 'ER', 'burundi': 'BI', 'comoros': 'KM',
                'guinea': 'GN', 'sierra leone': 'SL', 'liberia': 'LR', 'gambia': 'GM',
                'togo': 'TG', 'benin': 'BJ', 'chad': 'TD', 'congo': 'CG',
                "democratic republic of congo": 'CD', 'rca': 'CF', 'gabon': 'GA',
                'equatorial guinea': 'GQ', 'sao tome': 'ST', 'cabo verde': 'CV',
                'seychelles': 'SC', 'lesotho': 'LS', 'eswatini': 'SZ',
                'multinational': 'ZZ', 'multi-national': 'ZZ', 'regional': 'ZZ',
            }
            for name, code in country_map.items():
                if name in candidate:
                    return code
        return 'ZZ'

    @staticmethod
    def _parse_date(val: str) -> Optional[datetime.date]:
        if not val:
            return None
        for fmt in ('%d-%b-%Y', '%d/%m/%Y', '%Y-%m-%d', '%B %d, %Y', '%d %B %Y'):
            try:
                return datetime.strptime(val.strip(), fmt).date()
            except ValueError:
                continue
        return None
