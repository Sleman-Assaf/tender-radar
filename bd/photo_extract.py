# bd/photo_extract.py
# Path: TenderRadar/bd/photo_extract.py
#
# Extracts the profile photo from an uploaded CV.
#   .docx — reads word/media/*, picks the first image referenced in the
#           document body (profile photos sit at the top of AJi CVs),
#           skipping tiny images (logos / icons).
#   .pdf  — pypdf page images from the first two pages, largest wins.
#
# EMF/WMF photos (pasted-into-Word metafiles, like Ahmad_Fares.docx) are
# converted to PNG via Pillow when the platform supports it (Windows), or
# via LibreOffice when available; otherwise the original bytes are kept —
# Word still renders EMF fine in the original document, we just can't embed
# it in the generated CV.

import io
import logging
import re
import zipfile
from xml.etree import ElementTree as ET

logger = logging.getLogger('tenders')

MIN_PHOTO_BYTES = 3 * 1024        # ignore icons / bullets / tiny logos
RASTER_EXTS     = ('png', 'jpg', 'jpeg', 'gif', 'bmp', 'tiff')
_R_NS = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'


def _docx_image_order(z: zipfile.ZipFile) -> list:
    """Media targets in the order they are referenced in the document body."""
    try:
        rels = ET.parse(io.BytesIO(z.read('word/_rels/document.xml.rels')))
        rid_to_target = {
            r.get('Id'): r.get('Target', '')
            for r in rels.getroot()
            if 'image' in (r.get('Type') or '')
        }
        body = z.read('word/document.xml').decode('utf-8', errors='replace')
        ordered, seen = [], set()
        for rid in re.findall(r'r:(?:embed|id)="(rId\d+)"', body):
            target = rid_to_target.get(rid, '')
            if target and target not in seen:
                seen.add(target)
                ordered.append('word/' + target.lstrip('/').replace('../', ''))
        return ordered
    except Exception:
        return []


def extract_docx_photo(fileobj):
    """Return (bytes, ext) of the profile photo, or (None, '')."""
    try:
        with zipfile.ZipFile(fileobj) as z:
            media = {n: z.getinfo(n).file_size for n in z.namelist()
                     if n.startswith('word/media/')}
            if not media:
                return None, ''
            candidates = [n for n in _docx_image_order(z)
                          if media.get(n, 0) >= MIN_PHOTO_BYTES]
            # Fallback: media files by name order, large enough
            if not candidates:
                candidates = sorted(n for n, size in media.items()
                                    if size >= MIN_PHOTO_BYTES)
            if not candidates:
                return None, ''
            name = candidates[0]
            ext = name.rsplit('.', 1)[-1].lower()
            return z.read(name), ext
    except Exception as exc:
        logger.warning(f'[bd.photo] docx photo extraction failed: {exc}')
        return None, ''


def extract_pdf_photo(fileobj):
    try:
        from pypdf import PdfReader
    except ImportError:
        return None, ''
    try:
        reader = PdfReader(fileobj)
        best, best_size, best_ext = None, 0, ''
        for page in reader.pages[:2]:
            try:
                for img in page.images:
                    data = img.data or b''
                    if len(data) > best_size and len(data) >= MIN_PHOTO_BYTES:
                        best, best_size = data, len(data)
                        best_ext = (img.name or 'img.png').rsplit('.', 1)[-1].lower()
            except Exception:
                continue
        return (best, best_ext or 'png') if best else (None, '')
    except Exception as exc:
        logger.warning(f'[bd.photo] pdf photo extraction failed: {exc}')
        return None, ''


def _autocrop(png_bytes: bytes) -> bytes:
    """Trim uniform borders (metafile conversions often land the photo on a
    big white/transparent canvas). Returns the input unchanged on failure."""
    try:
        from PIL import Image, ImageChops
        img = Image.open(io.BytesIO(png_bytes)).convert('RGB')
        bg = Image.new('RGB', img.size, img.getpixel((0, 0)))
        bbox = ImageChops.difference(img, bg).getbbox()
        if bbox and (bbox[2] - bbox[0]) > 20 and (bbox[3] - bbox[1]) > 20:
            img = img.crop(bbox)
            buf = io.BytesIO()
            img.save(buf, 'PNG')
            return buf.getvalue()
    except Exception:
        pass
    return png_bytes


def _emf_to_png(data: bytes):
    """Try converting EMF/WMF to PNG. Pillow supports metafiles on Windows;
    LibreOffice is the fallback. Returns PNG bytes or None."""
    # 1) Pillow (works on Windows deployments)
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(data))
        buf = io.BytesIO()
        img.convert('RGB').save(buf, 'PNG')
        return _autocrop(buf.getvalue())
    except Exception:
        pass
    # 2) LibreOffice headless
    try:
        import subprocess, tempfile, os
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, 'photo.emf')
            with open(src, 'wb') as f:
                f.write(data)
            subprocess.run(['soffice', '--headless', '--convert-to', 'png',
                            '--outdir', tmp, src],
                           capture_output=True, timeout=60)
            out = os.path.join(tmp, 'photo.png')
            if os.path.exists(out):
                with open(out, 'rb') as f:
                    return _autocrop(f.read())
    except Exception:
        pass
    return None


def normalise_photo(data: bytes, ext: str):
    """Prefer a raster PNG/JPEG; convert metafiles when possible.
    Returns (bytes, ext)."""
    if not data:
        return None, ''
    ext = (ext or '').lower().lstrip('.')
    if ext in RASTER_EXTS:
        return data, ('jpg' if ext == 'jpeg' else ext)
    if ext in ('emf', 'wmf'):
        png = _emf_to_png(data)
        if png:
            return png, 'png'
        logger.info('[bd.photo] EMF/WMF photo kept as-is (no converter available '
                    'on this host — install Pillow, or it will convert on the '
                    'Windows server).')
        return data, ext
    return data, ext or 'img'


def extract_photo(uploaded_file):
    """Main entry: (bytes, ext) of the CV photo, or (None, '')."""
    name = (getattr(uploaded_file, 'name', '') or '').lower()
    uploaded_file.seek(0)
    if name.endswith('.docx'):
        data, ext = extract_docx_photo(uploaded_file)
    elif name.endswith('.pdf'):
        data, ext = extract_pdf_photo(uploaded_file)
    else:
        data, ext = None, ''
    uploaded_file.seek(0)
    if not data:
        return None, ''
    return normalise_photo(data, ext)
