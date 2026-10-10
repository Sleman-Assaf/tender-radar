# newspaper_tenders/page_gate.py
"""
Decide, BEFORE a scanned page goes to the AI, whether it is worth reading.

Al-Rai prints a section title in the banner of every advertisements page:

    إعلانات          ads — tenders mixed with obituaries, company notices…
    إعلانات قضائية   court notices only — إخطار دائرة التنفيذ, مذكرة تبليغ
                     حكم, رقم الدعوى… never a tender
    محليات           local news

The vision model INVENTS tenders from the court pages: on 5 October 2026 one
such page (775093) came back as 36 "دعوة عطاء" rows for ministries that had
advertised nothing, and a second page added ten more — 47 of the day's 54
Al-Rai rows. The crop-OCR check (photo_verify) could not catch them because
their crops did not read as notices. So the page is judged by its printed
header instead, and a court page is never sent to the AI at all. That also
saves about two minutes of AI time per page.

The PDF text layer is useless here (the fonts carry no Unicode mapping, every
page extracts as garbage), so the header band is rendered and read with the
same EasyOCR reader photo_verify already loads. Reading 15 headers of the
5 October edition: 15 of 15 correct, 0.4–1.9 s each on CPU.

Decision rule — precision first, like every gate in this app: a page is
skipped only on a POSITIVE court match. An unreadable header, a missing
renderer or a missing OCR engine all mean "read the page as before", so a
real tender can never be lost to this module.
"""
import io
import logging
import re

from django.conf import settings

logger = logging.getLogger('tenders')

#: Verdict for a court-notices page.
COURT = 'court'

#: The note written on the file row of a skipped page (shown on the Runs page
#: and used by purge_court_page_tenders to recognise pages already judged).
SKIP_NOTE = 'Court-notices page (إعلانات قضائية) — not sent to the AI'

#: The header band: the section title and page number sit in the top-left of
#: the banner on every Al-Rai page (fractions of page width / height).
HEADER_WIDTH_FRACTION  = 0.45
HEADER_HEIGHT_FRACTION = 0.07
HEADER_DPI = 100

# قضائي with the OCR slips seen in practice: hamza-on-ya dropped (قضايي) or
# written as a bare ya; the final ta-marbuta/ha is left open.
_COURT_RE = re.compile(r'قضا[ئي]ي')


def enabled() -> bool:
    return bool(getattr(settings, 'NEWSPAPER_SKIP_COURT_PAGES', True))


def header_reads_as_court(header_text: str) -> bool:
    """
    Pure decision (no OCR): True when the printed section title says the page
    is court notices — "إعلانات قضائية". Unit-tested directly.
    """
    t = ' '.join((header_text or '').split())
    return bool(_COURT_RE.search(t))


def available() -> bool:
    """True when both the renderer (PyMuPDF) and the OCR engine are importable."""
    try:
        import pymupdf  # noqa: F401
    except Exception:
        return False
    from . import photo_verify
    return photo_verify.available()


def render_header(pdf_bytes: bytes):
    """Render the header band of page 1 to an RGB numpy array. May raise."""
    import numpy as np
    import pymupdf
    from PIL import Image

    doc = pymupdf.open(stream=pdf_bytes, filetype='pdf')
    try:
        page = doc[0]
        r = page.rect
        clip = pymupdf.Rect(0, 0, r.width * HEADER_WIDTH_FRACTION,
                            r.height * HEADER_HEIGHT_FRACTION)
        pix = page.get_pixmap(dpi=HEADER_DPI, clip=clip)
        img = Image.open(io.BytesIO(pix.tobytes('png'))).convert('RGB')
        return np.array(img)
    finally:
        doc.close()


def header_text(pdf_bytes: bytes) -> str:
    """OCR the header band to one string. May raise."""
    from . import photo_verify
    band = render_header(pdf_bytes)             # cheap; fails fast on a bad PDF
    reader = photo_verify._get_reader()         # heavy; loaded once per process
    words = reader.readtext(band, detail=0, paragraph=True)
    return ' '.join(words)


def classify_page(pdf_bytes: bytes) -> str:
    """
    COURT when the page's printed header says إعلانات قضائية, else ''.
    Never raises: any failure (no renderer, no OCR, unreadable band) returns ''
    so the page is read exactly as before.
    """
    if not available():
        return ''
    try:
        text = header_text(pdf_bytes)
    except Exception as exc:                                   # noqa: BLE001
        logger.warning(f'[page-gate] header unreadable, page will be read: {exc}')
        return ''
    if header_reads_as_court(text):
        logger.info(f'[page-gate] court-notices page ({text[:40]!r})')
        return COURT
    return ''
