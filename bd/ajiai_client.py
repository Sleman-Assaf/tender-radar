# bd/ajiai_client.py
# Path: TenderRadar/bd/ajiai_client.py
#
# Calls the AJiAI /api/extract-cv/ endpoint: raw CV text in, structured JSON
# matching the AJi CV template out. Returns {} on any failure so the BD can
# always fall back to filling the review form manually.

import logging
import requests
from django.conf import settings

logger = logging.getLogger('tenders')

AJIBRAIN_BASE_URL = getattr(settings, 'AJIBRAIN_BASE_URL', 'http://127.0.0.1:8001')

# Keys expected back from AJiAI (all optional)
CV_FIELDS = (
    'full_name', 'current_position', 'profession', 'nationality',
    'date_of_birth', 'years_with_firm', 'memberships',
    'countries_experience', 'synopsis', 'years_experience', 'discipline',
    'education', 'employment', 'experience', 'certifications', 'languages',
)


def extract_cv(cv_text: str) -> dict:
    """Parse raw CV text into structured template fields via AJiAI."""
    if not cv_text or not cv_text.strip():
        return {}
    try:
        resp = requests.post(
            f'{AJIBRAIN_BASE_URL}/api/extract-cv/',
            json={'cvText': cv_text[:16000]},   # keep within model context
            timeout=300,
        )
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            return {}
        result = {k: data[k] for k in CV_FIELDS if k in data}
        logger.info(f'[bd.ajiai] extract-cv OK — fields: {sorted(result.keys())}')
        return result
    except requests.RequestException as exc:
        logger.warning(f'[bd.ajiai] extract-cv API error: {exc}')
        return {}
    except Exception as exc:
        logger.error(f'[bd.ajiai] extract-cv unexpected: {exc}')
        return {}
