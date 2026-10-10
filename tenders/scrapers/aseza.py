# tenders/scrapers/aseza.py
# Path: TenderRadar/tenders/scrapers/aseza.py
"""
Jordan -- Aqaba Special Economic Zone Authority (ASEZA)
Portal  : https://aseza.jo/AR/Modules/Tenders

Requires valid ASP.NET_SessionId + __AntiXsrfToken cookies.
These are set by the homepage. The scraper must:
  1. GET homepage  → gets ASP.NET_SessionId
  2. GET tenders page with Referer: homepage → gets __AntiXsrfToken + full page
  3. Paginate via UpdatePanel postback
"""

import logging
import random
import re
import time
from datetime import date, datetime
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from .base import BaseScraper, TenderData

logger = logging.getLogger('tenders')

BASE_URL    = 'https://aseza.jo'
LISTING_URL = 'https://aseza.jo/AR/Modules/Tenders'
HOME_URL    = 'https://aseza.jo/Default/Ar'
SM_ID       = 'ctl00$ctl00$ScriptManagerSiteMaster'

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
      'AppleWebKit/537.36 (KHTML, like Gecko) '
      'Chrome/147.0.0.0 Safari/537.36')

_HOME_HEADERS = {
    'User-Agent':                UA,
    'Accept':                    'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
    # Accept-Encoding omitted — let requests handle decompression automatically
    'Accept-Language':           'en-US,en;q=0.9,ar-SA;q=0.8,ar;q=0.7',
    'Cache-Control':             'max-age=0',
    'Sec-CH-UA':                 '"Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
    'Sec-CH-UA-Mobile':          '?0',
    'Sec-CH-UA-Platform':        '"Windows"',
    'Sec-Fetch-Dest':            'document',
    'Sec-Fetch-Mode':            'navigate',
    'Sec-Fetch-Site':            'none',
    'Sec-Fetch-User':            '?1',
    'Upgrade-Insecure-Requests': '1',
    'Priority':                  'u=0, i',
}

_TENDERS_HEADERS = {
    **_HOME_HEADERS,
    'Referer':        HOME_URL,
    'Sec-Fetch-Site': 'same-origin',
}

_AJAX_HEADERS = {
    'User-Agent':       UA,
    'Accept':           '*/*',
    # Accept-Encoding omitted — let requests handle decompression automatically
    'Accept-Language':  'en-US,en;q=0.9,ar-SA;q=0.8,ar;q=0.7',
    'Content-Type':     'application/x-www-form-urlencoded; charset=UTF-8',
    'X-Requested-With': 'XMLHttpRequest',
    'X-MicrosoftAjax':  'Delta=true',
    'Origin':           BASE_URL,
    'Referer':          LISTING_URL,
    'Sec-CH-UA':        '"Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
    'Sec-CH-UA-Mobile': '?0',
    'Sec-CH-UA-Platform': '"Windows"',
    'Sec-Fetch-Dest':   'empty',
    'Sec-Fetch-Mode':   'cors',
    'Sec-Fetch-Site':   'same-origin',
    'Priority':         'u=0',
}


def _parse_date(text: str) -> Optional[date]:
    if not text:
        return None
    for fmt in ('%Y/%m/%d', '%Y-%m-%d', '%d/%m/%Y'):
        try:
            return datetime.strptime(text.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _extract_viewstate(soup: BeautifulSoup) -> dict:
    fields = {}
    for name in ['__VIEWSTATE', '__VIEWSTATEGENERATOR', '__EVENTVALIDATION',
                 '__EVENTTARGET', '__EVENTARGUMENT', '__LASTFOCUS']:
        inp = soup.find('input', {'name': name})
        fields[name] = inp.get('value', '') if inp else ''
    return fields


def _parse_updatepanel(text: str) -> Optional[BeautifulSoup]:
    """Parse ASP.NET UpdatePanel delta response."""
    logger.debug(f'[ASEZA] UpdatePanel ({len(text)}b): {text[:300]!r}')
    html_parts = []
    pos = 0
    while pos < len(text):
        m = re.match(r'(\d+)\|(\w+)\|([^|]*)\|', text[pos:])
        if not m:
            break
        length        = int(m.group(1))
        seg_type      = m.group(2)
        content_start = pos + m.end()
        content       = text[content_start: content_start + length]
        if seg_type == 'updatePanel':
            html_parts.append(content)
        pos = content_start + length
        if pos < len(text) and text[pos] == '|':
            pos += 1
    if html_parts:
        return BeautifulSoup('\n'.join(html_parts), 'html.parser')
    soup = BeautifulSoup(text, 'html.parser')
    if soup.select('a.ReadMore, div.divTenders'):
        return soup
    logger.warning(f'[ASEZA] UpdatePanel parse failed: {text[:200]!r}')
    return None


def _postback_data(vs: dict, event_target: str, cat: str = '0') -> dict:
    return {
        SM_ID:             f'{SM_ID}|{event_target}',
        '__EVENTTARGET':   event_target,
        '__EVENTARGUMENT': '',
        '__LASTFOCUS':     '',
        '__VIEWSTATE':           vs.get('__VIEWSTATE', ''),
        '__VIEWSTATEGENERATOR':  vs.get('__VIEWSTATEGENERATOR', ''),
        '__EVENTVALIDATION':     vs.get('__EVENTVALIDATION', ''),
        '__ASYNCPOST':    'true',
        'ctl00$ctl00$MainContent$ContentDetails$ddlTendersCategory':   cat,
        'ctl00$ctl00$MainContent$ContentDetails$txtFilterName_Tender': '',
        'ctl00$ctl00$MainContent$ContentDetails$txtFilterFrom_Tender': '',
        'ctl00$ctl00$MainContent$ContentDetails$txtFilterTo_Tenders':  '',
    }


def _parse_page(soup: BeautifulSoup, label: str) -> list[TenderData]:
    links = soup.select('a.ReadMore')
    logger.info(f'[ASEZA] {label}: {len(links)} links')
    results = []
    for link in links:
        t_span = link.find('span', class_='titleNews') or \
                 link.select_one('span[id*="lblNewsTitle"]')
        title  = t_span.get_text(strip=True) if t_span else ''
        if not title:
            continue
        b_span = link.find('span', class_='BriefNews') or \
                 link.select_one('span[id*="lblBrief"]')
        desc   = b_span.get_text(strip=True) if b_span else title
        p_span = link.select_one('span[id*="lblOpenDate"]')
        d_span = link.select_one('span[id*="lblTender_PublishDate"]')
        pub    = _parse_date(p_span.get_text(strip=True) if p_span else '')
        dead   = _parse_date(d_span.get_text(strip=True) if d_span else '')
        href   = link.get('href', '').strip()
        url    = urljoin(BASE_URL, href) if href else LISTING_URL
        slug   = href.rstrip('/').split('/')[-1] if href else ''
        ext_id = slug[:200] if slug else url[-100:]
        ref_m  = re.search(r'\(([^)]+)\)', title)
        ref_no = ref_m.group(1)[:100] if ref_m else ''
        results.append(TenderData(
            title               = title,
            title_ar            = title,
            source_url          = url,
            external_id         = ext_id,
            reference_no        = ref_no,
            description         = desc,
            description_ar      = desc,
            entity              = 'سلطة منطقة العقبة الاقتصادية الخاصة',
            entity_ar           = 'سلطة منطقة العقبة الاقتصادية الخاصة',
            category            = 'OTHER',
            status              = 'OPEN',
            publication_date    = pub,
            submission_deadline = dead,
            country_code        = 'JO',
        ))
    return results


def _next_page_target(soup: BeautifulSoup, page: int) -> Optional[str]:
    pager = soup.find('span', id=lambda i: i and 'TendersDataPage' in (i or ''))
    if not pager:
        return None
    for a in pager.find_all('a', class_='NumericClass'):
        if a.get_text(strip=True) == str(page + 1):
            m = re.search(r"__doPostBack\('([^']+)'", a.get('href', ''))
            if m:
                return m.group(1)
    return None


class ASEZAScraper(BaseScraper):

    source_name  = 'ASEZA -- Aqaba SEZ'
    country_code = 'JO'
    base_url     = BASE_URL
    MAX_PAGES    = 10

    def scrape(self) -> list[TenderData]:
        session = requests.Session()
        # Disable automatic header ordering so we can control it
        session.headers.clear()

        logger.info(f'[ASEZA] Starting — {LISTING_URL}')

        # ── Step 1: GET homepage to acquire ASP.NET_SessionId + __AntiXsrfToken ──
        try:
            r0 = session.get(HOME_URL, headers=_HOME_HEADERS, timeout=25)
            cookies_got = list(session.cookies.keys())
            logger.info(f'[ASEZA] Homepage: {r0.status_code} ({len(r0.content)}b) '
                        f'cookies={cookies_got}')
        except Exception as exc:
            logger.warning(f'[ASEZA] Homepage failed: {exc}')

        time.sleep(random.uniform(2.0, 3.5))

        # ── Step 2: GET tenders page with Referer ──────────────────────────────
        try:
            r1 = session.get(LISTING_URL, headers=_TENDERS_HEADERS, timeout=30)
            r1.raise_for_status()
            cookies_got = list(session.cookies.keys())
            logger.info(f'[ASEZA] Tenders GET: {r1.status_code} ({len(r1.content)}b) '
                        f'cookies={cookies_got}')
        except requests.RequestException as exc:
            logger.error(f'[ASEZA] GET failed: {exc}')
            return []

        soup = BeautifulSoup(r1.text, 'html.parser')
        vs   = _extract_viewstate(soup)
        n_links = len(soup.select('a.ReadMore'))
        logger.info(f'[ASEZA] Page1: VIEWSTATE={"OK" if vs["__VIEWSTATE"] else "MISSING"} '
                    f'({len(vs["__VIEWSTATE"])}c) ReadMore={n_links}')

        # ── Step 3: If no VIEWSTATE — session not established, retry once ──────
        if not vs['__VIEWSTATE']:
            logger.info('[ASEZA] VIEWSTATE missing — clearing session and retrying')
            session.cookies.clear()
            time.sleep(random.uniform(3.0, 5.0))

            try:
                r0b = session.get(HOME_URL, headers=_HOME_HEADERS, timeout=25)
                logger.info(f'[ASEZA] Retry homepage: {r0b.status_code} '
                            f'cookies={list(session.cookies.keys())}')
            except Exception as exc:
                logger.warning(f'[ASEZA] Retry homepage failed: {exc}')

            time.sleep(random.uniform(2.0, 3.0))

            try:
                r1b = session.get(LISTING_URL, headers=_TENDERS_HEADERS, timeout=30)
                r1b.raise_for_status()
                soup = BeautifulSoup(r1b.text, 'html.parser')
                vs   = _extract_viewstate(soup)
                n_links = len(soup.select('a.ReadMore'))
                logger.info(f'[ASEZA] Retry tenders: {r1b.status_code} ({len(r1b.content)}b) '
                            f'VIEWSTATE={"OK" if vs["__VIEWSTATE"] else "MISSING"} '
                            f'ReadMore={n_links}')
            except requests.RequestException as exc:
                logger.error(f'[ASEZA] Retry GET failed: {exc}')

        if not vs['__VIEWSTATE']:
            logger.error(
                f'[ASEZA] Cannot get VIEWSTATE. '
                f'Cookies: {list(session.cookies.keys())}. '
                f'HTML snippet: {r1.text[3000:3300]!r}'
            )
            return []

        # ── Paginate ────────────────────────────────────────────────────────────
        all_results: list[TenderData] = []
        seen_ids:    set[str]         = set()
        current_soup = soup
        page = 1

        while page <= self.MAX_PAGES:
            items = _parse_page(current_soup, f'p{page}')
            added = 0
            for item in items:
                if item.external_id not in seen_ids:
                    seen_ids.add(item.external_id)
                    all_results.append(item)
                    added += 1
            logger.info(f'[ASEZA] p{page}: {len(items)} parsed, {added} new '
                        f'(total={len(all_results)})')

            if not items:
                break

            next_t = _next_page_target(current_soup, page)
            if not next_t:
                logger.info(f'[ASEZA] No p{page+1} — done')
                break

            delay = random.uniform(2.0, 3.5)
            logger.info(f'[ASEZA] → p{page+1} (wait {delay:.1f}s)')
            time.sleep(delay)

            data = _postback_data(vs, next_t)
            try:
                rp = session.post(LISTING_URL, data=data,
                                  headers=_AJAX_HEADERS, timeout=30)
                rp.raise_for_status()
                logger.info(f'[ASEZA] p{page+1} POST: {rp.status_code} ({len(rp.content)}b)')
            except requests.RequestException as exc:
                logger.error(f'[ASEZA] p{page+1} POST failed: {exc}')
                break

            panel = _parse_updatepanel(rp.text)
            if panel is None:
                break

            vs_new = _extract_viewstate(panel)
            if vs_new.get('__VIEWSTATE'):
                vs = vs_new

            current_soup = panel
            page += 1

        logger.info(f'[ASEZA] Done — {len(all_results)} tenders')
        return all_results
