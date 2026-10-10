# tenders/scrapers/tendersinfo_jordan.py
"""
TendersInfo — Jordan Projects & Tenders
URL: https://www.tendersinfo.com/jordan-projects.php

What is this?
-------------
TendersInfo (Euclid Infotech Ltd) is a commercial tender aggregation platform
covering 190+ countries. The Jordan Projects page aggregates government and
private tender notices from Jordan ministries, municipalities, and international
organizations.

Technology: PHP + DataTables SPA. The table is loaded via AJAX POST to the
DataTables server-side processing endpoint.

API (confirmed from browser Network tab):
    POST https://www.tendersinfo.com/esearch/tender_sector_test
    Content-Type: application/x-www-form-urlencoded
    X-Requested-With: XMLHttpRequest

Key payload fields:
    countrytxt   = "0300400"   (Jordan country code in their system)
    country_code = "JO"
    notice_type  = 9           (9 = Projects)
    start        = 0           (DataTables offset, increments by length)
    length       = 25          (page size; use 25 for efficiency)
    draw         = N           (DataTables draw counter)
    order[0][column] = 0
    order[0][dir]    = "asc"

Response shape (confirmed from browser):
    {
        "draw": 1,
        "recordsTotal": 47,
        "recordsFiltered": 47,
        "data": [
            {
                "site_tender_id": "521963938",
                "short_desc":     "Hikma Loan 2025",
                "date_c":         "23-Aug-2025",    ← publication date
                "doc_last":       "31-Dec-2028",    ← deadline
                "authority":      "HIKMA FINANCE USA LLC",
                "organisation_h": "<br><b>Tender Authority: </b>HIKMA FINANCE USA LLC",
                "url":            "https://www.tendersinfo.com/tenders_details/hikma-loan-521963938",
                "sector_name":    "Healthcare And Medicine, Banking...",
                "region_name":    "Jordan",
                "country":        "JO",
                "est_cost_h":     ""
            },
            ...
        ]
    }

Date format: "DD-Mon-YYYY" e.g. "23-Aug-2025"
Total records: 47 (notice_type=9, Jordan) → 2 pages of 25
"""

import html
import logging
import re
from datetime import datetime, date
from typing import Optional

from .base import BaseScraper, TenderData

logger = logging.getLogger('tenders')

BASE_URL  = 'https://www.tendersinfo.com'
LIST_URL  = f'{BASE_URL}/jordan-projects.php'
API_URL   = f'{BASE_URL}/esearch/tender_sector_test'
PAGE_SIZE = 25
MAX_PAGES = 10   # 10 × 25 = 250 items; recordsTotal=47 so usually 2 pages

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept':          'application/json, text/javascript, */*; q=0.01',
    'Content-Type':    'application/x-www-form-urlencoded; charset=UTF-8',
    'X-Requested-With': 'XMLHttpRequest',
    'Referer':         LIST_URL,
    'Origin':          BASE_URL,
}


def _dt_payload(draw: int, start: int, length: int) -> dict:
    """Build the DataTables POST payload for Jordan projects."""
    return {
        'draw':                      draw,
        'columns[0][data]':          'site_tender_id',
        'columns[0][name]':          '',
        'columns[0][searchable]':    'true',
        'columns[0][orderable]':     'false',
        'columns[0][search][value]': '',
        'columns[0][search][regex]': 'false',
        'columns[1][data]':          '',
        'columns[1][name]':          '',
        'columns[1][searchable]':    'true',
        'columns[1][orderable]':     'false',
        'columns[1][search][value]': '',
        'columns[1][search][regex]': 'false',
        'columns[2][data]':          'date_c',
        'columns[2][name]':          '',
        'columns[2][searchable]':    'true',
        'columns[2][orderable]':     'true',
        'columns[2][search][value]': '',
        'columns[2][search][regex]': 'false',
        'columns[3][data]':          'short_desc',
        'columns[3][name]':          '',
        'columns[3][searchable]':    'true',
        'columns[3][orderable]':     'false',
        'columns[3][search][value]': '',
        'columns[3][search][regex]': 'false',
        'columns[4][data]':          'doc_last',
        'columns[4][name]':          '',
        'columns[4][searchable]':    'true',
        'columns[4][orderable]':     'true',
        'columns[4][search][value]': '',
        'columns[4][search][regex]': 'false',
        'order[0][column]':          0,
        'order[0][dir]':             'asc',
        'order[0][name]':            '',
        'start':                     start,
        'length':                    length,
        'search[value]':             '',
        'search[regex]':             'false',
        'sectortxt':                 '',
        'countrytxt':                '0300400',
        'region_txt':                '',
        'country_code':              'JO',
        'cpvtxt':                    '',
        'notice_type':               9,
    }


class TendersInfoJordanScraper(BaseScraper):
    """
    Scraper for TendersInfo Jordan Projects.

    Uses the DataTables server-side processing endpoint directly:
    POST https://www.tendersinfo.com/esearch/tender_sector_test

    Pagination: DataTables offset-based (start=0, 25, 50, ...)
    Total records: ~47 for notice_type=9 (Projects), Jordan.
    """

    country_code = 'JO'
    source_name  = 'TendersInfo — Jordan'
    base_url     = BASE_URL

    def scrape(self) -> list[TenderData]:
        from tenders.models import ScraperSource
        try:
            source = ScraperSource.objects.get(
                scraper_class='TendersInfoJordanScraper', is_active=True,
            )
        except ScraperSource.DoesNotExist:
            print('  X  [TendersInfoJordan] No active ScraperSource — run seed_data')
            return []
        except ScraperSource.MultipleObjectsReturned:
            source = ScraperSource.objects.filter(
                scraper_class='TendersInfoJordanScraper', is_active=True
            ).first()

        self.session.headers.update(HEADERS)

        rel_src, cat_src = self._reload_keywords_from_db()
        rel_count = len(self._relevance_keywords)
        cat_count = sum(len(v) for v in self._category_keywords.values())

        print(f'\n{"_" * 62}')
        print(f'  SOURCE  : {source.name}')
        print(f'  SCRAPER : TendersInfoJordanScraper')
        print(f'  MODE    : POST JSON API (DataTables server-side)')
        print(f'  API     : {API_URL}')
        print(f'  Keywords: {rel_count} relevance [{rel_src}]'
              f'  |  {cat_count} category [{cat_src}]')
        print(f'{"_" * 62}')

        results:   list[TenderData] = []
        seen_ids:  set[str]        = set()
        draw       = 1
        start      = 0
        total      = None

        while True:
            page_num = draw
            print(f'       > page {page_num:>3} (start={start})', end='  ', flush=True)

            payload = _dt_payload(draw=draw, start=start, length=PAGE_SIZE)

            try:
                resp = self.session.post(API_URL, data=payload, timeout=self.timeout)
                resp.raise_for_status()
                envelope = resp.json()
            except Exception as exc:
                err = str(exc)
                if '403' in err or '401' in err:
                    print('(blocked — WAF/IP restriction)')
                    logger.warning(f'[TendersInfoJordan] WAF block start={start}: {exc}')
                else:
                    print(f'X ERROR: {exc}')
                    logger.error(f'[TendersInfoJordan] start={start}: {exc}')
                break

            if total is None:
                total = envelope.get('recordsTotal', 0)
                filtered = envelope.get('recordsFiltered', total)
                print(f'(recordsTotal={total}, filtered={filtered})', end='  ')

            rows = envelope.get('data', [])
            if not rows:
                print('(empty — stopping)')
                break

            # Debug on first page
            if draw == 1 and rows:
                r0 = rows[0]
                print(f'\n  [DEBUG] site_tender_id: {r0.get("site_tender_id")}')
                print(f'  [DEBUG] short_desc:     {r0.get("short_desc","?")[:65]}')
                print(f'  [DEBUG] authority:      {r0.get("authority","?")}')
                print(f'  [DEBUG] date_c:         {r0.get("date_c")}')
                print(f'  [DEBUG] doc_last:       {r0.get("doc_last")}')
                print(f'  [DEBUG] sector_name:    {r0.get("sector_name","?")[:60]}')
                print(f'  [DEBUG] url:            {r0.get("url","?")}')
                print()

            matched = 0
            page_new = 0
            for row in rows:
                td = self._parse_row(row)
                if not td:
                    continue
                if td.external_id and td.external_id in seen_ids:
                    continue
                if td.external_id:
                    seen_ids.add(td.external_id)
                page_new += 1
                combined = f'{td.title} {td.entity} {td.description}'
                if self.detect_relevance(combined):
                    results.append(td)
                    matched += 1

            print(f'-> {len(rows)} rows, {page_new} new, {matched} relevant')
            logger.info(
                f'[TendersInfoJordan] start={start}: '
                f'{len(rows)} rows, {page_new} new, {matched} relevant'
            )

            start += PAGE_SIZE
            draw  += 1

            if total and start >= total:
                print(f'       > All {total} records fetched')
                break
            if draw > MAX_PAGES:
                print(f'       > Hit MAX_PAGES cap ({MAX_PAGES})')
                break
            if len(rows) < PAGE_SIZE:
                print(f'       > Partial page ({len(rows)}) — end of data')
                break

        print(f'\n  {"=" * 58}')
        print(f'  TOTAL : {len(results)} relevant TendersInfo Jordan tender(s)')
        print(f'  {"=" * 58}\n')
        logger.info(f'[TendersInfoJordan] complete — total={len(results)}')
        return results

    # ── Row parser ────────────────────────────────────────────────────────

    def _parse_row(self, row: dict) -> Optional[TenderData]:
        try:
            # Title — decode HTML entities (e.g. &amp;)
            title = html.unescape(row.get('short_desc', '') or '').strip()
            if not title:
                return None

            ext_id     = str(row.get('site_tender_id', '') or '').strip()
            detail_url = row.get('url', '') or LIST_URL
            authority  = (row.get('authority', '') or '').strip()
            sector     = (row.get('sector_name', '') or '').strip()

            # Dates
            pub_date = self._parse_ti_date(row.get('date_c', ''))
            deadline = self._parse_ti_date(row.get('doc_last', ''))

            today  = date.today()
            if deadline and deadline < today:
                status        = 'CLOSED'
                save_deadline = None   # bypass save_tender_data past-date skip
            else:
                status        = 'OPEN'
                save_deadline = deadline

            return TenderData(
                title               = title,
                title_ar            = title,
                source_url          = detail_url,
                external_id         = ext_id,
                reference_no        = ext_id,
                entity              = authority or 'Jordan Government',
                entity_ar           = authority or 'حكومة الأردن',
                description         = sector,
                category            = self.detect_category(f'{title} {sector}'),
                status              = status,
                currency            = 'JOD',
                estimated_value     = None,
                publication_date    = pub_date,
                submission_deadline = save_deadline,
                opening_date        = None,
            )

        except Exception as exc:
            logger.warning(f'[TendersInfoJordan] parse error: {exc}')
            return None

    # ── Date parsing ──────────────────────────────────────────────────────

    @staticmethod
    def _parse_ti_date(val: str) -> Optional[date]:
        """Parse TendersInfo date format: 'DD-Mon-YYYY' e.g. '23-Aug-2025'."""
        if not val:
            return None
        val = val.strip()
        for fmt in ('%d-%b-%Y', '%d-%b-%y', '%d/%b/%Y', '%Y-%m-%d'):
            try:
                return datetime.strptime(val, fmt).date()
            except ValueError:
                continue
        return None
