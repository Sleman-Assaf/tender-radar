# tenders/scrapers/saudi_etimad_qual.py
"""
Saudi Arabia — Etimad Qualification / Pre-qualification Notices
https://tenders.etimad.sa/Qualification/QualificationsForVisitor?PageNumber=1

How it works
------------
The page is a Vue.js SPA that calls an internal JSON endpoint on load:

    GET /Qualification/GetAllQualificationsForVisitorAsync
        ?PageNumber=1&PageSize=10

Expected response shape (mirrors AllSupplierTendersForVisitorAsync):
    {
        "data": [
            {
                "qualificationIdString":     "<encrypted-id>",
                "qualificationName":         "تأهيل مقدمي خدمات الاستشارات الهندسية",
                "referenceNumber":           "QR-2024-001",
                "agencyName":                "وزارة الإسكان",
                "branchName":                "المشتريات",
                "qualificationTypeName":     "تأهيل مسبق",
                "qualificationActivityName": "خدمات استشارية",
                "submitionDate":             "2024-12-01T00:00:00",
                "lastEnqueriesDate":         "2025-01-10T00:00:00",
                "lastOfferPresentationDate": "2025-01-20T00:00:00",
                ...
            },
            ...
        ],
        "totalCount": 450
    }

Detail URL:
    https://tenders.etimad.sa/Qualification/DetailsForVisitor?SQualificationId=<qualificationIdString>

NOTE: Etimad blocks most automated requests (Cloudflare / custom WAF).
      The scraper uses the same session headers as the tender scraper and
      will silently return [] if the endpoint returns 403/captcha, so the
      rest of the pipeline is unaffected.
"""

import logging
import math
from datetime import datetime, date
from typing import Optional

from .base import BaseScraper, TenderData

logger = logging.getLogger('tenders')

BASE_URL   = 'https://tenders.etimad.sa'
API_URL    = f'{BASE_URL}/Qualification/AllPreQualificationsAsync'
DETAIL_URL = f'{BASE_URL}/Qualification/PrequalificationVisitorDetails'

PAGE_SIZE = 10
MAX_PAGES = 20   # 10 × 20 = 200 qualifications per run

HEADERS = {
    'Accept':           'application/json, text/plain, */*',
    'Accept-Language':  'ar,en;q=0.9',
    'Referer':          f'{BASE_URL}/Qualification/QualificationsForVisitor',
    'Origin':            BASE_URL,
    'X-Requested-With': 'XMLHttpRequest',
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
}


class SaudiEtimadQualScraper(BaseScraper):
    """
    Scraper for Etimad pre-qualification / qualification notices.

    Runs alongside SaudiEtimadScraper — same country, different endpoint.
    """

    country_code = 'SA'
    source_name  = 'Saudi Etimad'   # shares the same ScraperSource record
    base_url     = BASE_URL

    # ── Entry point ───────────────────────────────────────────────────────────
    def scrape(self) -> list[TenderData]:
        from tenders.models import ScraperSource

        # Reuse the same ScraperSource as the main Etimad scraper
        try:
            source = ScraperSource.objects.get(
                scraper_class='SaudiEtimadScraper', is_active=True
            )
        except ScraperSource.DoesNotExist:
            print('  X  [EtimadQual] ScraperSource (SaudiEtimadScraper) not found — skipping')
            return []
        except ScraperSource.MultipleObjectsReturned:
            source = ScraperSource.objects.filter(
                scraper_class='SaudiEtimadScraper', is_active=True
            ).first()

        self.session.headers.update(HEADERS)

        rel_src, cat_src = self._reload_keywords_from_db()
        rel_count = len(self._relevance_keywords)

        print(f'\n{"_" * 62}')
        print(f'  SOURCE  : {source.name} — Qualifications')
        print(f'  SCRAPER : SaudiEtimadQualScraper')
        print(f'  API URL : {API_URL}')
        print(f'  Keywords: {rel_count} relevance [{rel_src}]')
        print(f'{"_" * 62}')

        results: list[TenderData] = []
        page        = 1
        total_pages = None

        while True:
            print(f'       > page {page:>4}', end='  ', flush=True)

            params = {
                'PageNumber':    page,
                'PageSize':      PAGE_SIZE,
                'Sort':          'SubmitionDate',
                'SortDirection': 'DESC',
            }

            try:
                resp = self.get(API_URL, params=params)
                resp.raise_for_status()
                body = resp.json()
            except Exception as exc:
                err = str(exc)
                if '403' in err or '401' in err:
                    print(f'(blocked by WAF — skipping this run)')
                    logger.warning(f'[EtimadQual] WAF block on page {page}: {exc}')
                elif '404' in err:
                    print(f'X 404 — wrong endpoint URL, check API_URL constant')
                    logger.error(f'[EtimadQual] 404 on page {page}: {exc}')
                else:
                    print(f'X ERROR: {exc}')
                    logger.error(f'[EtimadQual] page {page} error: {exc}')
                break

            raw_list    = body.get('data') or []
            total_count = int(body.get('totalCount') or 0)

            if total_pages is None and total_count:
                total_pages = min(
                    math.ceil(total_count / PAGE_SIZE),
                    MAX_PAGES,
                )
                print(f'(total={total_count}, pages≤{total_pages})', end='  ')

            if not raw_list:
                print('(empty — stopping)')
                break

            matched = 0
            for item in raw_list:
                text = self._item_text(item)
                if self.detect_relevance(text):
                    results.append(self._to_tender_data(item))
                    matched += 1

            print(f'-> {len(raw_list)} items, {matched} relevant')

            if total_pages and page >= total_pages:
                print(f'       > Reached last page ({page})')
                break
            if page >= MAX_PAGES:
                print(f'       > Hit MAX_PAGES cap ({MAX_PAGES})')
                break

            page += 1

        print(f'\n  {"=" * 58}')
        print(f'  TOTAL : {len(results)} relevant qualification(s)  <-- EtimadQual')
        print(f'  {"=" * 58}\n')
        return results

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _item_text(self, item: dict) -> str:
        """All searchable text for keyword matching.
        Field names taken from the Vue template in QualificationsForVisitor HTML:
            tender.qualificationName, tender.agencyName, tender.qualificationTypeName
        """
        return ' '.join(filter(None, [
            item.get('qualificationName',         ''),
            item.get('agencyName',                ''),
            item.get('qualificationTypeName',     ''),
            item.get('qualificationActivityName', ''),
            # also try tenderName / tenderActivityName in case response reuses tender keys
            item.get('tenderName',                ''),
            item.get('tenderActivityName',        ''),
        ]))

    def _to_tender_data(self, item: dict) -> TenderData:
        # From HTML: tenderDetailsUrl uses tender.tenderIdString
        # URL pattern: /Qualification/PrequalificationVisitorDetails/<tenderIdString>
        qual_id = (
            item.get('tenderIdString')         or
            item.get('qualificationIdString')  or
            item.get('qualificationId')        or
            item.get('id')                     or ''
        )

        source_url = f'{DETAIL_URL}/{qual_id}' if qual_id else BASE_URL

        combined = self._item_text(item)

        return TenderData(
            title               = (
                item.get('qualificationName') or
                item.get('tenderName')        or
                item.get('name')              or ''
            ),
            source_url          = source_url,
            external_id         = str(qual_id),
            reference_no        = item.get('referenceNumber', '') or '',
            entity              = item.get('agencyName', '') or '',
            entity_ar           = item.get('agencyName', '') or '',
            category            = self.detect_category(combined),
            status              = 'OPEN',
            currency            = 'SAR',
            estimated_value     = None,
            publication_date    = self._parse_date(
                item.get('submitionDate') or item.get('publishDate')
            ),
            submission_deadline = self._parse_date(
                item.get('lastOfferPresentationDate') or
                item.get('lastEnqueriesDate')
            ),
            opening_date        = self._parse_date(item.get('offersOpeningDate')),
        )

    # ── Date parsing (shared logic with main Etimad scraper) ──────────────────
    @staticmethod
    def _parse_date(val) -> Optional[date]:
        if not val:
            return None
        if isinstance(val, str):
            val = val.strip()
            if not val:
                return None
            if val.startswith('/Date('):
                try:
                    ms_str = val[6:val.index(')')]
                    ms_str = ms_str.split('+')[0].split('-')[0]
                    return datetime.utcfromtimestamp(int(ms_str) / 1000).date()
                except (ValueError, IndexError):
                    return None
            if '.' in val:
                val = val[:val.index('.')]
            for fmt, length in [
                ('%Y-%m-%dT%H:%M:%S', 19),
                ('%Y-%m-%dT%H:%M',    16),
                ('%Y-%m-%d',          10),
                ('%d/%m/%Y',          10),
            ]:
                try:
                    return datetime.strptime(val[:length], fmt).date()
                except ValueError:
                    continue
        return None
