# tenders/scrapers/undp_jordan.py
"""
UNDP Jordan — Procurement Notices
URL: https://www.undp.org/jordan/procurement

What is this?
-------------
UNDP Jordan publishes procurement notices via a public JavaScript component
embedded on their procurement page. The component is hosted at:
  https://public-components.undp.org/?comp=proc_notices&cty_id_c=JOR&style_type=table

This script response contains a self-executing JavaScript function call with
the tender data embedded as JSON inside the JS.

Response format (JavaScript with embedded JSON):
    E73A7D21_...(
        {
            "recordcount": 6,
            "columnlist": "area_desc,date,deadline,...",
            "data": {
                "area_desc":           ["OTHER", ...],
                "title":               ["Capacity Building...", ...],
                "notice_id":           ["UNDP-JOR-00673", ...],
                "link":                ["https://procurement-notices.undp.org/view_negotiation.cfm?nego_id=46293", ...],
                "formatted_post_date": ["07-Jun-26", ...],  ← publication date
                "formatted_date":      ["18-Jun-26", ...],  ← deadline
                "posted_d":            ["2026-06-07 00:00:00.0", ...],
                "date":                ["2026-06-18 04:00:00.0", ...],
                "duty_station":        ["UNDP", ...],
                "duty_station_cty":    ["JORDAN", ...],
                "ref_bid_num":         [5924194, ...],
            }
        },
        'DIV_ID'
    )

Pagination: None visible — all notices returned in one call (currently 6 active).
No pagination params found. The component does not paginate.

Detail URL: https://procurement-notices.undp.org/view_negotiation.cfm?nego_id=NNNNN
"""

import logging
import re
import json
from datetime import datetime, date
from typing import Optional

from .base import BaseScraper, TenderData

logger = logging.getLogger('tenders')

BASE_URL    = 'https://www.undp.org/jordan/procurement'
SOURCE_URL  = 'https://public-components.undp.org/?comp=proc_notices&cty_id_c=JOR&style_type=table'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept':          '*/*',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer':         BASE_URL,
    'Origin':          'https://www.undp.org',
}


class UNDPJordanScraper(BaseScraper):
    """
    Scraper for UNDP Jordan Procurement Notices.

    Fetches the public JavaScript component from public-components.undp.org,
    extracts the embedded JSON payload via regex, and parses the columnar
    data arrays into TenderData objects.

    All active Jordan notices are returned in one call (no pagination).
    """

    country_code = 'JO'
    source_name  = 'UNDP Jordan'
    base_url     = BASE_URL

    def scrape(self) -> list[TenderData]:
        from tenders.models import ScraperSource

        try:
            source = ScraperSource.objects.get(
                scraper_class='UNDPJordanScraper',
                is_active=True,
            )
        except ScraperSource.DoesNotExist:
            print('  X  [UNDPJordan] No active ScraperSource — run seed_data')
            return []
        except ScraperSource.MultipleObjectsReturned:
            source = ScraperSource.objects.filter(
                scraper_class='UNDPJordanScraper', is_active=True
            ).first()

        self.session.headers.update(HEADERS)

        rel_src, cat_src = self._reload_keywords_from_db()
        rel_count = len(self._relevance_keywords)
        cat_count = sum(len(v) for v in self._category_keywords.values())

        print(f'\n{"_" * 62}')
        print(f'  SOURCE  : {source.name}')
        print(f'  SCRAPER : UNDPJordanScraper')
        print(f'  MODE    : JS component (embedded JSON)')
        print(f'  URL     : {SOURCE_URL[:65]}')
        print(f'  Keywords: {rel_count} relevance [{rel_src}]'
              f'  |  {cat_count} category [{cat_src}]')
        print(f'{"_" * 62}')

        # ── Fetch JS component ────────────────────────────────────────────
        try:
            resp = self.session.get(SOURCE_URL, timeout=self.timeout)
            resp.raise_for_status()
        except Exception as exc:
            err = str(exc)
            if '403' in err or '401' in err:
                print('  X  [UNDPJordan] Blocked by WAF — server IP not whitelisted')
                logger.warning(f'[UNDPJordan] WAF block: {exc}')
            else:
                print(f'  X  [UNDPJordan] ERROR: {exc}')
                logger.error(f'[UNDPJordan] fetch error: {exc}')
            return []

        js_text = resp.text

        # ── Extract JSON from JS call ─────────────────────────────────────
        # Pattern: functionName({"recordcount":N,...},'DIV_ID')
        m = re.search(r'\(\s*(\{["\']recordcount["\'].*?\})\s*,\s*["\'][^"\']+["\']\)', js_text, re.DOTALL)
        if not m:
            print('  X  [UNDPJordan] Could not extract JSON from JS response')
            print(f'  [DEBUG] Response preview: {js_text[:300]}')
            logger.error('[UNDPJordan] JSON extraction failed')
            return []

        try:
            payload = json.loads(m.group(1))
        except json.JSONDecodeError as exc:
            print(f'  X  [UNDPJordan] JSON parse error: {exc}')
            logger.error(f'[UNDPJordan] JSON parse error: {exc}')
            return []

        record_count = payload.get('recordcount', 0)
        data         = payload.get('data', {})

        print(f'       > records found: {record_count}')

        if not record_count or not data:
            print('  [INFO] No active procurement notices at this time')
            logger.info('[UNDPJordan] recordcount=0')
            return []

        # Debug on first run
        print(f'\n  [DEBUG] columns: {payload.get("columnlist","")}')
        if data.get('title') and data['title']:
            print(f'  [DEBUG] title[0]: {data["title"][0][:70]}')
            print(f'  [DEBUG] notice_id[0]: {(data.get("notice_id") or ["?"])[0]}')
            print(f'  [DEBUG] link[0]: {(data.get("link") or ["?"])[0]}')
            print()

        # ── Parse each record ─────────────────────────────────────────────
        results: list[TenderData] = []

        for i in range(record_count):
            def col(key: str) -> str:
                vals = data.get(key)
                if vals and i < len(vals) and vals[i] is not None:
                    return str(vals[i]).strip()
                return ''

            title     = col('title')
            notice_id = col('notice_id')
            link      = col('link')
            area_desc = col('area_desc')
            posted    = col('formatted_post_date')   # "07-Jun-26"
            deadline  = col('formatted_date')        # "18-Jun-26"
            posted_d  = col('posted_d')              # "2026-06-07 00:00:00.0"
            date_raw  = col('date')                  # "2026-06-18 04:00:00.0"

            if not title:
                continue

            pub_date  = self._parse_undp_date(posted_d) or self._parse_undp_short(posted)
            deadline_ = self._parse_undp_datetime(date_raw) or self._parse_undp_short(deadline)

            # Status: if deadline passed → CLOSED
            today = date.today()
            status = 'CLOSED' if (deadline_ and deadline_ < today) else 'OPEN'

            # Clean title — remove " - UNDP - JORDAN" suffix
            clean_title = re.sub(r'\s*-\s*UNDP\s*-\s*JORDAN\s*$', '', title, flags=re.IGNORECASE).strip()

            combined = f'{clean_title} {area_desc}'

            if self.detect_relevance(combined):
                results.append(TenderData(
                    title               = clean_title,
                    title_ar            = clean_title,
                    source_url          = link or BASE_URL,
                    external_id         = notice_id,
                    reference_no        = notice_id,
                    entity              = 'UNDP Jordan',
                    entity_ar           = 'برنامج الأمم المتحدة الإنمائي — الأردن',
                    description         = f'Area: {area_desc} | Deadline: {deadline}' if area_desc else f'Deadline: {deadline}',
                    category            = self.detect_category(combined),
                    status              = status,
                    currency            = 'USD',
                    estimated_value     = None,
                    publication_date    = pub_date,
                    submission_deadline = deadline_,
                    opening_date        = None,
                ))

        print(f'       > {len(results)} relevant out of {record_count} notices')
        print(f'\n  {"=" * 58}')
        print(f'  TOTAL : {len(results)} relevant UNDP Jordan tender(s)')
        print(f'  {"=" * 58}\n')
        logger.info(f'[UNDPJordan] complete — total={len(results)}, found={record_count}')
        return results

    # ── Date parsing ──────────────────────────────────────────────────────

    @staticmethod
    def _parse_undp_datetime(val: str) -> Optional[date]:
        """Parse '2026-06-07 00:00:00.0' → date."""
        if not val:
            return None
        try:
            return datetime.strptime(val[:10], '%Y-%m-%d').date()
        except ValueError:
            return None

    @staticmethod
    def _parse_undp_short(val: str) -> Optional[date]:
        """Parse '07-Jun-26' → date (assumes 20xx)."""
        if not val:
            return None
        val = val.strip()
        for fmt in ('%d-%b-%y', '%d-%b-%Y'):
            try:
                return datetime.strptime(val, fmt).date()
            except ValueError:
                continue
        return None

    # Alias
    _parse_undp_date = _parse_undp_datetime
