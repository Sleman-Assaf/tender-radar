# magazine_tenders/brief.py
"""
Daily News Brief — موجز الأخبار اليومي — PDF.

One-page-flow, AJi-branded (Yellow #FCD700 / Black / White) PDF listing every
News — الأخبار — item PUBLISHED TODAY, formatted for forwarding to the CEO by
WhatsApp or e-mail. Rendered with PyMuPDF's Story engine (already a project
dependency for the newspaper splitter), which shapes Arabic and handles RTL
natively, using the brand fonts shipped in static/fonts (IBM Plex Sans Arabic
for Arabic per the guidelines; Draft B for the wordmark).

Public API:
    get_today_news()                    → queryset of today's published news
    build_news_brief_pdf(items, day)    → PDF bytes
    save_news_brief(pdf_bytes, day)     → (absolute Path, public /media/… url)
"""

import html
import io
import logging
from pathlib import Path

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

logger = logging.getLogger('tenders')

YELLOW = '#FCD700'
BLACK  = '#000000'
WHITE  = '#ffffff'
INK    = '#1a1a1a'
MUTED  = '#6b6b6b'
LINE   = '#e6e6e6'
TAGLINE = 'Design smart. Build better.'

BRIEF_DIR = 'news_briefs'          # under MEDIA_ROOT / MEDIA_URL


# ─────────────────────────────────────────────────────────────────────────────
# Data selection — identical business rule to the digest's News section
# ─────────────────────────────────────────────────────────────────────────────

def get_today_news(target=None):
    """News — الأخبار — published today (no publication date → scraped today).
    Same display rule as the News page: MAGAZINE_AEC_ONLY + widened AEC."""
    from tenders.aec_scope import aec_display_q
    from tenders.models import Tender

    today = target or timezone.localdate()
    qs = (Tender.objects
          .filter(magazine_meta__isnull=False, is_relevant=True)
          .filter(Q(publication_date=today) |
                  Q(publication_date__isnull=True, scraped_at__date=today))
          .select_related('country', 'source', 'magazine_meta'))
    if getattr(settings, 'MAGAZINE_AEC_ONLY', True):
        qs = qs.filter(aec_display_q())
    return qs.order_by('-scraped_at')


# ─────────────────────────────────────────────────────────────────────────────
# PDF rendering
# ─────────────────────────────────────────────────────────────────────────────

def _e(s) -> str:
    return html.escape(str(s or ''))


def _fonts_archive():
    """Archive over static/fonts so @font-face resolves the brand fonts."""
    import pymupdf
    fonts_dir = Path(settings.BASE_DIR) / 'static' / 'fonts'
    return pymupdf.Archive(str(fonts_dir))


_CSS = f'''
@font-face {{ font-family: plexar; src: url(IBM_Plex_Sans_Arabic_Regular.otf); }}
@font-face {{ font-family: plexar; src: url(IBM_Plex_Sans_Arabic_Bold.otf); font-weight: bold; }}
@font-face {{ font-family: draftb; src: url(draft_b_regular.otf); }}
@font-face {{ font-family: draftb; src: url(draft_b_bold.otf); font-weight: bold; }}

body      {{ font-family: plexar, sans-serif; color: {INK}; font-size: 10pt; }}
.head     {{ background-color: {YELLOW}; padding: 16px 18px 14px; }}
.brand    {{ font-family: draftb; font-weight: bold; font-size: 26pt;
             color: {BLACK}; letter-spacing: -0.5pt; }}
.h-title  {{ font-weight: bold; font-size: 11pt; color: {BLACK}; margin-top: 4px; }}
.h-sub    {{ font-size: 9pt; color: {BLACK}; margin-top: 2px; }}
.rule     {{ background-color: {BLACK}; height: 5px; }}
.count    {{ font-size: 9pt; color: {MUTED}; margin: 10px 0 4px; }}
.item     {{ margin: 8px 0 0; border: 1px solid {LINE}; }}
.i-title  {{ background-color: {YELLOW}; padding: 7px 10px;
             font-weight: bold; font-size: 10.5pt; color: {BLACK}; }}
.i-ar     {{ padding: 6px 10px 0; font-weight: bold; font-size: 10.5pt;
             color: {BLACK}; text-align: right; }}
.i-meta   {{ padding: 5px 10px 2px; font-size: 8.5pt; color: {MUTED}; }}
.i-mag    {{ background-color: {BLACK}; color: {YELLOW}; font-weight: bold;
             font-size: 7.5pt; padding: 1px 6px; letter-spacing: 0.5pt; }}
.i-url    {{ padding: 2px 10px 8px; font-size: 8pt; color: {MUTED}; }}
.i-url a  {{ color: {BLACK}; }}
.empty    {{ margin: 14px 0; padding: 18px; border: 1px solid {LINE};
             color: {MUTED}; font-size: 10pt; text-align: center; }}
.foot     {{ background-color: {BLACK}; padding: 12px 18px; margin-top: 16px; }}
.f-brand  {{ font-family: draftb; font-weight: bold; font-size: 15pt; color: {YELLOW}; }}
.f-tag    {{ font-size: 8.5pt; color: {WHITE}; }}
'''


def _item_html(t) -> str:
    meta = getattr(t, 'magazine_meta', None)
    magazine = (meta.magazine_display_en if meta else '') or ''
    pub = (t.publication_date.strftime('%d %b %Y')
           if t.publication_date else t.scraped_at.strftime('%d %b %Y'))

    title_ar = (getattr(t, 'title_ar', '') or '').strip()
    ar_html = (f'<div class="i-ar" dir="rtl">{_e(title_ar[:220])}</div>'
               if title_ar and title_ar != t.title else '')

    mag_html = f'<span class="i-mag">{_e(magazine.upper())}</span>&nbsp;&nbsp;' if magazine else ''
    entity   = f'&nbsp;&nbsp;&#183;&nbsp;&nbsp;{_e(t.entity[:120])}' if t.entity else ''
    country  = (f'&nbsp;&nbsp;&#183;&nbsp;&nbsp;{_e(t.country.name_en)}'
                if getattr(t, 'country', None) else '')

    src_url = (meta.listing_url if meta and meta.listing_url else t.source_url) or ''
    url_html = (f'<div class="i-url"><a href="{_e(src_url)}">{_e(src_url[:110])}</a></div>'
                if src_url else '')

    return f'''
    <div class="item">
      <div class="i-title">{_e(t.title[:220])}</div>
      {ar_html}
      <div class="i-meta">{mag_html}{pub}{entity}{country}</div>
      {url_html}
    </div>'''


def build_news_brief_pdf(items, target=None) -> bytes:
    """Render the brief for `items` (today's published news) as PDF bytes."""
    import pymupdf

    today = target or timezone.localdate()
    date_label = today.strftime('%A, %d %B %Y')
    items = list(items)

    if items:
        body = ''.join(_item_html(t) for t in items)
        count = (f'<div class="count">{len(items)} news item'
                 f'{"s" if len(items) != 1 else ""} published today</div>')
    else:
        body = '<div class="empty">No news items were published today.</div>'
        count = ''

    doc_html = f'''
    <body>
      <div class="head">
        <div class="brand">AJi</div>
        <div class="h-title">TenderRadar &#8212; Daily News Brief
          &nbsp;&#183;&nbsp; <span dir="rtl">&#1605;&#1608;&#1580;&#1586; &#1575;&#1604;&#1571;&#1582;&#1576;&#1575;&#1585; &#1575;&#1604;&#1610;&#1608;&#1605;&#1610;</span></div>
        <div class="h-sub">{_e(date_label)}</div>
      </div>
      <div class="rule"></div>
      {count}
      {body}
      <div class="foot">
        <span class="f-brand">AJi</span>
        &nbsp;&nbsp;<span class="f-tag">{_e(TAGLINE)}</span>
      </div>
    </body>'''

    story = pymupdf.Story(html=doc_html, user_css=_CSS,
                          archive=_fonts_archive())
    buf = io.BytesIO()
    writer = pymupdf.DocumentWriter(buf)
    mediabox = pymupdf.paper_rect('a4')
    where = mediabox + (30, 30, -30, -30)

    more = 1
    while more:
        dev = writer.begin_page(mediabox)
        more, _ = story.place(where)
        story.draw(dev)
        writer.end_page()
    writer.close()
    return buf.getvalue()


def brief_filename(target=None) -> str:
    today = target or timezone.localdate()
    return f'AJi-News-Brief-{today:%Y-%m-%d}.pdf'


def save_news_brief(pdf_bytes: bytes, target=None):
    """
    Persist the PDF under MEDIA_ROOT/news_briefs/ and return
    (absolute Path, public url path under MEDIA_URL) — the URL is what the
    WhatsApp provider downloads the document from.
    """
    today = target or timezone.localdate()
    out_dir = Path(settings.MEDIA_ROOT) / BRIEF_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = brief_filename(today)
    path = out_dir / fname
    path.write_bytes(pdf_bytes)
    url_path = f'{settings.MEDIA_URL.rstrip("/")}/{BRIEF_DIR}/{fname}'
    logger.info(f'[news-brief] saved {path} ({len(pdf_bytes)} bytes)')
    return path, url_path
