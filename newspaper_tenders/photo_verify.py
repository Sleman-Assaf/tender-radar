# newspaper_tenders/photo_verify.py
"""
Verify that a tender's newspaper photo actually belongs to that tender.

The AI's TEXT extraction is reliable, but its per-ad image CROP is not — it
sometimes attaches a NEIGHBOURING ad (a court "مذكرة تبليغ حكم" notice, or an
auction) to a real tender. This module reads the crop with OCR and drops the
photo when it clearly belongs to a different, non-tender ad. It never touches
the tender text or its AI classification — only the picture.

Decision (deliberately conservative — a correct photo must never be lost):

    * the crop reads as a court / legal / auction notice AND carries no tender
      word  → WRONG photo (drop it);
    * anything else, including an OCR failure or an unreadable crop → KEEP.

OCR uses EasyOCR (Arabic + English). It is heavy (PyTorch), so it is imported
lazily and every entry point degrades gracefully when it is not installed: the
pipeline then keeps every photo exactly as before.
"""
import io
import logging
import re

logger = logging.getLogger('tenders')

# Arabic-Indic → Latin digits, so a reference like ٨٨/٢٠٢٦ and OCR output compare.
_A2L = str.maketrans('٠١٢٣٤٥٦٧٨٩', '0123456789')

# Phrases that appear in a WRONG crop (court/legal notice or auction) but not in
# a tender's own advertisement. Kept short and OCR-robust: EasyOCR frequently
# misreads محكمة as معكمة, so we key off the surrounding words that survive
# (تبليغ / الدعوى / بالنشر …) rather than محكمة itself.
_NON_TENDER_MARKERS = (
    'تبليغ',       # مذكرة تبليغ / تبليغ حكم — court service notice
    'الدعوى',      # رقم الدعوى — lawsuit number
    'بالنشر',      # حكم بالنشر — judgment by publication
    'صلح حقوق',    # محكمة صلح حقوق — magistrate court
    'الحجز',       # الحجز التنفيذي — execution seizure
    'إخلاء',       # إخطار إخلاء — eviction notice
    'مذكرة',       # مذكرة تبليغ
    'مزاد',        # auction
    'مزايدة',      # auction
)

# Words that mark the crop as a genuine tender advertisement. Their presence
# vetoes a drop, so a construction tender that merely mentions a courthouse is
# never mistaken for a court notice.
_TENDER_MARKERS = ('عطاء', 'مناقصة', 'عروض', 'توريد', 'مناقصه')


def crop_reads_as_non_tender(ocr_text: str) -> bool:
    """
    Pure decision (no OCR): True when `ocr_text` reads like a non-tender notice
    — a court/legal/auction crop with no tender word. Unit-tested directly.
    """
    t = (ocr_text or '').translate(_A2L)
    has_non_tender = any(m in t for m in _NON_TENDER_MARKERS)
    has_tender     = any(m in t for m in _TENDER_MARKERS)
    return has_non_tender and not has_tender


# ── OCR plumbing (lazy, optional) ─────────────────────────────────────────────

_reader = None


def available() -> bool:
    """True when EasyOCR can be imported (so callers can skip cleanly)."""
    try:
        import easyocr  # noqa: F401
        return True
    except Exception:
        return False


def _get_reader():
    global _reader
    if _reader is None:
        import easyocr
        _reader = easyocr.Reader(['ar', 'en'], gpu=False, verbose=False)
    return _reader


def _to_ndarray(data, *, max_width: int = 1000):
    """
    Accept a file path, raw image bytes, or a base64 string; return an RGB
    numpy array downscaled to `max_width` (OCR of a keyword needs no more, and
    smaller is markedly faster on CPU).
    """
    import base64
    import numpy as np
    from PIL import Image

    if isinstance(data, str):
        # A base64 image is long and is NOT a real file on disk. The old check
        # keyed off a leading '/', which is wrong — JPEG base64 begins with
        # "/9j/", so real crops were mistaken for file paths and never decoded
        # (OCR then silently no-op'd). Decide by "is this an existing file?".
        import os
        s = data.strip()
        if len(s) > 256 and not os.path.isfile(s):
            try:
                data = base64.b64decode(s)
            except Exception:
                pass
    img = (Image.open(io.BytesIO(data)) if isinstance(data, (bytes, bytearray))
           else Image.open(data)).convert('RGB')
    if img.width > max_width:
        img = img.resize((max_width, max(1, round(img.height * max_width / img.width))))
    return np.array(img)


def ocr_text(image) -> str:
    """OCR an image (path / bytes / base64) to a single string. May raise."""
    return ' '.join(_get_reader().readtext(_to_ndarray(image), detail=0,
                                           paragraph=True))


def crop_is_degenerate(image, *, min_ratio: float = 0.45) -> bool:
    """
    True when the crop is a degenerate horizontal strip (height/width below
    min_ratio) — a sign the AJiAI box captured only a sliver of the ad instead
    of the whole thing. We cannot re-crop (the service returns no coordinates),
    so the caller shows the tender text-only rather than a misleading half image.

    Best-effort and cheap (no OCR): returns False on any decode error, and only
    catches the wide/short case — a crop clipped on one side keeps a normal
    aspect ratio and cannot be detected this way.
    """
    try:
        import base64
        import os
        from PIL import Image
        data = image
        if isinstance(data, str):
            s = data.strip()
            if len(s) > 256 and not os.path.isfile(s):
                data = base64.b64decode(s)
        img = (Image.open(io.BytesIO(data)) if isinstance(data, (bytes, bytearray))
               else Image.open(data))
        w, h = img.size
        return bool(w and (h / w) < min_ratio)
    except Exception:
        return False


def photo_belongs_to_tender(image) -> bool:
    """
    True (keep the photo) unless the crop clearly reads as a non-tender notice.

    Conservative by design: any OCR failure, missing dependency, or ambiguous
    crop returns True, so a correct photo is never dropped — only crops that
    positively read as a court/auction notice are rejected.
    """
    try:
        text = ocr_text(image)
    except Exception as exc:
        logger.warning(f'[photo-verify] OCR unavailable/failed, keeping photo: {exc}')
        return True
    if crop_reads_as_non_tender(text):
        return False
    return True
