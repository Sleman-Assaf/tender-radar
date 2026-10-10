# tenders/services.py

import hashlib
import logging
from typing import Tuple

from django.utils import timezone

from .models import ScraperSource, Tender, CountryConfig
from .scrapers.base import TenderData

logger = logging.getLogger('tenders')

# Source country codes that trigger mirror-copy logic
_MIRROR_SOURCES = {'WB', 'UN', 'DE', 'IB', 'EU', 'ZA', 'AB'}  # ZA=AfricaGateway, AB=AfDB


def _run_ajibrain_enrichment(tender: Tender) -> None:
    """
    Synchronously call AJiBrain API to:
      1. Classify whether the tender is AEC-related  -> sets is_aec
      2. Translate title to Arabic if title_ar missing -> sets title_ar
         The original title field is never modified.
    """
    from .ajibrain_client import classify_and_translate_tender

    updates = classify_and_translate_tender(tender)
    if not updates:
        return

    logger.info(
        f'[ajibrain] #{tender.pk} is_aec={updates.get("is_aec")} '
        f'title_ar={updates.get("title_ar", "")[:60]!r}'
    )
    Tender.objects.filter(pk=tender.pk).update(**updates)


def save_tender_data(td: TenderData, source: ScraperSource,
                     enrich: bool = True) -> Tuple[bool, bool]:
    # `enrich=False` is used by the Newspaper Tenders pipeline
    # (newspaper_tenders/pipeline.py): its tenders arrive ALREADY classified +
    # translated by the same AJiAI implementation, so re-running enrichment
    # here would just duplicate the LLM call. Default behaviour is unchanged.
    # Skip tenders whose deadline has already passed
    today  = timezone.now().date()
    src_cc = (source.country.code if source.country else '').upper()
    is_wb  = src_cc == 'WB'
    if not is_wb and td.submission_deadline is not None and td.submission_deadline < today:
        logger.info(f'[save] SKIPPED past deadline ({td.submission_deadline}): {td.title[:80]}')
        return False, False

    content_hash = _compute_hash(td)
    external_id  = (td.external_id or td.source_url.split('/')[-1])[:200]

    # Resolve country FK
    if td.country_code:
        try:
            country = CountryConfig.objects.get(code=td.country_code.upper())
        except CountryConfig.DoesNotExist:
            country = source.country
    else:
        country = source.country

    # Save / update the primary tender row
    created = updated = False
    primary: Tender | None = None

    try:
        existing = Tender.objects.get(source=source, external_id=external_id)
        if existing.content_hash != content_hash:
            existing.title               = td.title
            existing.title_ar            = td.title_ar
            existing.reference_no        = td.reference_no
            existing.description         = td.description
            existing.description_ar      = td.description_ar
            existing.entity              = td.entity
            existing.entity_ar           = td.entity_ar
            # category is AI-classified (AJiAI) once is_aec_checked — never
            # overwrite it with the scraper's rough guess on content updates
            if not existing.is_aec_checked:
                existing.category        = td.category
            existing.status              = td.status
            existing.estimated_value     = td.estimated_value
            existing.currency            = td.currency or country.currency
            existing.publication_date    = td.publication_date
            existing.submission_deadline = td.submission_deadline
            existing.opening_date        = td.opening_date
            existing.content_hash        = content_hash
            existing.is_relevant         = _is_relevant(td)
            existing.sort_order          = getattr(td, 'sort_order', 0)
            existing.save()
            updated  = True
        primary = existing

    except Tender.DoesNotExist:
        primary = Tender.objects.create(
            source               = source,
            country              = country,
            external_id          = external_id,
            source_url           = td.source_url,
            content_hash         = content_hash,
            title                = td.title,
            title_ar             = td.title_ar,
            reference_no         = td.reference_no,
            description          = td.description,
            description_ar       = td.description_ar,
            entity               = td.entity,
            entity_ar            = td.entity_ar,
            category             = td.category,
            status               = td.status,
            estimated_value      = td.estimated_value,
            currency             = td.currency or country.currency,
            publication_date     = td.publication_date,
            submission_deadline  = td.submission_deadline,
            opening_date         = td.opening_date,
            is_relevant          = _is_relevant(td),
            sort_order           = getattr(td, 'sort_order', 0),
            is_mirror            = False,
        )
        created = True

    except Exception as exc:
        logger.error(f'Failed to save tender "{td.title[:60]}": {exc}')
        return False, False

    # Mirror copies for WB / UN / DE / IB / EU sources
    if primary and src_cc in _MIRROR_SOURCES:
        _save_mirrors(td, source, primary, content_hash)

    # AI enrichment: AEC classification + Arabic translation
    # - AJIBRAIN: runs synchronously per tender immediately after save
    # - OPENAI:   skipped here — batch processing done after full scrape
    if created and primary and enrich:
        try:
            from .models import SiteSettings
            ai_service = SiteSettings.get().ai_service
            logger.debug(f'[save] tender #{primary.pk} ai_service={ai_service!r}')
            if ai_service in ('OPENAI', 'OLLAMA'):
                # Leave is_aec_checked=False — batch processing will handle it
                logger.debug(f'[save] tender #{primary.pk} queued for {ai_service} batch')
            else:
                # AJIBRAIN — run inline per tender
                _run_ajibrain_enrichment(primary)
        except Exception as exc:
            logger.warning(f'[ai] Enrichment failed for #{primary.pk}: {exc}')

    return created, updated


def _save_mirrors(
    td: TenderData,
    source: ScraperSource,
    primary: Tender,
    content_hash: str,
) -> None:
    from .country_extractor import extract_mirror_countries, ensure_country

    mirror_codes = extract_mirror_countries(td)
    if not mirror_codes:
        return

    primary_cc = primary.country.code if primary.country else ''

    for cc in mirror_codes:
        if cc == primary_cc:
            continue

        mirror_country = ensure_country(cc)
        if mirror_country is None:
            continue

        mirror_ext_id = f'{primary.external_id}__mirror__{cc}'[:300]

        try:
            mirror = Tender.objects.get(source=source, external_id=mirror_ext_id)
            if mirror.content_hash != content_hash:
                mirror.title               = td.title
                mirror.title_ar            = td.title_ar
                mirror.reference_no        = td.reference_no
                mirror.description         = td.description
                mirror.description_ar      = td.description_ar
                mirror.entity              = td.entity
                mirror.entity_ar           = td.entity_ar
                mirror.category            = primary.category
                mirror.status              = td.status
                mirror.estimated_value     = td.estimated_value
                mirror.currency            = td.currency or mirror_country.currency
                mirror.publication_date    = td.publication_date
                mirror.submission_deadline = td.submission_deadline
                mirror.opening_date        = td.opening_date
                mirror.content_hash        = content_hash
                mirror.is_relevant         = primary.is_relevant
                mirror.save()
                logger.debug(f'[mirror] Updated mirror {mirror_ext_id}')

        except Tender.DoesNotExist:
            Tender.objects.create(
                source               = source,
                country              = mirror_country,
                external_id          = mirror_ext_id,
                source_url           = td.source_url,
                content_hash         = content_hash,
                title                = td.title,
                title_ar             = td.title_ar,
                reference_no         = td.reference_no,
                description          = td.description,
                description_ar       = td.description_ar,
                entity               = td.entity,
                entity_ar            = td.entity_ar,
                category             = primary.category,
                status               = td.status,
                estimated_value      = td.estimated_value,
                currency             = td.currency or mirror_country.currency,
                publication_date     = td.publication_date,
                submission_deadline  = td.submission_deadline,
                opening_date         = td.opening_date,
                is_relevant          = primary.is_relevant,
                is_mirror            = True,
                mirror_of            = primary,
            )
            logger.info(
                f'[mirror] Created mirror for {primary.external_id} → {cc} '
                f'({mirror_country.name_en})'
            )

        except Exception as exc:
            logger.error(f'[mirror] Failed to save mirror {mirror_ext_id}: {exc}')


def _compute_hash(td: TenderData) -> str:
    content = f'{td.title}|{td.reference_no}|{str(td.submission_deadline)}|{td.status}'
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


def scraping_mode_is_all() -> bool:
    try:
        from .models import SiteSettings
        return SiteSettings.scraping_mode_value() == SiteSettings.ScrapingMode.ALL
    except Exception:
        return False


def _is_relevant(td: TenderData) -> bool:
    if getattr(td, 'force_relevant', False):
        return True
    if scraping_mode_is_all():
        return True
    from .scrapers.base import _fetch_relevance_keywords
    kws, _src = _fetch_relevance_keywords()
    combined  = f"{td.title} {td.title_ar} {td.description} {td.entity}".lower()
    return any(kw in combined for kw in kws)


def run_ai_classification(tender) -> dict:
    """
    Run AJiBrain classify+translate on a single tender.
    Returns dict of fields to update (does NOT save).
    """
    from .ajibrain_client import classify_and_translate_tender
    return classify_and_translate_tender(tender)
