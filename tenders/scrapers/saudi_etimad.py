# tenders/scrapers/saudi_etimad.py
"""
Saudi Arabia — Etimad Government Procurement Platform
https://tenders.etimad.sa/Tender/AllTendersForVisitor?PageNumber=N

How it works
------------
The page (AllTendersForVisitor) is a Vue.js SPA.  On load it calls the
internal JSON endpoint:

    GET /Tender/AllSupplierTendersForVisitorAsync?PageNumber=1&PageSize=24&PublishDateId=5&Sort=SubmitionDate&SortDirection=DESC

Each response:
    {
        "data": [                         ← list of tenders
            {
                "tenderIdString":          "GBsq7vC7q6AUruP...",   ← encrypted ID for detail URL
                "tenderName":              "تشغيل وتنفيذ إدارة المخاطر المؤسسية",
                "referenceNumber":         "241239003005",
                "tenderTypeName":          "منافسة عامة",
                "tenderTypeId":            1,
                "agencyName":              "وزارة العدل - الديوان العام",
                "branchName":              "إدارة المشتريات والعقود",
                "tenderActivityName":      "خدمات استشارية",
                "submitionDate":           "2024-12-27T10:21:49",
                "lastEnqueriesDate":       "2025-01-15T10:00:00",
                "lastOfferPresentationDate": "2025-01-27T10:00:00",
                "offersOpeningDate":       "2025-01-27T10:00:00",
                "remainingDays":           29,
                "remainingHours":          12,
                "condetionalBookletPrice": 0.0,
                "financialFees":           0.0,
                "isUGRP":                  false,
            },
            ...
        ],
        "totalCount": 2394     ← total matching tenders (from stats shown on page)
    }

Detail URL:
    https://tenders.etimad.sa/Tender/DetailsForVisitor?STenderId=<tenderIdString>

Pagination:
    ?PageNumber=N  (1-based),  PageSize up to 24 per request
    We use PageSize=24 (largest the selector shows) to minimise HTTP calls.

PublishDateId:
    1 = any time,  2 = last 2 days,  3 = last week,  4 = last month,
    5 = last 3 months (default on page).
    We leave it unset (= 1 / any time) so we never miss older tenders.

Relevance:
    Checked per-tender for accuracy.
"""

import logging
import math
from datetime import datetime, date
from typing import Optional

from .base import BaseScraper, TenderData

logger = logging.getLogger('tenders')

# ── Constants ─────────────────────────────────────────────────────────────────
BASE_URL   = 'https://tenders.etimad.sa'
# The real async JSON endpoint (not the SPA HTML page)
API_URL    = f'{BASE_URL}/Tender/AllSupplierTendersForVisitorAsync'
DETAIL_URL = f'{BASE_URL}/Tender/DetailsForVisitor'

PAGE_SIZE  = 24          # maximum shown in the UI selector
MAX_PAGES  = 10         # safety cap  (300 × 24 = 7 200 tenders)

HEADERS = {
    'Accept':           'application/json, text/plain, */*',
    'Accept-Language':  'ar,en;q=0.9',
    'Referer':          f'{BASE_URL}/Tender/AllTendersForVisitor',
    'Origin':           BASE_URL,
    'X-Requested-With': 'XMLHttpRequest',
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
}

# tenderTypeId → CSIR-style status (no explicit status field in listing)
# tenderTypeId 15 = announcement (DetailsTenderAnnouncement)
TENDER_TYPE_STATUS = {
    15: 'ANNOUNCEMENT',
}


class SaudiEtimadScraper(BaseScraper):
    """
    Scraper for the Saudi Etimad government procurement platform.

    JSON endpoint:
        GET https://tenders.etimad.sa/Tender/AllSupplierTendersForVisitorAsync
            ?PageNumber=1&PageSize=24&Sort=SubmitionDate&SortDirection=DESC
    """

    country_code = 'SA'
    source_name  = 'Saudi Etimad'
    base_url     = BASE_URL

    # ── Public entry point ────────────────────────────────────────────────────
    def scrape(self) -> list[TenderData]:
        from tenders.models import ScraperSource

        try:
            source = ScraperSource.objects.get(
                scraper_class='SaudiEtimadScraper', is_active=True
            )
        except ScraperSource.DoesNotExist:
            print('  X  [SaudiEtimad] ScraperSource not configured in Admin')
            return []
        except ScraperSource.MultipleObjectsReturned:
            source = ScraperSource.objects.filter(
                scraper_class='SaudiEtimadScraper', is_active=True
            ).first()

        self.session.headers.update(HEADERS)

        filter_relevance = True

        rel_src, cat_src = self._reload_keywords_from_db()
        rel_count = len(self._relevance_keywords)
        cat_count = sum(len(v) for v in self._category_keywords.values())

        print(f'\n{"_" * 62}')
        print(f'  SOURCE  : {source.name}')
        print(f'  SCRAPER : SaudiEtimadScraper')
        print(f'  MODE    : JSON API  (GET AllSupplierTendersForVisitorAsync)')
        print(f'  API URL : {API_URL}')
        filter_mode = 'ON (keywords)' if filter_relevance else 'OFF (save all)'
        print(f'  Keywords: {rel_count} relevance [{rel_src}]'
              f'  |  {cat_count} category [{cat_src}]')
        print(f'  Filter  : {filter_mode}')
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
                # No PublishDateId → returns all time; remove if you want recent only
            }

            try:
                resp = self.get(API_URL, params=params)
                resp.raise_for_status()
                body = resp.json()
            except Exception as exc:
                print(f'X ERROR: {exc}')
                logger.error(f'[SaudiEtimad] page {page} error: {exc}')
                break

            # ── Pagination ─────────────────────────────────────────────────
            raw_list    = body.get('data') or []
            total_count = int(body.get('totalCount') or 0)

            if total_pages is None and total_count:
                total_pages = min(
                    math.ceil(total_count / PAGE_SIZE),
                    MAX_PAGES,
                )
                print(f'(total={total_count}, pages={total_pages})', end='  ')

            if not raw_list:
                print('(empty — stopping)')
                break

            # ── Per-tender relevance ──────────────────────────────────────
            matched = 0
            # DEBUG first page only
            if page == 1 and raw_list:
                t0   = raw_list[0]
                txt0 = self._tender_text(t0)
                kws  = self._relevance_keywords
                print(f'\n  [DEBUG] relevance keywords ({len(kws)}): {kws[:8]}')
                print(f'  [DEBUG] sample tender text : {txt0[:150]}')
                hits = [kw for kw in kws if kw in txt0.lower()]
                print(f'  [DEBUG] matching kws       : {hits}\n')
            for t in raw_list:
                text = self._tender_text(t)
                if self.detect_relevance(text):
                    results.append(self._to_tender_data(t))
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
        print(f'  TOTAL : {len(results)} relevant tender(s)  <-- SaudiEtimad')
        print(f'  {"=" * 58}\n')
        return results

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _tender_text(self, t: dict) -> str:
        """All searchable text fields concatenated for keyword matching."""
        return ' '.join(filter(None, [
            t.get('tenderName',         ''),
            t.get('agencyName',         ''),
            t.get('branchName',         ''),
            t.get('tenderTypeName',     ''),
            t.get('tenderActivityName', ''),
        ]))

    def _to_tender_data(self, t: dict) -> TenderData:
        tender_id_str = t.get('tenderIdString', '') or ''
        tender_type   = t.get('tenderTypeId') or 0

        # Announcements use a different detail URL
        if tender_type == 15:
            source_url = (
                f'{BASE_URL}/Tender/DetailsTenderAnnouncement'
                f'?STenderId={tender_id_str}'
            )
        else:
            source_url = f'{DETAIL_URL}?STenderId={tender_id_str}'

        combined    = self._tender_text(t)
        booklet_fee = (t.get('condetionalBookletPrice') or 0)
        fin_fee     = (t.get('financialFees') or 0)
        value       = self._safe_float(booklet_fee + fin_fee) if (booklet_fee or fin_fee) else None

        return TenderData(
            title               = t.get('tenderName',                '') or '',
            source_url          = source_url,
            external_id         = tender_id_str,
            reference_no        = t.get('referenceNumber',           '') or '',
            entity              = t.get('agencyName',                '') or '',
            entity_ar           = t.get('agencyName',                '') or '',
            category            = self.detect_category(combined),
            status              = TENDER_TYPE_STATUS.get(tender_type, 'OPEN'),
            currency            = 'SAR',
            estimated_value     = value,
            publication_date    = self._parse_date(t.get('submitionDate')),
            submission_deadline = self._parse_date(t.get('lastOfferPresentationDate')),
            opening_date        = self._parse_date(t.get('offersOpeningDate')),
        )

    # ── Static utilities ──────────────────────────────────────────────────────

    @staticmethod
    def _parse_date(val) -> Optional[date]:
        """Parse date strings returned by the API.
        Uses explicit slice lengths — NOT len(fmt) which counts % chars wrongly."""
        if not val:
            return None
        if isinstance(val, str):
            val = val.strip()
            if not val:
                return None
            # ASP.NET  /Date(1735689600000)/
            if val.startswith('/Date('):
                try:
                    ms_str = val[6:val.index(')')]
                    ms_str = ms_str.split('+')[0].split('-')[0]
                    return datetime.utcfromtimestamp(int(ms_str) / 1000).date()
                except (ValueError, IndexError):
                    return None
            # Strip milliseconds  "2025-01-27T10:00:00.123" → "2025-01-27T10:00:00"
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
    @staticmethod
    def _safe_float(val) -> Optional[float]:
        try:
            f = float(val)
            return f if f > 0 else None
        except (ValueError, TypeError):
            return None
