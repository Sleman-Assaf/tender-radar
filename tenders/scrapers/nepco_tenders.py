# tenders/scrapers/nepco_tenders.py
"""
NEPCO — National Electric Power Company Jordan — Tenders
URL: https://www.nepco.com.jo/en/Tenders.aspx

What is this?
-------------
NEPCO (شركة الكهرباء الوطنية) is the owner and operator of Jordan's electric
transmission grid and the single buyer of the electrical system. It publishes
procurement tenders on its website covering power substations, transmission
lines, electrical equipment, and engineering services.

NEPCO tenders are highly relevant for AEC/energy consultancies — they cover
large-scale infrastructure: 400kV/132kV GIS substations, transmission lines,
fault recorders, insulators, civil works for substations, etc.

Technology: ASP.NET WebForms with Bootstrap 5 card grid.
Static server-rendered HTML — no JavaScript needed.
All active tenders shown on a single page (no pagination).

HTML structure (confirmed from live page):
    Cards: div.col-lg-6.tender-hover > div.card
    Each card:
        div.card-header:
            span[id$='tenderType_N']  → type e.g. "Bidding"
            span[id$='tenderNo_N']    → ref no e.g. "43/2026"
            span[id$='drawnNo_N']     → draw e.g. "For The First Time"
        div.card-body:
            h5.card-title > span[id$='tenderDesc_N'] → title (Arabic or English)
            span[id$='closeDate_N']   → deadline e.g. "2026-06-21" (ISO YYYY-MM-DD)
            span[id$='copyPrice_N']   → copy price e.g. "125 JOD "
        div.card-footer > a.btn-success[href] → detail page link

Detail URL pattern:
    https://www.nepco.com.jo/en/TenderPage.aspx?q=<encrypted>&sig=<sig>
    (encrypted per-tender URL, stable for the lifetime of the tender)

Sample tenders (confirmed from page):
    43/2026 - استئجار سطحات وونش ورافعة شوكية في مشاريع محطات التحويل
    33/2026 - Operational Study for the Electric System of Jordan (2026-2031)
    32/2026 - Supplying of Fault Recorders
    11/2026 - Supply of 400KV & 132KV Composite Insulators
    8/2026  - RTV Coating for 132/33 KV Mafraq Substation
    3/2026  - Engineering, Supply, Installation for New Aqaba 400/132 KV GIS Substation
    42/2024 - Supply of 132 KV Substation Copper Connectors Clamps

Note on titles: some are Arabic-only, some English-only, some mixed.
The scraper stores the raw title as both title and title_ar.
detect_relevance bypassed — all NEPCO tenders are infrastructure/energy.

Date format: ISO "YYYY-MM-DD" (clean, no parsing needed).
"""

import logging
import re
from datetime import datetime, date
from typing import Optional

from .base import BaseScraper, TenderData

logger = logging.getLogger('tenders')

BASE_URL  = 'https://www.nepco.com.jo'
LIST_URL  = f'{BASE_URL}/en/Tenders.aspx'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept':          'text/html,application/xhtml+xml,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9,ar;q=0.8',
    'Referer':         BASE_URL,
}


class NEPCOTendersScraper(BaseScraper):
    """
    Scraper for NEPCO (National Electric Power Company Jordan) tenders.

    Parses static Bootstrap card grid from a single HTML page.
    All active tenders are on one page — no pagination needed.

    All NEPCO tenders are infrastructure/energy related so relevance
    filter is bypassed (all are accepted).
    """

    country_code = 'JO'
    source_name  = 'NEPCO — National Electric Power Company Jordan'
    base_url     = BASE_URL

    def scrape(self) -> list[TenderData]:
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            print('  X  [NEPCO] beautifulsoup4 not installed')
            return []

        from tenders.models import ScraperSource
        try:
            source = ScraperSource.objects.get(
                scraper_class='NEPCOTendersScraper', is_active=True,
            )
        except ScraperSource.DoesNotExist:
            print('  X  [NEPCO] No active ScraperSource — run seed_data')
            return []
        except ScraperSource.MultipleObjectsReturned:
            source = ScraperSource.objects.filter(
                scraper_class='NEPCOTendersScraper', is_active=True
            ).first()

        self.session.headers.update(HEADERS)

        rel_src, cat_src = self._reload_keywords_from_db()
        rel_count = len(self._relevance_keywords)
        cat_count = sum(len(v) for v in self._category_keywords.values())

        print(f'\n{"_" * 62}')
        print(f'  SOURCE  : {source.name}')
        print(f'  SCRAPER : NEPCOTendersScraper')
        print(f'  MODE    : HTML scraping (Bootstrap card grid, single page)')
        print(f'  URL     : {LIST_URL}')
        print(f'  FILTER  : All accepted (energy/infrastructure entity)')
        print(f'  Keywords: {rel_count} relevance [{rel_src}]'
              f'  |  {cat_count} category [{cat_src}]')
        print(f'{"_" * 62}')

        try:
            resp = self.session.get(LIST_URL, timeout=self.timeout)
            resp.raise_for_status()
        except Exception as exc:
            err = str(exc)
            if '403' in err or '401' in err:
                print(f'  X  [NEPCO] Blocked: {exc}')
            else:
                print(f'  X  [NEPCO] Error: {exc}')
            logger.error(f'[NEPCO] fetch error: {exc}')
            return []

        soup = BeautifulSoup(resp.text, 'html.parser')
        cards = soup.select('div.tender-hover')

        print(f'       > Found {len(cards)} tender card(s)')

        if cards:
            # Debug first card
            c0 = cards[0]
            ref0 = self._span_text(c0, 'tenderNo')
            ttl0 = self._span_text(c0, 'tenderDesc')
            dl0  = self._span_text(c0, 'closeDate')
            print(f'  [DEBUG] ref:      {ref0}')
            print(f'  [DEBUG] title:    {ttl0[:60]}')
            print(f'  [DEBUG] deadline: {dl0}')
            print()

        results: list[TenderData] = []
        today = date.today()

        for card in cards:
            td = self._parse_card(card, today)
            if td:
                results.append(td)

        print(f'\n  {"=" * 58}')
        print(f'  TOTAL : {len(results)} NEPCO tender(s)')
        print(f'  {"=" * 58}\n')
        logger.info(f'[NEPCO] complete — total={len(results)}')
        return results

    # ── Card parser ───────────────────────────────────────────────────────

    def _parse_card(self, card, today: date) -> Optional[TenderData]:
        try:
            ref_no    = self._span_text(card, 'tenderNo')
            ttype     = self._span_text(card, 'tenderType')
            drawn     = self._span_text(card, 'drawnNo')
            raw_title = self._span_text(card, 'tenderDesc')
            close_str = self._span_text(card, 'closeDate')
            price_str = self._span_text(card, 'copyPrice')

            # Clean title — strip "TENDER NO. XX/XXXX\n" prefix if present
            title = self._clean_title(raw_title)
            if not title:
                return None

            # Detail URL
            link_el = card.select_one('div.card-footer a.btn-success')
            if link_el and link_el.get('href'):
                href = link_el['href']
                detail_url = (
                    f'{BASE_URL}/en/{href}'
                    if not href.startswith('http')
                    else href
                )
            else:
                detail_url = LIST_URL

            # Deadline — ISO format YYYY-MM-DD
            deadline = self._parse_iso_date(close_str)

            if deadline and deadline < today:
                status        = 'CLOSED'
                save_deadline = None
            else:
                status        = 'OPEN'
                save_deadline = deadline

            # Estimated value from copy price (not the contract value, just indicator)
            est_value = self._parse_price(price_str)

            description = f'{ttype} — {drawn}' if drawn else ttype
            combined    = f'{title} {description}'

            return TenderData(
                title               = title,
                title_ar            = raw_title.strip(),  # keep original Arabic if present
                source_url          = detail_url,
                external_id         = ref_no,
                reference_no        = ref_no,
                entity              = 'NEPCO — National Electric Power Company',
                entity_ar           = 'شركة الكهرباء الوطنية (نبكو)',
                description         = description,
                category            = self.detect_category(combined),
                status              = status,
                currency            = 'JOD',
                estimated_value     = est_value,
                publication_date    = None,
                submission_deadline = save_deadline,
                opening_date        = None,
            )

        except Exception as exc:
            logger.warning(f'[NEPCO] card parse error: {exc}')
            return None

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _span_text(card, id_suffix: str) -> str:
        """Find span whose id ends with the given suffix and return stripped text."""
        el = card.find('span', id=lambda x: x and x.endswith(id_suffix) or False)
        if not el:
            # Try contains suffix with underscore separator
            el = card.find('span', id=re.compile(rf'_{id_suffix}_?\d*$'))
        return el.get_text(strip=True) if el else ''

    @staticmethod
    def _clean_title(raw: str) -> str:
        """
        Remove boilerplate prefixes like 'TENDER NO. 32/2026\n' or 'tender 33/2026\n'.
        Keep the meaningful description.
        """
        lines = [l.strip() for l in raw.strip().splitlines() if l.strip()]
        if not lines:
            return ''
        # If first line is just a tender number reference, skip it
        if lines and re.match(r'^tender\s+\d+/\d+$', lines[0], re.IGNORECASE):
            lines = lines[1:]
        if lines and re.match(r'^tender\s*no\.?\s*\d+/\d+$', lines[0], re.IGNORECASE):
            lines = lines[1:]
        return ' '.join(lines).strip() if lines else raw.strip()

    @staticmethod
    def _parse_iso_date(val: str) -> Optional[date]:
        """Parse ISO date 'YYYY-MM-DD' → date."""
        if not val:
            return None
        try:
            return datetime.strptime(val.strip(), '%Y-%m-%d').date()
        except ValueError:
            return None

    @staticmethod
    def _parse_price(val: str) -> Optional[float]:
        """Parse '125 JOD ' → 125.0, '0 JOD' → None."""
        m = re.search(r'[\d,]+(?:\.\d+)?', val)
        if m:
            try:
                v = float(m.group().replace(',', ''))
                return v if v > 0 else None
            except ValueError:
                pass
        return None
