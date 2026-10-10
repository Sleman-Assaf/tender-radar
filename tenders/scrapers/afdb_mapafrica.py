# tenders/scrapers/afdb_mapafrica.py
# Path: TenderRadar/tenders/scrapers/afdb_mapafrica.py
"""
AfDB MapAfrica -- African Development Bank Project Portfolio
https://mapafrica.afdb.org/api/v15/activities

API returns JSON with project/activity records.
Each record is mirrored to every country in country_codes (comma-separated).
Fetches up to 10 pages × 100 records = 1000 projects per run.
"""

import logging
from datetime import datetime
from typing import Optional

import requests

from .base import BaseScraper, TenderData

logger = logging.getLogger('tenders')

API_URL  = 'https://mapafrica.afdb.org/api/v15/activities'
BASE_URL = 'https://mapafrica.afdb.org'
MAX_PAGES = 10

_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (compatible; TenderRadar/1.0)',
    'Accept':     'application/json',
}

# sector code → category
_SECTOR_MAP = {
    'A': 'GOODS',        # Agriculture
    'B': 'WORKS',        # Industry / Mining
    'C': 'WORKS',        # Environment
    'D': 'WORKS',        # Transport
    'E': 'WORKS',        # Water & Sanitation
    'F': 'WORKS',        # Energy
    'G': 'GOODS',        # Finance / Private sector
    'H': 'SERVICES',     # Finance
    'I': 'CONSULTANCY',  # Social / Health / Education
    'J': 'SERVICES',     # Multi-sector
    'K': 'CONSULTANCY',  # Governance / Public management
}

# activity_status codes
_STATUS_MAP = {
    '1': 'OPEN',     # Pipeline / Under preparation
    '2': 'OPEN',     # Implementation
    '3': 'CLOSED',   # Completion
    '5': 'CLOSED',   # Cancelled
}


class AfDBMapAfricaScraper(BaseScraper):
    """Scraper for African Development Bank MapAfrica project portfolio API."""

    source_name  = 'AfDB MapAfrica -- African Development Bank Projects'
    country_code = 'ZZ'   # multi-country — overridden per tender
    base_url     = BASE_URL

    def scrape(self) -> list[TenderData]:
        results = []
        seen_ids = set()

        for page in range(1, MAX_PAGES + 1):
            records = self._fetch_page(page)
            if not records:
                logger.info(f'[AfDB] No records on page {page}, stopping')
                break

            for rec in records:
                tenders = self._record_to_tenders(rec)
                for t in tenders:
                    key = f"{t.external_id}-{t.country_code}"
                    if key not in seen_ids:
                        seen_ids.add(key)
                        results.append(t)

            logger.info(f'[AfDB] Page {page}: {len(records)} records (total tenders: {len(results)})')

            if len(records) < 100:
                break  # last page

        logger.info(f'[AfDB] Done — {len(results)} tenders')
        return results

    def _fetch_page(self, page: int) -> list[dict]:
        params = {
            'lang':      'en',
            'per_page':  100,
            'page':      page,
            'ascending': 'false',
            'order_by':  'activity_date_planned_start',
            'currency':  'xdr',
        }
        try:
            r = requests.get(API_URL, params=params, headers=_HEADERS, timeout=20)
            if r.status_code != 200:
                logger.warning(f'[AfDB] Page {page}: status {r.status_code}')
                return []
            data = r.json()
            return data.get('results', [])
        except Exception as exc:
            logger.warning(f'[AfDB] Page {page} error: {exc}')
            return []

    def _record_to_tenders(self, rec: dict) -> list[TenderData]:
        """
        One API record → one TenderData per country in country_codes.
        Multinational projects (country_codes = 'Z1,...') get one entry per real country code.
        """
        title        = (rec.get('title') or '').strip()
        afdb_ref     = rec.get('afdb_identifier_ref') or rec.get('iati_identifier') or ''
        activity_id  = str(rec.get('id', ''))
        status_code  = str(rec.get('activity_status') or '1')
        status       = _STATUS_MAP.get(status_code, 'OPEN')
        sector_code  = rec.get('custom_sector_code') or ''
        category     = _SECTOR_MAP.get(sector_code, 'SERVICES')
        commitment   = rec.get('total_commitments') or 0
        pub_date     = self._parse_date(rec.get('activity_date_planned_start') or rec.get('activity_date_actual_start'))
        end_date     = self._parse_date(rec.get('activity_date_planned_end') or rec.get('activity_date_actual_end'))
        source_url   = f'https://mapafrica.afdb.org/en/projects/{afdb_ref}' if afdb_ref else BASE_URL
        entity       = rec.get('reporting_org') or 'African Development Bank'
        description  = (
            f"AfDB Status: {rec.get('afdb_status') or 'N/A'} | "
            f"Sector: {sector_code} | "
            f"Commitment: USD {commitment:,.0f}"
        )

        if not title or len(title) < 5:
            return []

        # Parse country codes — may be comma-separated, may include Z1 (multinational)
        raw_codes = rec.get('country_codes') or rec.get('recipient_country') or 'ZZ'
        codes = [c.strip() for c in raw_codes.split(',') if c.strip() and c.strip() != 'Z1']
        if not codes:
            codes = ['ZZ']  # unknown/multinational

        tenders = []
        for country_code in codes:
            tenders.append(TenderData(
                title               = title[:300],
                source_url          = source_url,
                external_id         = f'{afdb_ref or activity_id}-{country_code}',
                reference_no        = afdb_ref[:100] if afdb_ref else activity_id,
                entity              = entity[:200],
                entity_ar           = 'البنك الأفريقي للتنمية',
                description         = description,
                category            = category,
                status              = status,
                currency            = 'USD',
                publication_date    = pub_date,
                submission_deadline = end_date,
                country_code        = country_code,
                estimated_value     = float(commitment) if commitment else None,
            ))

        return tenders

    @staticmethod
    def _parse_date(val) -> Optional[datetime.date]:
        if not val:
            return None
        try:
            return datetime.strptime(str(val)[:10], '%Y-%m-%d').date()
        except ValueError:
            return None
