# tenders/scrapers/adfd.py
# Path: TenderRadar/tenders/scrapers/adfd.py
"""
Abu Dhabi Fund for Development (ADFD) -- Tenders
URL: https://www.adfd.ae/en/what-we-do/tenders
API: POST https://www.adfd.ae/api/PublicApi/GetContentList

Browser DevTools captured payload (exact):
  htmlTemplatePath: "\\ajax-templates\\pages\\tenders.html"   <- single backslash in value
  loadResultAsHTML: false
  typeId: 2029, languageId: 1, languageCode: "en-GB"
  imageSize: "full", thumbnailSize: "2580x1900wTransparent"
  isArchived: false, excludeItems: []
  pageIndex: N, pageSize: 9
  Sort: [{Key:"ItemDate1", Operator:"DESC"}]
  FilterGroups: [
    {OR_AND_Operator:"AND", Filters:[Status=Published, Status=Active, Status=NotArchived]},
    {OR_AND_Operator:"AND", Filters:[]}
  ]
"""

import json
import logging
import re
import time
from datetime import date, datetime
from typing import Optional

import requests
from bs4 import BeautifulSoup

from .base import BaseScraper, TenderData

logger = logging.getLogger('tenders')

BASE_URL  = 'https://www.adfd.ae'
PAGE_URL  = 'https://www.adfd.ae/en/what-we-do/tenders'
API_URL   = 'https://www.adfd.ae/api/PublicApi/GetContentList'
PAGE_SIZE = 9
TYPE_ID   = 2029

# The template path as a Python string (single backslash in value)
# JSON-encoded this becomes: "\\ajax-templates\\pages\\tenders.html"
_TEMPLATE_PATH = r'\ajax-templates\pages\tenders.html'

_FILTER_GROUPS = [
    {
        'OR_AND_Operator': 'AND',
        'Filters': [
            {'Key': 'Status', 'Value': 'Published',   'Operator': 'Contains', 'OR_AND_Operator': 'AND'},
            {'Key': 'Status', 'Value': 'Active',       'Operator': 'Contains', 'OR_AND_Operator': 'AND'},
            {'Key': 'Status', 'Value': 'NotArchived',  'Operator': 'Contains', 'OR_AND_Operator': 'AND'},
        ],
    },
    {
        'OR_AND_Operator': 'AND',
        'Filters': [],
    },
]


def _parse_date(text: str) -> Optional[date]:
    if not text:
        return None
    text = str(text).strip()
    m = re.match(r'(\d{4}-\d{2}-\d{2})', text)
    if m:
        try:
            return datetime.strptime(m.group(1), '%Y-%m-%d').date()
        except ValueError:
            pass
    for fmt in ('%d/%m/%Y', '%d %b %Y', '%B %d, %Y', '%d-%m-%Y'):
        try:
            return datetime.strptime(text[:20].strip(), fmt).date()
        except ValueError:
            continue
    return None


def _strip_html(html: str) -> str:
    if not html:
        return ''
    return BeautifulSoup(str(html), 'html.parser').get_text(separator=' ', strip=True)


class ADFDScraper(BaseScraper):
    source_name  = 'UAE -- ADFD (Abu Dhabi Fund for Development)'
    country_code = 'AE'
    base_url     = BASE_URL
    MAX_PAGES    = 50

    def scrape(self) -> list[TenderData]:
        session = requests.Session()
        session.headers.update({
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/124.0.0.0 Safari/537.36'
            ),
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
        })

        logger.info(f'[ADFD] Starting scrape -- {PAGE_URL}')

        # GET page to get session cookies + CSRF token
        try:
            r = session.get(PAGE_URL, timeout=20, headers={
                'Accept': 'text/html,application/xhtml+xml,*/*;q=0.8',
                'Upgrade-Insecure-Requests': '1',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'none',
                'Sec-Fetch-User': '?1',
                'Cache-Control': 'max-age=0',
            })
            logger.info(f'[ADFD] Page GET HTTP {r.status_code} ({len(r.content)}B) '
                        f'cookies={list(session.cookies.keys())}')
            if r.status_code == 403:
                logger.error('[ADFD] 403 geo-blocked')
                return []
        except requests.RequestException as exc:
            logger.error(f'[ADFD] Page GET failed: {exc}')
            return []

        csrf = self._extract_csrf(r.text)
        logger.info(f'[ADFD] CSRF: {len(csrf)} chars')

        # Try all CSRF header strategies
        for strategy_name, extra_headers in [
            ('double-underscore header', {'__RequestVerificationToken': csrf}),
            ('X- prefix header',         {'X-RequestVerificationToken': csrf}),
            ('plain header',             {'RequestVerificationToken': csrf}),
            ('all three headers',        {
                '__RequestVerificationToken': csrf,
                'X-RequestVerificationToken': csrf,
                'RequestVerificationToken':   csrf,
            }),
            ('no CSRF header', {}),
        ]:
            payload = self._build_payload(1)
            body = json.dumps(payload)
            logger.info(
                f'[ADFD] Trying strategy "{strategy_name}" '
                f'| body sample: {body[:100]!r}'
            )

            api_headers = {
                'Accept':           'application/json, text/javascript, */*; q=0.01',
                'Content-Type':     'application/json; charset=UTF-8',
                'Referer':          PAGE_URL,
                'Origin':           BASE_URL,
                'X-Requested-With': 'XMLHttpRequest',
                'Sec-Fetch-Dest':   'empty',
                'Sec-Fetch-Mode':   'cors',
                'Sec-Fetch-Site':   'same-origin',
                **extra_headers,
            }

            try:
                resp = session.post(API_URL, data=body, headers=api_headers, timeout=30)
                logger.info(
                    f'[ADFD] {strategy_name}: HTTP {resp.status_code} '
                    f'({len(resp.content)}B) preview={resp.text[:200]!r}'
                )
                if resp.status_code == 403:
                    continue
                data = resp.json()
                if isinstance(data, dict) and data.get('code') == 500:
                    continue
                # Success — collect all pages
                return self._collect(session, data, api_headers)
            except Exception as exc:
                logger.warning(f'[ADFD] Strategy "{strategy_name}" failed: {exc}')
                continue

        logger.error('[ADFD] All CSRF strategies returned code:500 -- 0 tenders')
        return []

    def _extract_csrf(self, html: str) -> str:
        for pat in [
            r'name=["\']?__RequestVerificationToken["\']?\s[^>]+value=["\']([^"\']{10,})["\']',
            r'value=["\']([^"\']{10,})["\'][^>]+name=["\']?__RequestVerificationToken',
            r'id=["\']?__RequestVerificationToken["\']?[^>]+value=["\']([^"\']{10,})["\']',
        ]:
            m = re.search(pat, html)
            if m:
                return m.group(1)
        return ''

    def _build_payload(self, page: int) -> dict:
        return {
            'htmlTemplatePath': _TEMPLATE_PATH,
            'loadResultAsHTML': False,
            'typeId':           TYPE_ID,
            'languageId':       1,
            'languageCode':     'en-GB',
            'imageSize':        'full',
            'thumbnailSize':    '2580x1900wTransparent',
            'isArchived':       False,
            'excludeItems':     [],
            'pageIndex':        page,
            'pageSize':         PAGE_SIZE,
            'Sort':             [{'Key': 'ItemDate1', 'Operator': 'DESC'}],
            'FilterGroups':     _FILTER_GROUPS,
        }

    def _collect(self, session, first_data: dict, api_headers: dict) -> list[TenderData]:
        all_results: list[TenderData] = []
        seen_ids: set[str] = set()
        data = first_data

        for page_num in range(1, self.MAX_PAGES + 1):
            contents = (
                data.get('Contents') or data.get('contents') or
                data.get('Items')    or data.get('items')    or []
            )
            total = int(
                data.get('TotalCount') or data.get('totalCount') or
                data.get('Total')      or 0
            )
            logger.info(f'[ADFD] Page {page_num}: {len(contents)} items, total={total}')

            if not contents:
                logger.warning(
                    f'[ADFD] Empty page {page_num}. '
                    f'Keys={list(data.keys()) if isinstance(data, dict) else type(data)}'
                )
                break

            for item in contents:
                t = self._parse_item(item)
                if t and t.external_id not in seen_ids:
                    seen_ids.add(t.external_id)
                    all_results.append(t)

            if len(all_results) >= total or len(contents) < PAGE_SIZE:
                break

            time.sleep(0.8)
            payload = self._build_payload(page_num + 1)
            try:
                resp = session.post(API_URL, data=json.dumps(payload),
                                    headers=api_headers, timeout=30)
                data = resp.json()
                if isinstance(data, dict) and data.get('code') == 500:
                    break
            except Exception as exc:
                logger.error(f'[ADFD] Page {page_num + 1} failed: {exc}')
                break

        logger.info(f'[ADFD] Done -- {len(all_results)} unique tenders')
        return all_results

    def _parse_item(self, item: dict) -> Optional[TenderData]:
        def g(*keys):
            for k in keys:
                for v in [item.get(k), item.get(k[0].lower() + k[1:])]:
                    if v:
                        return str(v)
            return ''

        title = _strip_html(g('Title', 'Name'))
        if not title:
            return None

        url = g('DetailURL', 'DetailUrl', 'Url', 'Link')
        if url and not url.startswith('http'):
            url = BASE_URL + '/' + url.lstrip('/')
        url = url or PAGE_URL

        ext_id = g('ItemId', 'Id', 'ContentId') or url.rstrip('/').split('/')[-1][:200]
        ref_m  = re.search(r'\b(T[-/]\d{4}[-/]\d+|\d{4}[-/]\d+)\b', title)

        return TenderData(
            title                = title,
            source_url           = url,
            external_id          = ext_id,
            reference_no         = ref_m.group(0)[:100] if ref_m else '',
            description          = _strip_html(g('Summary', 'Description')) or title,
            entity               = 'Abu Dhabi Fund for Development',
            entity_ar            = 'صندوق أبوظبي للتنمية',
            category             = 'OTHER',
            status               = 'OPEN',
            publication_date     = _parse_date(g('ItemDate2', 'PublishDate')),
            submission_deadline  = _parse_date(g('ItemDate1', 'EndDate', 'ClosingDate')),
            country_code         = 'AE',
        )
