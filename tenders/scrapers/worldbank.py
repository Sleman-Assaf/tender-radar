# tenders/scrapers/worldbank.py
"""
World Bank Procurement Notices scraper
Source: https://projects.worldbank.org/en/projects-operations/procurement

API endpoint (from page source):
    GET https://search.worldbank.org/api/v2/procnotices
        ?format=json
        &fct=procurement_group_desc_exact,notice_type_exact,
             procurement_method_code_exact,procurement_method_name_exact,
             project_ctry_code_exact,project_ctry_name_exact,
             regionname_exact,rregioncode,project_id,
             sector_exact,sectorcode_exact
        &fl=id,bid_description,project_ctry_name,project_name,
            notice_type,notice_status,notice_lang_name,
            submission_date,noticedate
        &srt=submission_date desc,id asc
        &apilang=en
        &rows=20          ← page size
        &os=0             ← offset (0, 20, 40 …)

Response JSON structure:
    {
      "procnotices": {
          "<id>": {
              "id":                "<notice-id>",
              "bid_description":   "Title / description of the notice",
              "project_name":      "Name of the WB project",
              "project_ctry_name": "Country name(s)",
              "notice_type":       "Request for Expression of Interest",
              "notice_status":     "Active",
              "notice_lang_name":  "English",
              "submission_date":   "2026-04-30T00:00:00Z",
              "noticedate":        "2026-04-01T00:00:00Z",
              ...
          },
          ...
      },
      "facets": { … }
    }

Detail page URL pattern:
    https://projects.worldbank.org/en/projects-operations/procurement-detail/<id>

Pagination: offset-based — os=0, os=20, os=40 …
Max pages is controlled by TenderingURL.max_pages (default 10 → 200 notices).
"""

import logging
from datetime import datetime
from typing import Optional

from .base import BaseScraper, TenderData

logger = logging.getLogger('tenders')

BASE_URL    = 'https://projects.worldbank.org'
API_URL     = 'https://search.worldbank.org/api/v2/procnotices'
DETAIL_BASE = f'{BASE_URL}/en/projects-operations/procurement-detail'
PAGE_SIZE   = 20
MAX_PAGES = 10   # default — overridden by TenderingURL.max_pages

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept':          'application/json, text/javascript, */*; q=0.01',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer':         f'{BASE_URL}/en/projects-operations/procurement',
}

# Facet fields requested from the API (match the page source exactly)
FCT_FIELDS = ','.join([
    'procurement_group_desc_exact',
    'notice_type_exact',
    'procurement_method_code_exact',
    'procurement_method_name_exact',
    'project_ctry_code_exact',
    'project_ctry_name_exact',
    'regionname_exact',
    'rregioncode',
    'project_id',
    'sector_exact',
    'sectorcode_exact',
])

# Fields to return per notice
FL_FIELDS = ','.join([
    'id',
    'bid_description',
    'bid_reference_no',
    'project_ctry_name',
    'project_ctry_code',
    'project_name',
    'notice_type',
    'notice_status',
    'notice_lang_name',
    'noticedate',
    'submission_date',
    'submission_deadline_date',
    'submission_deadline_time',
    'procurement_group',
    'procurement_group_desc',
    'procurement_method_name',
    'sector',
    'notice_text',
])


def _effective_max_pages(url_obj, default: int = 10) -> int:
    """Return max_pages from DB setting (scraping mode does NOT affect page count)."""
    return url_obj.max_pages if (url_obj and url_obj.max_pages) else default


class WorldBankScraper(BaseScraper):
    """
    Scraper for World Bank Procurement Notices.

    Uses the JSON search API directly — no HTML parsing needed.
    Offset-based pagination: os=0, os=20, os=40 …

    TenderingURL in DB:
        url              = https://search.worldbank.org/api/v2/procnotices
        pagination_style = SINGLE   (we handle pagination ourselves in scrape())
        max_pages        = 10       (10 × 20 = 200 notices per run)
    """

    country_code = 'WB'        # all notices assigned to WB country
    source_name  = 'WorldBank'
    base_url     = BASE_URL

    def scrape(self) -> list[TenderData]:
        from tenders.models import ScraperSource

        try:
            source = ScraperSource.objects.get(
                scraper_class='WorldBankScraper', is_active=True
            )
        except ScraperSource.DoesNotExist:
            print('  ✗  [WorldBank] ScraperSource not configured — run seed_data')
            return []
        except ScraperSource.MultipleObjectsReturned:
            source = ScraperSource.objects.filter(
                scraper_class='WorldBankScraper', is_active=True
            ).first()

        self.session.headers.update(HEADERS)

        url_obj   = source.tendering_urls.filter(is_active=True).first()
        max_pages = _effective_max_pages(url_obj, default=MAX_PAGES)

        rel_src, cat_src = self._reload_keywords_from_db()
        rel_count = len(self._relevance_keywords)
        cat_count = sum(len(v) for v in self._category_keywords.values())

        print(f'\n{"─" * 62}')
        print(f'  SOURCE  : {source.name}')
        print(f'  SCRAPER : WorldBankScraper')
        print(f'  API     : {API_URL}')
        print(f'  PAGES   : {max_pages} × {PAGE_SIZE} = up to {max_pages * PAGE_SIZE} notices')
        print(f'  Keywords: {rel_count} relevance [{rel_src}]'
              f'  |  {cat_count} category [{cat_src}]')
        print(f'{"─" * 62}')

        all_results: list[TenderData] = []
        seen_ids:    set[str]         = set()

        NOTICE_TYPES = [
            'Request for Expression of Interest',
            'Invitation for Bids',
            'General Procurement Notice',
            'Invitation for Prequalification',
        ]

        for notice_type_filter in NOTICE_TYPES:
            print(f'\n  [WorldBank] ── {notice_type_filter} ──')
            logger.info(f'[WorldBank] Fetching: {notice_type_filter}')

            for page in range(max_pages):
                offset = page * PAGE_SIZE
                print(f'       > page {page + 1:>2}/{max_pages}  (os={offset})',
                      end='  ', flush=True)
                logger.info(f'[WorldBank] {notice_type_filter[:25]} p{page + 1}/{max_pages}')

                params = {
                    'format':                        'json',
                    'fct':                           FCT_FIELDS,
                    'fl':                            FL_FIELDS,
                    'srt':                           'submission_date desc,id asc',
                    'apilang':                       'en',
                    'rows':                          PAGE_SIZE,
                    'showrecent':                    'true',
                    'srce':                          'notices',
                    'os':                            offset,
                    'notice_type_exact':             notice_type_filter,
                    'project_ctry_name_exact':       '',
                    'regionname_exact':              '',
                    'sector.sector_description':     '',
                    'procurement_method_name_exact': '',
                    'procurement_group_desc_exact':  '',
                }

                try:
                    resp = self.post(API_URL, params=params)
                    resp.raise_for_status()
                except Exception as exc:
                    print(f'✗ ERROR: {exc}')
                    logger.error(f'[WorldBank] request failed: {exc}')
                    break

                try:
                    body = resp.json()
                except Exception as exc:
                    print(f'✗ JSON error: {exc}')
                    break

                if page == 0:
                    notices_raw = body.get('procnotices', {})
                    print(f'(total in API: {len(notices_raw)})', end='  ')

                items = self._parse_response(body)

                if not items:
                    print('(empty — next type)')
                    logger.info(f'[WorldBank] p{page + 1}: empty')
                    break

                matched = 0
                for t in items:
                    if t.external_id not in seen_ids:
                        seen_ids.add(t.external_id)
                        combined = (
                            f"{t.title} {t.description or ''} "
                            f"{t.entity or ''} {t.category or ''}"
                        )
                        if self.detect_relevance(combined):
                            all_results.append(t)
                            matched += 1

                print(f'→ {len(items)} notices, {matched} relevant')
                logger.info(f'[WorldBank] p{page + 1}: {len(items)} parsed, {matched} relevant')

        print(f'\n  {"═" * 58}')
        # Sort: known deadlines first (ascending), unknown deadline last
        known   = sorted(
            [t for t in all_results if t.submission_deadline],
            key=lambda t: t.submission_deadline
        )
        unknown = [t for t in all_results if not t.submission_deadline]
        all_results = known + unknown

        # Tag each result with its sort position so DB insertion order is correct
        for i, t in enumerate(all_results):
            t.sort_order = i

        print(f'  TOTAL : {len(all_results)} relevant tender(s)  ←  WorldBank')
        print(f'  (known deadline: {len(known)}, unknown deadline: {len(unknown)})')
        print(f'  {"═" * 58}\n')
        logger.info(f'[WorldBank] scrape() complete — total={len(all_results)} '
                    f'(known={len(known)}, unknown={len(unknown)})')

        return all_results

    # ── Response parser ───────────────────────────────────────────────────────

    # ── procurement_group code → TenderRadar category ───────────────────────
    PROC_GROUP_CATEGORY = {
        'CS': 'ADV',    # Consulting Services → Advisory
        'CW': 'CIVIL',  # Civil Works
        'GO': 'OTHER',  # Goods
        'IT': 'OTHER',  # Information Technology
        'IN': 'OTHER',  # Non-consulting Individual
    }

    def _parse_response(self, body: dict) -> list[TenderData]:
        """
        Parse the JSON response from the World Bank Procurement API.

        Confirmed field mapping from live API response:
            id                      → external_id
            bid_description         → title
            bid_reference_no        → reference_no
            project_name            → entity
            project_ctry_name       → country display name
            notice_type             → notice type label
            notice_status           → 'Published' = active
            noticedate              → '12-Apr-2026' = publication date
            submission_date         → notice posted date (NOT deadline)
            submission_deadline_date→ '2026-04-30T00:00:00Z' = actual deadline
                                      (only present on open bids, not contract awards)
            procurement_group       → 'CS'/'CW'/'GO' code for category
            procurement_method_name → method label
            notice_text             → HTML with full notice details
        """
        from bs4 import BeautifulSoup

        raw = body.get('procnotices', {})
        if isinstance(raw, dict):
            notices = list(raw.values())
        elif isinstance(raw, list):
            notices = raw
        else:
            logger.warning(f'[WorldBank] Unexpected procnotices type: {type(raw)}')
            return []

        items: list[TenderData] = []

        for n in notices:
            if not isinstance(n, dict):
                continue

            notice_id = str(n.get('id', '')).strip()
            if not notice_id:
                continue

            # Skip only explicitly closed/cancelled notices
            # WB uses 'Published' as the active status
            raw_notice_status = (n.get('notice_status') or '').strip().upper()
            CLOSED_STATUSES = {'CLOSED', 'ARCHIVED', 'CANCELLED', 'WITHDRAWN', 'DELETED'}
            if raw_notice_status in CLOSED_STATUSES:
                continue

            # ── Title ─────────────────────────────────────────────────────
            title = (n.get('bid_description') or '').strip()
            if not title or len(title) < 5:
                title = (n.get('project_name') or '').strip()
            if not title:
                continue

            # ── Reference number ──────────────────────────────────────────
            ref_no = (n.get('bid_reference_no') or notice_id).strip()

            # ── Entity (WB project name) ───────────────────────────────────
            project_name = (n.get('project_name') or '').strip()
            entity       = project_name

            # ── Country display name ───────────────────────────────────────
            ctry_name = n.get('project_ctry_name', '')
            if isinstance(ctry_name, list):
                ctry_name = ', '.join(ctry_name)
            ctry_name = ctry_name.strip()

            # ── Notice metadata ───────────────────────────────────────────
            notice_type  = (n.get('notice_type')             or '').strip()
            proc_group   = (n.get('procurement_group')       or '').strip().upper()
            proc_method  = (n.get('procurement_method_name') or '').strip()

            # ── Category: procurement_group code first, then keyword fallback
            category = self.PROC_GROUP_CATEGORY.get(proc_group, None)
            if not category or category == 'OTHER':
                # Try keyword detection on title + method
                cat_text = f'{title} {proc_method} {notice_type}'
                kw_cat   = self.detect_category(cat_text)
                if kw_cat != 'OTHER':
                    category = kw_cat
                else:
                    category = self.PROC_GROUP_CATEGORY.get(proc_group, 'OTHER')

            # ── Status ────────────────────────────────────────────────────
            status = 'CLOSED' if raw_notice_status in CLOSED_STATUSES else 'OPEN'

            # ── Dates ─────────────────────────────────────────────────────
            # submission_deadline_date = actual bid deadline (open bids only)
            # noticedate               = publication date  "12-Apr-2026"
            # submission_date          = notice posted date (same as noticedate usually)
            deadline     = self._parse_wb_date(n.get('submission_deadline_date', ''))
            pub_date     = self._parse_wb_date(n.get('noticedate', ''))

            # ── Description: plain text from notice_text HTML ─────────────
            notice_html = (n.get('notice_text') or '').strip()
            if notice_html:
                try:
                    soup_notice = BeautifulSoup(notice_html, 'html.parser')
                    plain_text  = soup_notice.get_text(separator=' ', strip=True)
                    # Truncate but keep meaningful content
                    description = plain_text[:1000]
                except Exception:
                    description = title
            else:
                description = ' | '.join(filter(None, [
                    title,
                    f'Project: {project_name}' if project_name else '',
                    f'Type: {notice_type}'     if notice_type  else '',
                    f'Method: {proc_method}'   if proc_method  else '',
                    f'Country: {ctry_name}'    if ctry_name    else '',
                ]))

            # ── Detail URL ────────────────────────────────────────────────
            source_url = f'{DETAIL_BASE}/{notice_id}'

            items.append(TenderData(
                title               = title[:300],
                source_url          = source_url,
                external_id         = notice_id,
                reference_no        = ref_no[:200],
                description         = description[:1000],
                entity              = entity[:200],
                category            = category,
                status              = status,
                currency            = 'USD',
                publication_date    = pub_date,
                submission_deadline = deadline,
                country_code        = 'WB',
            ))

        return items

    # ── Date parser ───────────────────────────────────────────────────────────

    @staticmethod
    def _parse_wb_date(text: str) -> Optional[datetime.date]:
        """
        Parse World Bank date strings:
            "2026-04-30T00:00:00Z"   ← ISO with time
            "2026-04-30"             ← ISO date only
            "04/30/2026"             ← US format (rare)
        """
        if not text:
            return None
        val = str(text).strip()
        for fmt, length in [
            ('%Y-%m-%dT%H:%M:%SZ', 20),   # ISO with Z  "2026-04-12T00:00:00Z"
            ('%Y-%m-%dT%H:%M:%S',  19),   # ISO no Z    "2026-04-12T00:00:00"
            ('%Y-%m-%d',           10),   # date only   "2026-04-12"
            ('%d-%b-%Y',           11),   # WB listing  "12-Apr-2026"
            ('%m/%d/%Y',           10),   # US format   "04/12/2026"
            ('%d/%m/%Y',           10),   # EU format   "12/04/2026"
        ]:
            try:
                return datetime.strptime(val[:length], fmt).date()
            except ValueError:
                continue
        return None
