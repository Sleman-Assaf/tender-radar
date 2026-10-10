# newspaper_tenders/splitter.py
# Tender-page selection for full-newspaper PDFs (Al-Ghad).
#
# Al-Ghad publishes the whole ~24-page paper as ONE PDF that carries a real
# text layer. Sending every page to the vision model would cost hours per
# night, so this module scans the text layer for tender markers and exports
# ONLY the candidate pages, each as a small single-page PDF — from there the
# pipeline treats every page exactly like one Al-Rai ads PDF.
#
# The AI stays the final judge of what is and isn't a tender; this filter
# only decides which pages are worth showing it. Marker list errs on the
# inclusive side (a false-positive page costs ~2 minutes of AI time; a
# false negative loses a tender).

import logging

from django.conf import settings

logger = logging.getLogger('tenders')

# Strong markers: one hit is enough to send the page.
STRONG_MARKERS = ['مناقصة', 'استدراج عروض', 'طرح عطاء', 'دعوة عطاء',
                  'إعادة طرح', 'اعادة طرح', 'دعوة تأهيل', 'العطاء رقم',
                  'عطاء رقم']
# Weak marker: needs NEWSPAPER_SPLIT_MIN_HITS occurrences (news articles
# mention "عطاء" once in passing; ads pages repeat it).
WEAK_MARKER = 'عطاء'


class SplitError(Exception):
    """Raised when the PDF cannot be split (no renderer / no text layer)."""


def _min_hits() -> int:
    return int(getattr(settings, 'NEWSPAPER_SPLIT_MIN_HITS', 2))


def _max_pages() -> int:
    # Upper bound on pages sent to the AI per edition — cost protection.
    return int(getattr(settings, 'NEWSPAPER_SPLIT_MAX_PAGES', 8))


def _page_text(page) -> str:
    """Extracted page text, NFKC-normalised so Arabic presentation-form
    glyphs (ﻋ ﻄ ﺎ ء…) collapse to their logical letters before keyword
    matching. Separate function so tests can stub page text directly."""
    import unicodedata
    return unicodedata.normalize('NFKC', page.get_text())


def find_tender_pages(data: bytes) -> list[tuple[int, bytes]]:
    """
    Return [(page_number_1_based, single_page_pdf_bytes), ...] for the pages
    whose text layer suggests tender announcements.

    Raises SplitError when PyMuPDF is unavailable or the PDF has no usable
    text layer (nothing to filter by — refusing loudly beats silently
    sending 24 pages to the AI).
    """
    try:
        import pymupdf
    except ImportError:
        raise SplitError('PyMuPDF is required to split full-newspaper PDFs '
                         '(pip install PyMuPDF)')

    try:
        doc = pymupdf.open(stream=data, filetype='pdf')
    except Exception as exc:
        raise SplitError(f'PDF cannot be opened: {exc}')

    total_text = 0
    candidates: list[tuple[int, int]] = []          # (page_no, score)
    for i in range(doc.page_count):
        text = _page_text(doc[i])
        total_text += len(text)
        strong = sum(text.count(m) for m in STRONG_MARKERS)
        weak = text.count(WEAK_MARKER)
        if strong >= 1 or weak >= _min_hits():
            candidates.append((i + 1, strong * 10 + weak))

    if total_text < 500:
        doc.close()
        raise SplitError('PDF has no usable text layer — cannot locate '
                         'tender pages')

    # Cost cap: keep the highest-scoring pages.
    if len(candidates) > _max_pages():
        dropped = sorted(candidates, key=lambda c: -c[1])[_max_pages():]
        logger.warning(f'[newspaper.split] capping candidate pages at '
                       f'{_max_pages()} — dropped {[p for p, _ in dropped]}')
        keep = {p for p, _ in sorted(candidates, key=lambda c: -c[1])[:_max_pages()]}
        candidates = [c for c in candidates if c[0] in keep]

    pages: list[tuple[int, bytes]] = []
    for page_no, _score in candidates:
        single = pymupdf.open()
        single.insert_pdf(doc, from_page=page_no - 1, to_page=page_no - 1)
        pages.append((page_no, single.tobytes()))
        single.close()
    doc.close()

    logger.info(f'[newspaper.split] {len(pages)} tender-candidate page(s): '
                f'{[p for p, _ in pages]}')
    return pages
