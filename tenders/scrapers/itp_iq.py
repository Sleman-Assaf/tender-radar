# tenders/scrapers/itp_iq.py
# Path: TenderRadar/tenders/scrapers/itp_iq.py
"""
ITP.IQ — نافذة العراق الموحدة للإعلانات والمناقصات
Frontend : https://itp.iq/BUSINESS/tenders
API      : POST https://api.itp.iq/api/bus/tenders/get
"""

import logging
import time
import random
from datetime import date, datetime
from typing import Optional

import requests

from .base import BaseScraper, TenderData

logger = logging.getLogger('tenders')

FRONTEND_URL = 'https://itp.iq'
API_URL      = 'https://api.itp.iq/api/bus/tenders/get'
PAGE_SIZE    = 200
MAX_PAGES    = 50

_HEADERS = {
    'User-Agent':      (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept':          'application/json, text/plain, */*',
    'Accept-Language': 'ar-IQ,ar;q=0.9,en-US;q=0.8,en;q=0.7',
    'Content-Type':    'application/json',
    'Origin':          FRONTEND_URL,
    'Referer':         FRONTEND_URL + '/BUSINESS/tenders',
}

STATUS_MAP = {
    'متاحة':      'OPEN',
    'مفتوحة':     'OPEN',
    'active':     'OPEN',
    'open':       'OPEN',
    'تم التنويه': 'OPEN',
    'منتهية':     'CLOSED',
    'مغلقة':      'CLOSED',
    'closed':     'CLOSED',
    'محال':       'AWARDED',
    'awarded':    'AWARDED',
}


def _str(val) -> str:
    """Safely convert any API value to string — handles dicts, lists, None."""
    if val is None:
        return ''
    if isinstance(val, dict):
        for k in ('nameAr', 'name', 'ar', 'value', 'title', 'text', 'label'):
            if k in val and val[k]:
                return str(val[k]).strip()
        vals = [v for v in val.values() if v]
        return str(vals[0]).strip() if vals else ''
    if isinstance(val, list):
        return ', '.join(_str(v) for v in val if v)
    return str(val).strip()


def _get(t: dict, *keys) -> str:
    """Try multiple keys, return first non-empty string value."""
    for k in keys:
        v = t.get(k)
        if v:
            s = _str(v)
            if s:
                return s
    return ''


def _parse_date(value) -> Optional[date]:
    if not value:
        return None
    val = _str(value)
    if 'T' in val:
        val = val[:val.index('T')]
    val = val[:10]
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%Y/%m/%d', '%d-%m-%Y'):
        try:
            return datetime.strptime(val, fmt).date()
        except ValueError:
            continue
    return None


def _map_status(raw: str) -> str:
    r = raw.strip()
    return STATUS_MAP.get(r, STATUS_MAP.get(r.lower(), 'OPEN'))


def _category(t: dict) -> str:
    raw = _get(t, 'tenderType', 'category', 'type', 'categoryAr').lower()
    if any(w in raw for w in ('استشار', 'خبرة', 'consult')):
        return 'CONSULTANCY'
    if any(w in raw for w in ('اشغال', 'انشاء', 'work')):
        return 'WORKS'
    if any(w in raw for w in ('مواد', 'تجهيز', 'بضاع', 'good', 'suppl')):
        return 'GOODS'
    return 'SERVICES'


def _parse_tender(t: dict) -> Optional[TenderData]:
    eid = _get(t, 'id', 'tenderId', '_id', 'tenderCode')
    if not eid:
        return None

    ref_no = _get(t, 'tenderNumber', 'referenceNo', 'reference', 'number', 'tenderCode') or eid

    title_ar = _get(t, 'titleAr', 'title_ar', 'nameAr', 'subjectAr', 'title', 'name') or '(بدون عنوان)'
    title_en = _get(t, 'titleEn', 'title_en', 'nameEn', 'title') or title_ar

    entity_ar = _get(t, 'ownerName', 'ownerNameAr', 'ministry', 'ministryAr',
                     'organization', 'organizationAr', 'entity', 'entityAr',
                     'department', 'departmentAr', 'governorate')
    entity_en = _get(t, 'ownerNameEn', 'ministryEn', 'organizationEn', 'entityEn') or entity_ar

    # مكان التقديم — prepend to description
    submission_place = _get(t, 'submissionPlace', 'location', 'submissionLocation', 'place')
    desc_ar = _get(t, 'description', 'descriptionAr', 'details', 'detailsAr')
    if submission_place and submission_place not in desc_ar:
        desc_ar = f'مكان التقديم: {submission_place}\n{desc_ar}'.strip()
    desc_en = _get(t, 'descriptionEn', 'details') or desc_ar

    status_raw = _get(t, 'status', 'statusAr', 'tenderStatus') or 'متاحة'
    status     = _map_status(status_raw)

    pub_date = _parse_date(
        t.get('publishDate') or t.get('publish_date') or
        t.get('publishedAt') or t.get('createdAt') or t.get('date')
    )
    deadline = _parse_date(
        t.get('closeDate') or t.get('close_date') or
        t.get('endDate') or t.get('end_date') or
        t.get('closingDate') or t.get('closing_date') or
        t.get('deadline')
    )

    value = t.get('estimatedValue') or t.get('value') or t.get('amount')

    url = _get(t, 'url', 'link') or f'{FRONTEND_URL}/BUSINESS/tenders/{eid}'

    return TenderData(
        title               = title_en,
        title_ar            = title_ar,
        description         = desc_en,
        description_ar      = desc_ar,
        entity              = entity_en,
        entity_ar           = entity_ar,
        reference_no        = ref_no[:100],
        external_id         = eid[:200],
        source_url          = url,
        category            = _category(t),
        status              = status,
        publication_date    = pub_date,
        submission_deadline = deadline,
        country_code        = 'IQ',
        estimated_value     = float(value) if value else None,
    )


class ITPIQScraper(BaseScraper):

    source_name  = 'ITP.IQ — Iraq Tenders Portal'
    country_code = 'IQ'
    base_url     = FRONTEND_URL

    def scrape(self) -> list[TenderData]:
        logger.info(f'[ITPIQ] Starting — {API_URL}')
        session = requests.Session()
        session.headers.update(_HEADERS)

        # Log sample keys on first call to help verify field names
        try:
            r0 = session.post(API_URL, json={
                'page': 1, 'count': 1, 'searchTerm': '', 'tags': [], 'ownedBy': 'Organization',
            }, timeout=30)
            r0.raise_for_status()
            d0 = r0.json()
            sample = next((v for v in ([d0] if isinstance(d0, list) else d0.values())
                           if isinstance(v, list) and v), [])
            if sample:
                print(f'  [ITPIQ] API keys: {list(sample[0].keys())}')
                logger.info(f'[ITPIQ] Sample keys: {list(sample[0].keys())}')
            total = d0.get('total') or d0.get('totalCount') or 0 if isinstance(d0, dict) else 0
        except Exception as exc:
            logger.warning(f'[ITPIQ] Probe failed: {exc}')
            total = 0

        all_results: list[TenderData] = []
        seen_ids:    set[str]         = set()
        page = 1

        while page <= MAX_PAGES:
            try:
                r = session.post(API_URL, json={
                    'page': page, 'count': PAGE_SIZE,
                    'searchTerm': '', 'tags': [], 'ownedBy': 'Organization',
                }, timeout=30)
                r.raise_for_status()
                data = r.json()
            except Exception as exc:
                logger.error(f'[ITPIQ] Page {page} failed: {exc}')
                break

            tenders = []
            if isinstance(data, list):
                tenders = data
            elif isinstance(data, dict):
                for key in ('tenders', 'data', 'items', 'results', 'records', 'list'):
                    if key in data and isinstance(data[key], list):
                        tenders = data[key]
                        break
                if not tenders:
                    tenders = next((v for v in data.values()
                                    if isinstance(v, list) and v), [])

            if not tenders:
                logger.info(f'[ITPIQ] Page {page}: empty — done')
                break

            added = 0
            for raw in tenders:
                try:
                    t = _parse_tender(raw)
                except Exception as exc:
                    logger.warning(f'[ITPIQ] Parse error: {exc} — {list(raw.keys()) if isinstance(raw, dict) else raw}')
                    t = None
                if t and t.external_id not in seen_ids:
                    seen_ids.add(t.external_id)
                    all_results.append(t)
                    added += 1

            print(f'       > page {page:>2}  → {added} tenders  (total={len(all_results)})', flush=True)
            logger.info(f'[ITPIQ] Page {page}: {len(tenders)} raw, {added} new')

            if total and len(all_results) >= int(total):
                break
            if len(tenders) < PAGE_SIZE:
                break

            page += 1
            time.sleep(random.uniform(0.5, 1.5))

        logger.info(f'[ITPIQ] Done — {len(all_results)} tenders')
        return all_results

    def parse_page(self, soup, tendering_url):
        return []
