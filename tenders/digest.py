# Path: tenders/digest.py
"""
Daily tender digest e-mail.

Each user receives one aggregate HTML e-mail with the tenders added during the
digest window for their preferred countries (profile favourites; if none,
their focus countries). The window is a rolling 24 hours, except on Sunday —
the first working day after the weekend — when it covers the last 72 hours
(Thursday → Sunday) so nothing added over the weekend is missed.

Sent daily at the configured hour by Celery Beat
(tenders.tasks.send_daily_digest_task), or on demand from the superuser
Manage → Daily Digest page.
"""

import logging
from datetime import timedelta

from django.utils import timezone

logger = logging.getLogger('tenders')

# AJi Brand Guidelines palette — Yellow / White / Black + neutral greys
YELLOW = '#FCD700'   # AJi Yellow (Pantone Yellow 012 C)
YELLOW_SOFT = '#FFF7C2'   # tint of AJi Yellow for highlights
BLACK  = '#000000'
WHITE  = '#ffffff'
INK    = '#111111'   # body text on white
MUTED  = '#6b6b6b'   # secondary text (neutral grey)
LINE   = '#e7e7e4'   # hairlines (neutral grey)
SOFT   = '#f7f7f5'   # section panels
CHIP   = '#efefec'   # neutral pills
BG     = '#eceae4'   # outer canvas
# System typeface for e-mails per the guidelines (brand font license-safe)
FONT   = "'Segoe UI', Arial, Helvetica, sans-serif"
FONT_AR = "Tahoma, 'Segoe UI', Arial, sans-serif"   # Arabic renders best in Tahoma
TAGLINE = 'Design smart. Build better.'

DAILY_HOURS  = 24
SUNDAY_HOURS = 72     # Sunday digest covers Thursday → Sunday


# ─────────────────────────────────────────────────────────────────────────────
# Digest window
# ─────────────────────────────────────────────────────────────────────────────

def get_digest_window(now=None) -> dict:
    """
    The period the digest covers: the last 24 hours, or the last 72 hours on
    Sunday (server local time — Asia/Amman). Returns
    {'since', 'until', 'hours', 'label', 'range_label', 'is_weekend'}.
    """
    until = now or timezone.now()
    local_until = timezone.localtime(until)
    is_weekend = local_until.weekday() == 6          # Sunday
    hours = SUNDAY_HOURS if is_weekend else DAILY_HOURS
    since = until - timedelta(hours=hours)
    local_since = timezone.localtime(since)
    return {
        'since': since,
        'until': until,
        'hours': hours,
        'is_weekend': is_weekend,
        'label': f'the last {hours} hours',
        'range_label': (f'{local_since:%a %d %b, %H:%M} &#8594; '
                        f'{local_until:%a %d %b %Y, %H:%M}'),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Data selection
# ─────────────────────────────────────────────────────────────────────────────

def get_user_country_codes(user) -> list[str]:
    """Preferred countries: profile favourites first; fall back to focus countries."""
    profile = getattr(user, 'profile', None)
    if not profile:
        return []
    codes = profile.get_favorite_codes()
    if not codes:
        codes = profile.get_focus_country_codes()
    return codes


def get_digest_tenders(country_codes: list[str], since, user=None):
    """Tenders added since `since` for the given countries, AEC-locked if the user is."""
    from .models import Tender
    if not country_codes:
        return Tender.objects.none()

    qs = (Tender.objects
          .filter(country__code__in=country_codes,
                  is_relevant=True,
                  scraped_at__gte=since)
          .select_related('country', 'source'))

    profile = getattr(user, 'profile', None) if user else None
    if profile and not profile.can_see_all_tenders:
        qs = qs.filter(is_aec=True)

    # Newspaper Tenders and News rows have their own digest sections (below),
    # so keep them out of the per-country tender sections — otherwise the same
    # item would be listed twice in one e-mail.
    qs = qs.filter(newspaper_meta__isnull=True, magazine_meta__isnull=True)

    return qs.order_by('country__name_en', '-publication_date', '-scraped_at')


def get_digest_newspaper_tenders(since):
    """
    Newspaper Tenders — عطاءات الجريدة — scraped during the digest window.

    Included for every recipient regardless of preferred countries: the printed
    Jordanian dailies are AJi's home market, so the digest always carries them
    in their own section. Mirrors the Newspaper page's display rule
    (NEWSPAPER_AEC_ONLY + the widened AEC scope from tenders/aec_scope.py).
    """
    from django.conf import settings
    from .aec_scope import aec_display_q
    from .models import Tender

    qs = (Tender.objects
          .filter(newspaper_meta__isnull=False,
                  is_relevant=True,
                  scraped_at__gte=since)
          .select_related('country', 'source', 'newspaper_meta',
                          'newspaper_meta__edition'))
    if getattr(settings, 'NEWSPAPER_AEC_ONLY', True):
        qs = qs.filter(aec_display_q())
    return qs.order_by('-scraped_at')


def get_digest_news(since):
    """
    News — الأخبار — items PUBLISHED during the digest window on the magazine
    websites. The publisher's date is a calendar day, so every day the window
    touches counts (24 h → yesterday + today; Sunday's 72 h → Thursday to
    Sunday). Undated items fall back to "scraped during the window". Same
    display rule as the News page (MAGAZINE_AEC_ONLY + the widened AEC scope).
    Not filtered by the user's countries: news items are regional by nature.
    """
    from django.conf import settings
    from django.db.models import Q
    from .aec_scope import aec_display_q
    from .models import Tender

    today = timezone.localdate()
    first_day = timezone.localtime(since).date()
    qs = (Tender.objects
          .filter(magazine_meta__isnull=False,
                  is_relevant=True)
          .filter(Q(publication_date__gte=first_day,
                    publication_date__lte=today) |
                  Q(publication_date__isnull=True, scraped_at__gte=since))
          .select_related('country', 'source', 'magazine_meta'))
    if getattr(settings, 'MAGAZINE_AEC_ONLY', True):
        qs = qs.filter(aec_display_q())
    return qs.order_by('-publication_date', '-scraped_at')


# ─────────────────────────────────────────────────────────────────────────────
# HTML rendering (inline styles + table layout — required for e-mail clients)
# ─────────────────────────────────────────────────────────────────────────────

def _esc(s) -> str:
    from django.utils.html import escape
    return escape(str(s or ''))


def _plural(n: int, word: str) -> str:
    return f'{n} {word}{"s" if n != 1 else ""}'


def _pill(text: str, bg: str = CHIP, fg: str = INK, cls: str = 'bg-chip tx-ink',
          bold: bool = False) -> str:
    """Rounded label chip (square in Outlook desktop — degrades cleanly)."""
    weight = 'bold' if bold else 'normal'
    return (f'<span class="{cls}" style="display:inline-block;background-color:{bg};'
            f'color:{fg};font-size:11px;line-height:16px;font-weight:{weight};'
            f'padding:3px 10px;border-radius:999px;font-family:{FONT};'
            f'margin:0 6px 6px 0;white-space:nowrap">{text}</span>')


def _btn(url: str, text: str, primary: bool = True) -> str:
    if primary:
        return (f'<a href="{_esc(url)}" class="bg-black tx-yellow" '
                f'style="display:inline-block;background-color:{BLACK};color:{YELLOW};'
                f'font-size:12px;font-weight:bold;text-decoration:none;'
                f'padding:9px 18px;border-radius:8px;font-family:{FONT};'
                f'letter-spacing:.3px;border:1px solid {BLACK};'
                f'margin:0 8px 0 0">{text}</a>')
    return (f'<a href="{_esc(url)}" class="bg-white tx-black" '
            f'style="display:inline-block;background-color:{WHITE};color:{BLACK};'
            f'font-size:12px;font-weight:bold;text-decoration:none;'
            f'padding:9px 16px;border-radius:8px;font-family:{FONT};'
            f'border:1px solid #cfcfca">{text}</a>')


def _arabic(t, size: int = 14) -> str:
    title_ar = (getattr(t, 'title_ar', '') or '').strip()
    if not title_ar or title_ar == t.title:
        return ''
    return (f'<div class="tx-ink" dir="rtl" style="font-size:{size}px;line-height:1.8;'
            f'color:{INK};font-family:{FONT_AR};margin-top:6px;text-align:right">'
            f'{_esc(title_ar[:220])}</div>')


def _title(t, size: int = 15) -> str:
    return (f'<div class="tx-ink" style="font-size:{size}px;line-height:1.45;color:{INK};'
            f'font-weight:bold;font-family:{FONT}">{_esc(t.title[:220])}</div>')


def _pub_label(t) -> str:
    return (t.publication_date.strftime('%d %b %Y')
            if t.publication_date else t.scraped_at.strftime('%d %b %Y'))


def _deadline_pill(t) -> str:
    if not t.submission_deadline:
        return _pill('Deadline: &#8212;')
    days = (t.submission_deadline - timezone.localdate()).days
    date = t.submission_deadline.strftime('%d %b %Y')
    if days < 0:
        return _pill(f'Closed &#183; {date}', bg=CHIP, fg=MUTED, cls='bg-chip tx-muted')
    if days <= 7:
        left = 'today' if days == 0 else f'{_plural(days, "day")} left'
        return _pill(f'&#9200; {date} &#183; {left}', bg=BLACK, fg=YELLOW,
                     cls='bg-black tx-yellow', bold=True)
    return _pill(f'Deadline {date} &#183; {days} days', bg=YELLOW_SOFT, fg=BLACK,
                 cls='bg-ysoft tx-black', bold=True)


def _card(inner: str, accent: str = YELLOW, accent_cls: str = 'bg-yellow') -> str:
    """One item as a rounded white card with a coloured accent bar on the left."""
    return f'''
    <tr><td style="padding:0 14px 10px">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
             class="bg-white" style="border-collapse:separate;background-color:{WHITE};
             border:1px solid {LINE};border-radius:12px">
        <tr>
          <td width="5" class="{accent_cls}" style="width:5px;background-color:{accent};
              border-radius:12px 0 0 12px;font-size:0;line-height:0">&nbsp;</td>
          <td style="padding:16px 18px 12px">{inner}</td>
        </tr>
      </table>
    </td></tr>'''


def _tender_row(t, site_url: str) -> str:
    entity = (f'<div class="tx-muted" style="font-size:12px;color:{MUTED};'
              f'margin-top:8px;font-family:{FONT_AR}">&#127970;&nbsp; '
              f'{_esc(t.entity)}</div>') if t.entity else ''

    chips = _pill(f'Published {_pub_label(t)}')
    if t.reference_no:
        chips += _pill(f'Ref {_esc(t.reference_no)}')
    chips += _deadline_pill(t)

    links = ''
    if site_url:
        links += _btn(f"{site_url.rstrip('/')}/tenders/{t.pk}/", 'Open in TenderRadar')
    if t.source_url:
        links += _btn(t.source_url, 'Source portal &#8599;', primary=False)

    inner = (f'{_title(t)}{_arabic(t)}{entity}'
             f'<div style="margin-top:10px">{chips}</div>'
             f'<div style="margin-top:6px">{links}</div>')
    return _card(inner)


def _newspaper_row(t, site_url: str) -> str:
    """
    One Newspaper Tender row: the small ad photo (the crop of the printed
    announcement) beside the title. The image is hosted by TenderRadar
    itself, so without a reachable site_url it is left out rather than
    rendered as a broken icon.
    """
    meta = getattr(t, 'newspaper_meta', None)
    img_url = ''
    if site_url and meta and meta.ad_image:
        img_url = f"{site_url.rstrip('/')}{meta.ad_image.url}"

    paper = (((meta.edition.source_display_en if meta and meta.edition else '')
              or (t.source.name if t.source else '')))
    chips = ''
    if paper:
        chips += _pill(_esc(paper.upper()), bg=BLACK, fg=YELLOW,
                       cls='bg-black tx-yellow', bold=True)
    chips += _pill(f'Published {_pub_label(t)}')
    if t.submission_deadline:
        chips += _deadline_pill(t)

    entity = (f'<div class="tx-muted" style="font-size:12px;color:{MUTED};'
              f'margin-top:8px;font-family:{FONT_AR}">&#127970;&nbsp; '
              f'{_esc(t.entity[:120])}</div>') if t.entity else ''

    detail_url = f"{site_url.rstrip('/')}/tenders/{t.pk}/" if site_url else ''
    links = _btn(detail_url, 'Open in TenderRadar') if detail_url else ''

    text = (f'{_title(t, 14)}{_arabic(t, 13)}{entity}'
            f'<div style="margin-top:10px">{chips}</div>'
            f'<div style="margin-top:6px">{links}</div>')

    if not img_url:
        return _card(text)

    # Two-cell table layout — the only float that survives every Outlook.
    inner = f'''
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
               style="border-collapse:collapse">
          <tr>
            <td width="96" valign="top" style="padding:0 14px 0 0">
              <a href="{_esc(detail_url)}">
                <img src="{_esc(img_url)}" width="90" alt="Tender photo"
                     style="display:block;width:90px;max-width:90px;height:auto;
                            border:1px solid {LINE};border-radius:8px"/>
              </a>
            </td>
            <td valign="top">{text}</td>
          </tr>
        </table>'''
    return _card(inner)


def _news_row(t, site_url: str) -> str:
    """One News — الأخبار — item: title (+ Arabic), magazine, date, links."""
    meta = getattr(t, 'magazine_meta', None)
    magazine = (meta.magazine_display_en if meta else '') or ''

    chips = ''
    if magazine:
        chips += _pill(_esc(magazine.upper()), bg=BLACK, fg=YELLOW,
                       cls='bg-black tx-yellow', bold=True)
    chips += _pill(_pub_label(t))
    if getattr(t, 'country', None):
        chips += _pill(_esc(t.country.name_en))

    entity = (f'<div class="tx-muted" style="font-size:12px;color:{MUTED};'
              f'margin-top:8px;font-family:{FONT_AR}">&#127970;&nbsp; '
              f'{_esc(t.entity[:120])}</div>') if t.entity else ''

    links = ''
    if site_url:
        links += _btn(f"{site_url.rstrip('/')}/tenders/{t.pk}/", 'Open in TenderRadar')
    src_url = (meta.listing_url if meta and meta.listing_url else t.source_url)
    if src_url:
        links += _btn(src_url, 'Read article &#8599;', primary=False)

    inner = (f'{_title(t, 14)}{_arabic(t, 13)}{entity}'
             f'<div style="margin-top:10px">{chips}</div>'
             f'<div style="margin-top:6px">{links}</div>')
    return _card(inner, accent=BLACK, accent_cls='bg-black')


def _section(title_html: str, count_text: str, rows: str, extra: str = '') -> str:
    """A rounded soft panel: header (title + count pill) then the item cards."""
    return f'''
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
             class="bg-soft" style="margin:0 0 22px;border-collapse:separate;
             background-color:{SOFT};border:1px solid {LINE};border-radius:16px">
        <tr>
          <td style="padding:16px 18px 14px">
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
              <tr>
                <td valign="middle">{title_html}{extra}</td>
                <td valign="middle" align="right" style="white-space:nowrap">
                  <span class="bg-black tx-yellow" style="display:inline-block;
                        background-color:{BLACK};color:{YELLOW};font-size:11px;
                        font-weight:bold;padding:4px 12px;border-radius:999px;
                        font-family:{FONT};letter-spacing:.4px">{count_text}</span>
                </td>
              </tr>
            </table>
          </td>
        </tr>
        {rows}
        <tr><td style="height:4px;font-size:0;line-height:0">&nbsp;</td></tr>
      </table>'''


def _section_title(en: str, ar: str = '') -> str:
    ar_html = (f'<span class="tx-muted" style="font-size:14px;color:{MUTED};'
               f'font-family:{FONT}">&nbsp;&nbsp;&#183;&nbsp;&nbsp;</span>'
               f'<span class="tx-muted" dir="rtl" style="font-size:14px;color:{MUTED};'
               f'font-family:{FONT_AR};font-weight:bold">{ar}</span>') if ar else ''
    return (f'<span class="tx-black" style="font-size:15px;font-weight:bold;color:{BLACK};'
            f'font-family:{FONT};letter-spacing:.2px">{_esc(en)}</span>{ar_html}')


def _stat_cell(value: int, label: str, highlight: bool = False) -> str:
    bg, cls = (YELLOW, 'bg-yellow') if highlight else (WHITE, 'bg-white')
    return f'''
            <td width="33%" valign="top" style="padding:0 5px">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
                     class="{cls}" style="border-collapse:separate;background-color:{bg};
                     border:1px solid {LINE};border-radius:12px">
                <tr><td align="center" style="padding:14px 8px 12px">
                  <div class="tx-black" style="font-size:26px;line-height:1;font-weight:bold;
                              color:{BLACK};font-family:{FONT}">{value}</div>
                  <div class="tx-muted" style="font-size:10px;color:{MUTED};margin-top:6px;
                              letter-spacing:.8px;font-weight:bold;font-family:{FONT};
                              text-transform:uppercase">{label}</div>
                </td></tr>
              </table>
            </td>'''


def build_digest_html(display_name: str, tenders, site_url: str = '',
                      period_label: str = 'the last 24 hours',
                      newspaper_tenders=None, news_items=None,
                      range_label: str = '', is_weekend: bool = False) -> str:
    """Aggregate HTML e-mail — AJi Brand Guidelines (Yellow #FCD700 / black /
    white). Modern card layout built from tables so it survives Outlook;
    hardened against e-mail-client dark mode: color-scheme metas +
    [data-ogsc]/[data-ogsb] (Outlook apps) and prefers-color-scheme overrides
    that pin the brand colors."""
    today = timezone.localdate().strftime('%A, %d %B %Y')

    groups: dict[str, list] = {}
    for t in tenders:
        cname = t.country.name_en if t.country else 'Other'
        groups.setdefault(cname, []).append(t)

    newspaper_tenders = list(newspaper_tenders or [])
    news_items        = list(news_items or [])
    n_country = sum(len(v) for v in groups.values())
    total     = n_country + len(newspaper_tenders)

    extras = []
    if newspaper_tenders:
        extras.append('the newspaper tenders')
    if news_items:
        extras.append('the news published in the same period')
    greeting_extra = (', plus ' + ' and '.join(extras)) if extras else ''
    weekend_note = (' Since it is Sunday, this edition also covers the weekend.'
                    if is_weekend else '')

    # ── Contents chips (one per country) ──
    contents = ''
    if groups:
        chips = ''.join(_pill(f'{_esc(c)} &nbsp;<strong>{len(v)}</strong>',
                              bg=SOFT, fg=INK, cls='bg-soft tx-ink')
                        for c, v in groups.items())
        contents = (f'<div style="margin:0 0 18px"><div class="tx-muted" '
                    f'style="font-size:10px;color:{MUTED};letter-spacing:.8px;'
                    f'font-weight:bold;font-family:{FONT};margin-bottom:8px">'
                    f'IN THIS DIGEST</div>{chips}</div>')

    sections = ''
    for cname, items in groups.items():
        rows = ''.join(_tender_row(t, site_url) for t in items)
        sections += _section(_section_title(cname),
                             _plural(len(items), 'tender').upper(), rows)

    # ── Newspaper Tenders — عطاءات الجريدة — their own section ──
    if newspaper_tenders:
        rows = ''.join(_newspaper_row(t, site_url) for t in newspaper_tenders)
        sections += _section(
            _section_title('Newspaper Tenders',
                           '&#1593;&#1591;&#1575;&#1569;&#1575;&#1578; '
                           '&#1575;&#1604;&#1580;&#1585;&#1610;&#1583;&#1577;'),
            _plural(len(newspaper_tenders), 'tender').upper(), rows)

    # ── News — الأخبار — published during the window ──
    if news_items:
        rows = ''.join(_news_row(t, site_url) for t in news_items)
        news_link = ''
        if site_url:
            news_link = (f'<div style="margin-top:4px"><a href="{_esc(site_url.rstrip("/") + "/news/")}" '
                         f'class="tx-black" style="color:{BLACK};font-size:11px;'
                         f'font-family:{FONT};text-decoration:underline">'
                         f'View all news &#8599;</a></div>')
        sections += _section(
            _section_title('News', '&#1575;&#1604;&#1571;&#1582;&#1576;&#1575;&#1585;'),
            _plural(len(news_items), 'item').upper(), rows, extra=news_link)

    if not sections:
        sections = f'''
      <div class="bg-soft tx-muted" style="padding:40px 20px;text-align:center;
                  background-color:{SOFT};border:1px dashed #cfcfca;border-radius:16px;
                  color:{MUTED};font-size:14px;font-family:{FONT}">
        No new tenders were published in your preferred countries during {period_label}.
      </div>'''

    site_btn = ''
    if site_url:
        site_btn = f'''
      <div style="text-align:center;margin:26px 0 6px">
        <a href="{_esc(site_url)}" class="bg-black tx-yellow"
           style="display:inline-block;padding:14px 38px;background-color:{BLACK};
                  color:{YELLOW};font-size:13px;font-weight:bold;text-decoration:none;
                  border-radius:10px;font-family:{FONT};letter-spacing:.6px">OPEN TENDERRADAR &#8594;</a>
      </div>'''

    # AJi wordmark — always "AJi": capital A and J, small i (guidelines p.19)
    aji_black = (f'<span class="tx-black" style="font-family:Arial,sans-serif;font-weight:bold;'
                 f'color:{BLACK};letter-spacing:-.5px">AJi</span>')
    aji_yellow = (f'<span class="tx-yellow" style="font-family:Arial,sans-serif;font-weight:bold;'
                  f'color:{YELLOW};letter-spacing:-.5px">AJi</span>')

    edition = 'Weekend Tender Digest' if is_weekend else 'Daily Tender Digest'
    window_pill = ''
    if range_label:
        window_pill = (f'<div style="margin-top:12px"><span class="bg-black tx-yellow" '
                       f'style="display:inline-block;background-color:{BLACK};color:{YELLOW};'
                       f'font-size:11px;font-weight:bold;padding:5px 12px;border-radius:999px;'
                       f'font-family:{FONT};letter-spacing:.3px">'
                       f'{period_label.upper()} &nbsp;&#183;&nbsp; {range_label}</span></div>')

    # Dark-mode pinning: color-scheme metas keep clients in light mode where
    # honoured; [data-ogsc]/[data-ogsb] rules re-assert brand colors in the
    # Outlook apps; the media query covers Apple Mail & others.
    bg_map = {'bg-yellow': YELLOW, 'bg-black': BLACK, 'bg-white': WHITE,
              'bg-soft': SOFT, 'bg-chip': CHIP, 'bg-ysoft': YELLOW_SOFT}
    tx_map = {'tx-black': BLACK, 'tx-yellow': YELLOW, 'tx-white': WHITE,
              'tx-muted': MUTED, 'tx-ink': INK}
    base_rules = (''.join(f'.{k}{{background-color:{v} !important;}}' for k, v in bg_map.items())
                  + ''.join(f'.{k}{{color:{v} !important;}}' for k, v in tx_map.items()))
    ogs_rules = (''.join(f'[data-ogsb] .{k},[data-ogsb].{k}{{background-color:{v} !important;}}'
                         for k, v in bg_map.items())
                 + ''.join(f'[data-ogsc] .{k},[data-ogsc].{k}{{color:{v} !important;}}'
                           for k, v in tx_map.items()))
    dark_css = (':root{color-scheme:light only;supported-color-schemes:light;}'
                f'{base_rules}'
                f'@media (prefers-color-scheme: dark){{{base_rules}}}'
                f'{ogs_rules}'
                '@media only screen and (max-width:620px){'
                '.wrap{padding:12px 6px !important;}'
                '.px{padding-left:18px !important;padding-right:18px !important;}'
                '.hide-sm{display:none !important;}}')

    return f'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="color-scheme" content="light only">
  <meta name="supported-color-schemes" content="light">
  <style>{dark_css}</style>
</head>
<body style="margin:0;padding:0;background-color:{BG};font-family:{FONT};
             -webkit-text-size-adjust:100%">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
         style="background-color:{BG}">
    <tr><td align="center" class="wrap" style="padding:32px 12px">
      <table role="presentation" width="660" cellpadding="0" cellspacing="0"
             class="bg-white" style="max-width:660px;width:100%;border-collapse:separate;
             background-color:{WHITE};border-radius:20px;overflow:hidden;
             border:1px solid {LINE}">

        <!-- Hero — AJi Yellow block, black wordmark -->
        <tr><td class="bg-yellow px" style="padding:30px 32px 26px;background-color:{YELLOW};
                border-radius:20px 20px 0 0">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
            <td valign="top">
              <div style="font-size:34px;line-height:1">{aji_black}</div>
              <div class="tx-black" style="font-size:18px;color:{BLACK};margin-top:14px;
                          font-family:{FONT};font-weight:bold;letter-spacing:-.2px">
                TenderRadar &#183; {edition}</div>
              <div class="tx-black" style="font-size:12px;color:{BLACK};margin-top:4px;
                          font-family:{FONT}">{today}</div>
              {window_pill}
            </td>
            <td align="right" valign="top" class="hide-sm" style="padding-left:12px">
              <div class="tx-black" style="font-size:46px;font-weight:bold;color:{BLACK};
                          line-height:1;font-family:{FONT}">{total}</div>
              <div class="tx-black" style="font-size:10px;color:{BLACK};letter-spacing:.8px;
                          font-family:{FONT};font-weight:bold;margin-top:4px">
                NEW TENDER{'S' if total != 1 else ''}</div>
            </td>
          </tr></table>
        </td></tr>

        <!-- Greeting + stats -->
        <tr><td class="bg-white px" style="padding:26px 32px 6px;background-color:{WHITE}">
          <div class="tx-ink" style="font-size:14px;color:{INK};line-height:1.7;
                      font-family:{FONT}">
            Good morning{(' <strong>' + _esc(display_name) + '</strong>') if display_name else ''},<br>
            here is your summary of the tenders added during <strong>{period_label}</strong>
            for your preferred countries{greeting_extra}.{weekend_note}
          </div>
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
                 style="margin:20px 0 4px"><tr>
            {_stat_cell(n_country, 'Country tenders', highlight=True)}
            {_stat_cell(len(newspaper_tenders), 'Newspaper')}
            {_stat_cell(len(news_items), 'News')}
          </tr></table>
        </td></tr>

        <!-- Body -->
        <tr><td class="bg-white px" style="padding:20px 32px 12px;background-color:{WHITE}">
          {contents}
          {sections}
          {site_btn}
        </td></tr>

        <!-- Footer — black block, yellow wordmark + tagline -->
        <tr><td class="bg-black px" style="padding:26px 32px;background-color:{BLACK};
                border-radius:0 0 20px 20px">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
            <td valign="middle" width="70">
              <span style="font-size:26px;line-height:1">{aji_yellow}</span>
            </td>
            <td valign="middle" style="padding-left:16px;border-left:2px solid {YELLOW}">
              <div class="tx-white" style="font-size:13px;color:{WHITE};font-family:{FONT};
                          line-height:1.5">{TAGLINE}</div>
            </td>
          </tr></table>
          <div style="border-top:1px solid #2a2a2a;margin-top:18px;padding-top:12px;
                      font-size:10px;color:#8a8a8a;line-height:1.7;text-align:center;
                      font-family:{FONT}">
            You receive this e-mail because the daily digest is enabled for your TenderRadar
            account and you selected preferred countries in your profile.
            Automated message &#8212; please do not reply.
          </div>
        </td></tr>

      </table>
    </td></tr>
  </table>
</body>
</html>'''


# ─────────────────────────────────────────────────────────────────────────────
# Sending
# ─────────────────────────────────────────────────────────────────────────────

def _display_name(user) -> str:
    return (user.get_full_name() or user.username or '').strip()


def _subject(n_tenders: int, n_news: int, window: dict, test: bool = False) -> str:
    edition = 'Weekend Digest' if window['is_weekend'] else 'Daily Digest'
    news_part = f' · {n_news} news' if n_news else ''
    return (f'{"[TEST] " if test else ""}TenderRadar {edition} — '
            f'{_plural(n_tenders, "new tender")}{news_part} '
            f'· last {window["hours"]}h · {timezone.localdate():%d %b %Y}')


def _render(name: str, tenders, newspaper, news, site_url: str, window: dict) -> str:
    return build_digest_html(name, tenders, site_url,
                             period_label=window['label'],
                             newspaper_tenders=newspaper, news_items=news,
                             range_label=window['range_label'],
                             is_weekend=window['is_weekend'])


def send_digest_to_user(user, mailer=None, window=None) -> tuple[str, str]:
    """
    Send the digest to one user.
    Returns (status, detail) — status ∈ sent | no_email | no_countries | no_tenders | failed.
    """
    from .integrations import MSGraphMailer
    mailer = mailer or MSGraphMailer()
    window = window or get_digest_window()
    since  = window['since']

    if not user.email:
        return 'no_email', f'{user.username}: no e-mail address on the account.'

    codes = get_user_country_codes(user)
    if not codes:
        return 'no_countries', f'{user.username}: no preferred countries in profile.'

    tenders   = list(get_digest_tenders(codes, since, user=user))
    newspaper = list(get_digest_newspaper_tenders(since))
    news      = list(get_digest_news(since))
    if not tenders and not newspaper and not news:
        return 'no_tenders', (f'{user.username}: no new tenders, newspaper '
                              f'tenders or news in the last {window["hours"]}h '
                              f'for {", ".join(codes)}.')

    from .models import IntegrationSettings
    cfg  = IntegrationSettings.get()
    html = _render(_display_name(user), tenders, newspaper, news,
                   cfg.digest_site_url, window)
    n_tenders = len(tenders) + len(newspaper)
    subj = _subject(n_tenders, len(news), window)

    if mailer.send_mail(subj, html, to=[user.email], category='DIGEST'):
        return 'sent', (f'{user.username}: {n_tenders} tenders'
                        f'{f", {len(news)} news" if news else ""} '
                        f'(last {window["hours"]}h) → {user.email}')
    return 'failed', f'{user.username}: {mailer.last_error}'


def run_daily_digest(user_ids: list[int] | None = None) -> dict:
    """
    Send the digest to all active users with an e-mail (or to `user_ids` only).
    Window: last 24 hours; on Sunday the last 72 hours.
    Returns a summary dict for logging / the manage UI.
    """
    from django.contrib.auth.models import User
    from .integrations import MSGraphMailer
    from .models import IntegrationSettings

    window = get_digest_window()
    mailer = MSGraphMailer()

    users = User.objects.filter(is_active=True).exclude(email='')
    if user_ids is not None:
        users = users.filter(id__in=user_ids)

    summary = {'sent': 0, 'no_email': 0, 'no_countries': 0,
               'no_tenders': 0, 'failed': 0, 'details': [],
               'window_hours': window['hours']}

    logger.info(f'[Digest] window: last {window["hours"]}h '
                f'(since {timezone.localtime(window["since"]):%Y-%m-%d %H:%M}).')

    for user in users.select_related('profile'):
        try:
            status, detail = send_digest_to_user(user, mailer=mailer, window=window)
        except Exception as exc:                      # never break the whole run
            status, detail = 'failed', f'{user.username}: {exc}'
            logger.exception(f'[Digest] unexpected error for {user.username}')
        summary[status] += 1
        summary['details'].append(detail)
        logger.info(f'[Digest] {status} — {detail}')

    cfg = IntegrationSettings.get()
    cfg.digest_last_run_at = timezone.now()
    cfg.save(update_fields=['digest_last_run_at'])

    logger.info(f'[Digest] run complete: {summary["sent"]} sent, '
                f'{summary["failed"]} failed, '
                f'{summary["no_countries"]} without countries, '
                f'{summary["no_tenders"]} without new tenders.')
    return summary


def send_test_digest(email: str) -> tuple[bool, str]:
    """
    Send a sample digest to `email` so the formatting/flow can be verified.
    If a user account matches the address, their real preferences are used;
    otherwise the digest aggregates the window across all active countries.
    Uses the same window as the real run (24 h; 72 h on Sunday).
    """
    from django.contrib.auth.models import User
    from .integrations import MSGraphMailer
    from .models import IntegrationSettings, CountryConfig

    window = get_digest_window()
    since  = window['since']
    cfg    = IntegrationSettings.get()
    mailer = MSGraphMailer()

    user = User.objects.filter(email__iexact=email, is_active=True).first()
    if user and get_user_country_codes(user):
        codes, name = get_user_country_codes(user), _display_name(user)
        tenders = list(get_digest_tenders(codes, since, user=user))
        scope   = f'their profile countries ({", ".join(codes)})'
    else:
        codes   = list(CountryConfig.objects.filter(is_active=True)
                       .values_list('code', flat=True))
        name    = ''
        tenders = list(get_digest_tenders(codes, since))
        scope   = 'all active countries (no matching user profile)'

    newspaper = list(get_digest_newspaper_tenders(since))
    news      = list(get_digest_news(since))

    html = _render(name, tenders, newspaper, news, cfg.digest_site_url, window)
    n_tenders = len(tenders) + len(newspaper)
    subj = _subject(n_tenders, len(news), window, test=True)

    if mailer.send_mail(subj, html, to=[email], category='TEST'):
        return True, (f'Test digest sent to {email} — {n_tenders} tenders, '
                      f'{len(news)} news, last {window["hours"]}h, scope: {scope}.')
    return False, f'Sending failed: {mailer.last_error}'
