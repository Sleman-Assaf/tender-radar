# bd/text_extract.py
# Path: TenderRadar/bd/text_extract.py
#
# Text extraction from uploaded CV / project documents.
#   .docx — stdlib only (zipfile + XML), no dependency needed
#   .pdf  — pypdf (add `pypdf>=4.0` to requirements)
#   .txt  — read directly

import logging
import re
import zipfile
from xml.etree import ElementTree as ET

logger = logging.getLogger('tenders')

_W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


def extract_docx_text(fileobj) -> str:
    """Extract plain text from a .docx (paragraphs + tables), stdlib only."""
    try:
        with zipfile.ZipFile(fileobj) as z:
            with z.open('word/document.xml') as f:
                tree = ET.parse(f)
        lines = []
        for para in tree.iter(f'{_W}p'):
            texts = [t.text or '' for t in para.iter(f'{_W}t')]
            line = ''.join(texts).strip()
            if line:
                lines.append(line)
        return '\n'.join(lines)
    except Exception as exc:
        logger.warning(f'[bd.extract] DOCX extraction failed: {exc}')
        return ''


def extract_pdf_text(fileobj) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        try:
            from PyPDF2 import PdfReader  # legacy fallback
        except ImportError:
            logger.warning('[bd.extract] pypdf not installed — cannot read PDF. '
                           'Run: pip install pypdf')
            return ''
    try:
        reader = PdfReader(fileobj)
        pages = [(page.extract_text() or '') for page in reader.pages]
        return '\n'.join(p.strip() for p in pages if p.strip())
    except Exception as exc:
        logger.warning(f'[bd.extract] PDF extraction failed: {exc}')
        return ''


def extract_text(uploaded_file) -> str:
    """Route by extension. Returns '' when nothing could be extracted."""
    name = (getattr(uploaded_file, 'name', '') or '').lower()
    uploaded_file.seek(0)
    if name.endswith('.docx'):
        text = extract_docx_text(uploaded_file)
    elif name.endswith('.pdf'):
        text = extract_pdf_text(uploaded_file)
    elif name.endswith(('.txt', '.md')):
        try:
            text = uploaded_file.read().decode('utf-8', errors='replace')
        except Exception:
            text = ''
    else:
        text = ''
    uploaded_file.seek(0)
    # Normalise whitespace a little
    text = re.sub(r'\r\n?', '\n', text or '')
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()
