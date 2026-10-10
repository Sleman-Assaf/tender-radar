# tenders/scrapers/syriatender_com.py
"""
SyriaTender.com scraper — https://syriatender.com

The site is a Next.js frontend backed by a public Supabase REST API
(verified against a live capture):

    GET https://yzukdxgztjcpvfklihji.supabase.co/rest/v1/tenders
        ?select=id,title,description,organization,organization_name_snapshot,
                category,status,deadline,budget,created_at,created_by,
                is_featured,source_kind,envelope_mode,
                secure_procurement_enabled,language,owner_verified
        &duplicate_of=is.null
        &or=(extraction_quality.is.null,extraction_quality.eq.full,extraction_quality.eq.partial)
        &status=eq.open
        &or=(deadline.is.null,deadline.gte.<NOW ISO>)
        &order=owner_verified.desc.nullslast,deadline.asc.nullslast,created_at.desc,id.asc
        &offset=0&limit=24

Auth: the site's public *anon* apikey (embedded in the frontend JS) is sent
in both the `apikey` and `Authorization: Bearer` headers.
Total row count arrives in the `Content-Range` header (e.g. `0-23/354`)
when `Prefer: count=exact` is set.
"""

import logging
import time
from datetime import datetime, timezone

import requests

from .base import BaseScraper, TenderData

logger = logging.getLogger('tenders')

PAGE_SIZE = 24
MAX_PAGES = 30          # 30 × 24 = 720 rows — well above the ~354 currently live

SUPABASE_URL  = 'https://yzukdxgztjcpvfklihji.supabase.co/rest/v1/tenders'
# Public anon key shipped with the syriatender.com frontend (not a secret).
SUPABASE_ANON_KEY = (
    'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.'
    'eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inl6dWtkeGd6dGpjcHZma2xpaGppIiwicm9sZSI6'
    'ImFub24iLCJpYXQiOjE3NTI1NzAzMjgsImV4cCI6MjA2ODE0NjMyOH0.'
    'J3bSoGkNhFAYOen8WBNN7hSV2djgZcxClkVsr0ALSMo'
)

SELECT_FIELDS = (
    'id,title,description,organization,organization_name_snapshot,'
    'category,status,deadline,budget,created_at,created_by,is_featured,'
    'source_kind,envelope_mode,secure_procurement_enabled,language,owner_verified'
)


class SyriaTenderComScraper(BaseScraper):
    """Syrian tenders via the syriatender.com public Supabase API."""

    country_code = 'SY'
    source_name  = 'SyriaTender.com'
    base_url     = 'https://syriatender.com'

    HEADERS = {
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/124.0.0.0 Safari/537.36'
        ),
        'Accept':          '*/*',
        'Accept-Language': 'ar,en;q=0.9',
        'apikey':          SUPABASE_ANON_KEY,
        'Authorization':   f'Bearer {SUPABASE_ANON_KEY}',
        'Accept-Profile':  'public',
        'Prefer':          'count=exact',
        'Origin':          'https://syriatender.com',
        'Referer':         'https://syriatender.com/',
    }

    # ── Entry point ───────────────────────────────────────────────────────────

    def scrape(self) -> list[TenderData]:
        from tenders.models import ScraperSource

        try:
            source = ScraperSource.objects.get(
                scraper_class='SyriaTenderComScraper', is_active=True
            )
        except ScraperSource.DoesNotExist:
            print('  ✗  [SyriaTender.com] ScraperSource not configured')
            return []
        except ScraperSource.MultipleObjectsReturned:
            source = ScraperSource.objects.filter(
                scraper_class='SyriaTenderComScraper', is_active=True
            ).first()

        self.session.headers.update(self.HEADERS)

        rel_src, cat_src = self._reload_keywords_from_db()
        rel_count = len(self._relevance_keywords)
        cat_count = sum(len(v) for v in self._category_keywords.values())

        print(f'\n{"─" * 62}')
        print(f'  SOURCE  : {source.name}')
        print(f'  SCRAPER : SyriaTenderComScraper')
        print(f'  MODE    : Supabase REST API (open tenders, {PAGE_SIZE}/page)')
        print(f'  URL     : {SUPABASE_URL}')
        print(f'  Keywords: {rel_count} relevance [{rel_src}]'
              f'  |  {cat_count} category [{cat_src}]')
        print(f'{"─" * 62}')

        now_iso = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')

        seen: set[str] = set()
        results: list[TenderData] = []
        offset  = 0
        page    = 1
        total   = None

        while page <= MAX_PAGES:
            # PostgREST needs BOTH or= filters, so build the query string
            # manually (a dict would collapse the duplicate 'or' key).
            query = (
                f'select={SELECT_FIELDS}'
                f'&duplicate_of=is.null'
                f'&or=(extraction_quality.is.null,extraction_quality.eq.full,'
                f'extraction_quality.eq.partial)'
                f'&status=eq.open'
                f'&or=(deadline.is.null,deadline.gte.{now_iso})'
                f'&order=owner_verified.desc.nullslast,deadline.asc.nullslast,'
                f'created_at.desc,id.asc'
                f'&offset={offset}&limit={PAGE_SIZE}'
            )

            print(f'     page {page:>3} offset={offset}', end='  ', flush=True)

            try:
                resp = self.session.get(
                    f'{SUPABASE_URL}?{query}', timeout=self.timeout
                )
                # Supabase returns 200 or 206 (Partial Content) — both are fine.
                if resp.status_code not in (200, 206):
                    resp.raise_for_status()

                # Total rows from Content-Range: "0-23/354"
                if total is None:
                    cr = resp.headers.get('Content-Range', '')
                    if '/' in cr:
                        try:
                            total = int(cr.split('/')[-1])
                        except ValueError:
                            total = None

                try:
                    rows = resp.json()
                except ValueError:
                    print('✗ invalid JSON')
                    logger.error(f'[SyriaTender.com] page {page}: invalid JSON')
                    break

                if not rows:
                    print('(end)')
                    break

                taken = 0
                for raw in rows:
                    key = str(raw.get('id') or '')
                    if not key or key in seen:
                        continue
                    seen.add(key)

                    tender   = self._parse_row(raw)
                    combined = ' '.join(
                        str(raw.get(f) or '') for f in
                        ('title', 'description', 'organization',
                         'organization_name_snapshot', 'category')
                    )
                    if not self.detect_relevance(combined):
                        continue
                    results.append(tender)
                    taken += 1

                print(f'→ {len(rows)} fetched, {taken} kept'
                      f' (total={total if total is not None else "?"})')
                logger.info(
                    f'[SyriaTender.com] page {page}: '
                    f'fetched={len(rows)} kept={taken} total={total}'
                )

                if len(rows) < PAGE_SIZE:
                    break
                if total is not None and offset + PAGE_SIZE >= total:
                    break

                offset += PAGE_SIZE
                page   += 1
                time.sleep(self.delay)

            except requests.RequestException as exc:
                print(f'✗ {exc}')
                logger.error(f'[SyriaTender.com] page {page} request error: {exc}')
                break
            except Exception as exc:
                print(f'✗ parse error: {exc}')
                logger.error(
                    f'[SyriaTender.com] page {page} error: {exc}', exc_info=True
                )
                break

        print(f'\n  {"═" * 58}')
        print(f'  TOTAL : {len(results)} relevant tender(s)  ←  SyriaTender.com')
        print(f'  {"═" * 58}\n')
        logger.info(f'[SyriaTender.com] scrape() done — {len(results)} tenders')
        return results

    # ── Row parser ────────────────────────────────────────────────────────────

    def _parse_row(self, t: dict) -> TenderData:
        row_id   = str(t.get('id') or '')
        title    = (t.get('title') or '').strip()
        desc     = (t.get('description') or '').strip()
        entity   = (t.get('organization')
                    or t.get('organization_name_snapshot') or '').strip()
        cat_name = (t.get('category') or '').strip()
        budget   = (str(t.get('budget')) if t.get('budget') else '').strip()

        pub_date = self._parse_iso_date(t.get('created_at'))
        deadline = self._parse_iso_date(t.get('deadline'))

        combined = ' '.join([title, desc, entity, cat_name])
        category = self.detect_category(combined) or 'OTHER'

        status_raw = (t.get('status') or '').lower()
        status = {'open': 'OPEN', 'closed': 'CLOSED',
                  'awarded': 'AWARD', 'cancelled': 'CANCEL'}.get(
                      status_raw, 'UNKNOWN')

        # Keep the site's own category label in the description for context.
        description = desc
        if cat_name and cat_name not in description:
            description = f'[{cat_name}] {description}' if description else cat_name
        if budget and budget not in ('غير محدد', '0'):
            description = f'{description}\nBudget: {budget}'

        return TenderData(
            title               = title or cat_name or 'Untitled',
            title_ar            = title,
            description         = description[:4000],
            description_ar      = description[:4000],
            entity              = entity or 'SyriaTender.com',
            entity_ar           = entity,
            reference_no        = row_id[:8],
            external_id         = f'syriatender-com-{row_id}' if row_id else '',
            source_url          = f'{self.base_url}/tender/{row_id}'
                                  if row_id else self.base_url,
            category            = category,
            status              = status,
            publication_date    = pub_date,
            submission_deadline = deadline,
            country_code        = 'SY',
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_iso_date(value):
        """'2026-08-04T08:24:00+00:00' → date, tolerant of None/junk."""
        if not value:
            return None
        val = str(value).strip()
        try:
            return datetime.fromisoformat(val.replace('Z', '+00:00')).date()
        except ValueError:
            try:
                return datetime.strptime(val[:10], '%Y-%m-%d').date()
            except ValueError:
                logger.debug(f'[SyriaTender.com] unparseable date: {value!r}')
                return None
