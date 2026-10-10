# tenders/scrapers/gtai_kfw.py
"""
GTAI KfW Tenders Scraper
Source: https://www.gtai.de/en/meta/search/kfw-tenders/

Germany Trade & Invest (GTAI) publishes KfW Development Bank-financed tenders
from around the world. Pure server-rendered HTML — no JS or auth required.

Listing URL:
    https://www.gtai.de/en/meta/search/kfw-tenders/795748!search;{STATE}
        ?page=N        ← 0-based pagination, 10 results per page

Each result card structure (li.result-item):
    .overline__left:
        span.date           "Apr 14, 2026"
        span.overline__text  notice type  "Tender Notice" | "Tender Award"
        span.overline__text  country name "Turkey"
        span.overline__text  sector name  "Building Construction"
    div.content:
        a[href] > h3        title text + detail URL
        p                   "Deadline: May 13, 2026"  (optional)
        p                   "Expression of Interest"  (phase, optional)
        p                   "Financing: KfW Entwicklungsbank"
        p.excerpt           description text
    div.summary:
        a.country           country name link

Detail URL pattern:
    https://www.gtai.de/en/trade/{country}/tenders/{slug}-{ID}
External ID: numeric suffix in slug (e.g. 1990698)
"""

import logging
import re
from datetime import datetime
from typing import Optional
from urllib.parse import urljoin, urlencode

from bs4 import BeautifulSoup

from .base import BaseScraper, TenderData

logger = logging.getLogger('tenders')

BASE_URL  = 'https://www.gtai.de'
# The search URL with the encoded state blob that fixes KfW filter + Tenders rubric
SEARCH_BASE = (
    'https://www.gtai.de/en/meta/search/kfw-tenders/795748!search;'
    'eNqVkUFOxDAMRe_idSsBGlVMD8AFYIdYuKk7ZJQ6xXaAMurdSZFgUUQVdk70v'
    '-33fYEBHZlCe4EuqWdSrVEI1w-lQM6oh_YRTLAneKqA3l3IH7lyfIYWkGdYKp'
    'DUiXdb1_HYNDc7LhcTm8wb2996zZIoWqwP-FbeeyLnMRTryUWO4y_mHdikFsfy'
    '5QfPyI5kG-rhcNvsZDo9o1L5UnGccnmXD9Gj-cjl_K_E9jBP9I_riadyfk3dO'
    'avK84oyopX2X5PCE937jwxwfVXBC7ScQshzo-ShUPc0YApWQwU5mi9M4v5bJcgn'
    '-rEYrp71sSyf-3QUnQ'
)

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept':          'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer':         BASE_URL,
}

# Map country names (as shown by GTAI) → ISO 2-letter codes
COUNTRY_MAP = {
    'albania': 'AL', 'armenia': 'AM', 'azerbaijan': 'AZ',
    'bangladesh': 'BD', 'benin': 'BJ', 'bolivia': 'BO',
    'bosnia and herzegovina': 'BA', 'brazil': 'BR', 'burkina faso': 'BF',
    'burundi': 'BI', 'cambodia': 'KH', 'cameroon': 'CM',
    'central african republic': 'CF', 'chad': 'TD', 'china': 'CN',
    'colombia': 'CO', 'congo': 'CG', 'costa rica': 'CR',
    "côte d'ivoire": 'CI', 'democratic republic of the congo': 'CD',
    'ecuador': 'EC', 'egypt': 'EG', 'el salvador': 'SV',
    'ethiopia': 'ET', 'gabon': 'GA', 'gambia': 'GM',
    'georgia': 'GE', 'germany': 'DE', 'ghana': 'GH',
    'guatemala': 'GT', 'guinea': 'GN', 'guinea-bissau': 'GW',
    'honduras': 'HN', 'india': 'IN', 'indonesia': 'ID',
    'iraq': 'IQ', 'jordan': 'JO', 'kenya': 'KE',
    'kosovo': 'XK', 'kyrgyzstan': 'KG', 'lao': 'LA',
    'lebanon': 'LB', 'liberia': 'LR', 'luxembourg': 'LU',
    'madagaskar': 'MG', 'madagascar': 'MG', 'malawi': 'MW',
    'mali': 'ML', 'mauritania': 'MR', 'mauritius': 'MU',
    'mexico': 'MX', 'moldova': 'MD', 'mongolia': 'MN',
    'montenegro': 'ME', 'morocco': 'MA', 'mozambique': 'MZ',
    'myanmar': 'MM', 'namibia': 'NA', 'nepal': 'NP',
    'nicaragua': 'NI', 'niger': 'NE', 'nigeria': 'NG',
    'north macedonia': 'MK', 'pakistan': 'PK',
    'palestinian territories': 'PS', 'panama': 'PA',
    'paraguay': 'PY', 'peru': 'PE', 'philippines': 'PH',
    'rwanda': 'RW', 'senegal': 'SN', 'serbia': 'RS',
    'somalia': 'SO', 'south africa': 'ZA', 'south sudan': 'SS',
    'sudan': 'SD', 'tajikistan': 'TJ', 'tanzania': 'TZ',
    'togo': 'TG', 'tunesia': 'TN', 'tunisia': 'TN',
    'turkey': 'TR', 'uganda': 'UG', 'ukraine': 'UA',
    'uzbekistan': 'UZ', 'viet nam': 'VN', 'vietnam': 'VN',
    'yemen': 'YE', 'zambia': 'ZM',
}


class GTAIKfWScraper(BaseScraper):
    """
    Scraper for GTAI KfW Development Bank tenders.
    Server-rendered HTML — uses BaseScraper.scrape() → scrape_url() → parse_page().

    TenderingURL in DB:
        url              = https://www.gtai.de/en/meta/search/kfw-tenders/795748!search;{STATE}
        pagination_style = QUERY
        page_param       = page
        max_pages        = 20   (20 × 10 = 200 tenders; full = 105 pages = 1,042)
    """

    country_code = 'DE'    # GTAI is a German agency — filed under Germany
    source_name  = 'GTAI_KfW'
    base_url     = BASE_URL

    def scrape(self) -> list[TenderData]:
        from tenders.models import ScraperSource

        try:
            source = ScraperSource.objects.get(
                scraper_class='GTAIKfWScraper', is_active=True
            )
        except ScraperSource.DoesNotExist:
            print('  ✗  [GTAIKfW] ScraperSource not configured — run seed_data')
            return []
        except ScraperSource.MultipleObjectsReturned:
            source = ScraperSource.objects.filter(
                scraper_class='GTAIKfWScraper', is_active=True
            ).first()

        self.source = source
        self.session.headers.update(HEADERS)
        return super().scrape()

    # ── parse_page: called per page by BaseScraper.scrape_url() ──────────────

    def parse_page(self, soup: BeautifulSoup, tendering_url) -> list[TenderData]:
        """
        Parse one GTAI search results page.

        Each result card:
            <li class="result-item">
              <div class="overline">
                <div class="overline__left">
                  <span class="overline__text date">Apr 14, 2026</span>
                  <span class="overline__text">Tender Notice</span>
                  <span class="overline__text">Turkey</span>
                  <span class="overline__text">Building Construction</span>
                </div>
              </div>
              <div class="content">
                <a href="/en/trade/turkey/tenders/retrofitting-...-1990652">
                  <h3>Retrofitting Works of Educational Buildings...</h3>
                </a>
                <p>Deadline: May 15, 2026</p>
                <p>Financing: KfW Entwicklungsbank</p>
                <p class="excerpt">Requested Services:...</p>
              </div>
            </li>
        """
        cards = soup.select('li.result-item')

        if not cards:
            # Debug on empty page
            preview = soup.get_text(separator=' ', strip=True)[:200]
            print(f'\n  [DEBUG] No li.result-item found. Preview: {preview!r}\n')
            return []

        # Debug first page
        if not getattr(self, '_gtai_first_page_done', False):
            self._gtai_first_page_done = True
            print(f'\n  [DEBUG] {len(cards)} result cards on first page')
            if cards:
                sample = cards[0]
                h3 = sample.select_one('h3')
                print(f'  [DEBUG] Sample title: {h3.get_text(strip=True)[:80] if h3 else "–"}')
            print()

        items: list[TenderData] = []

        for card in cards:
            # ── Overline: date, notice type, country, sector ──────────────────
            overline_spans = card.select('.overline__left .overline__text')
            pub_date_raw  = overline_spans[0].get_text(strip=True) if len(overline_spans) > 0 else ''
            notice_type   = overline_spans[1].get_text(strip=True) if len(overline_spans) > 1 else ''
            country_name  = overline_spans[2].get_text(strip=True) if len(overline_spans) > 2 else ''
            sector_name   = overline_spans[3].get_text(strip=True) if len(overline_spans) > 3 else ''

            # Skip contract awards (already closed)
            if 'award' in notice_type.lower():
                continue

            # ── Title + detail URL ────────────────────────────────────────────
            content_link = card.select_one('div.content a[href]')
            if not content_link:
                continue
            href       = content_link.get('href', '')
            source_url = urljoin(BASE_URL, href)
            h3         = content_link.select_one('h3')
            title      = h3.get_text(strip=True) if h3 else ''
            if not title or len(title) < 5:
                continue

            # ── External ID: numeric suffix of slug ───────────────────────────
            id_match  = re.search(r'--?(\d+)$', href)
            notice_id = id_match.group(1) if id_match else href[-40:]

            # ── Content paragraphs: deadline, phase, description ──────────────
            deadline_raw = ''
            phase        = ''
            description  = ''

            for p in card.select('div.content p'):
                text = p.get_text(strip=True)
                if 'Deadline:' in text:
                    deadline_raw = text.replace('Deadline:', '').strip()
                elif 'Expression of Interest' in text or 'Prequalification' in text:
                    phase = text
                elif p.has_attr('class') and 'excerpt' in p.get('class', []):
                    description = text[:1000]

            if not description:
                description = title

            # ── Status ────────────────────────────────────────────────────────
            # "Tender Award" already filtered above; remaining are open
            if 'cancellation' in notice_type.lower():
                status = 'CLOSED'
            else:
                status = 'OPEN'

            # ── Country → ISO code ────────────────────────────────────────────
            country_code = COUNTRY_MAP.get(country_name.lower().strip(), 'DE')

            # ── Category ──────────────────────────────────────────────────────
            cat_text = f'{title} {sector_name} {notice_type} {phase}'
            category = self.detect_category(cat_text)

            items.append(TenderData(
                title               = title[:300],
                source_url          = source_url,
                external_id         = notice_id,
                reference_no        = notice_id,
                description         = description,
                entity              = 'KfW Entwicklungsbank',
                category            = category,
                status              = status,
                currency            = 'EUR',
                publication_date    = self._parse_gtai_date(pub_date_raw),
                submission_deadline = self._parse_gtai_date(deadline_raw),
                country_code        = country_code,
            ))

        return items

    # ── Date parser ───────────────────────────────────────────────────────────

    @staticmethod
    def _parse_gtai_date(text: str) -> Optional[datetime.date]:
        """
        Parse GTAI date strings:
            "Apr 14, 2026"    ← overline date format
            "May 13, 2026"    ← deadline format
        """
        if not text:
            return None
        val = text.strip()
        for fmt, length in [
            ('%b %d, %Y', None),   # "Apr 14, 2026"
            ('%B %d, %Y', None),   # "April 14, 2026"
            ('%d %b %Y',  None),   # "14 Apr 2026"
            ('%Y-%m-%d',  10),     # ISO
        ]:
            try:
                sliced = val[:length] if length else val
                return datetime.strptime(sliced, fmt).date()
            except ValueError:
                continue
        return None
