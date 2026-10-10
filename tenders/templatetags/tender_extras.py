# tenders/templatetags/tender_extras.py

import re

from django import template

register = template.Library()


@register.filter
def get_item(dictionary, key):
    """Get a value from a dict by key in templates."""
    if isinstance(dictionary, dict):
        return dictionary.get(key, '')
    return ''


@register.filter
def currency_symbol(code):
    symbols = {
        'SAR': 'SAR', 'AED': 'AED', 'OMR': 'OMR',
        'QAR': 'QAR', 'KWD': 'KWD', 'JOD': 'JOD',
        'USD': '$', 'EUR': '€',
    }
    return symbols.get(code, code)


@register.filter
def days_color(days):
    """Return CSS color class based on days remaining."""
    if days is None:
        return 'var(--grey-5)'
    if days < 0:
        return 'var(--grey-5)'
    if days <= 3:
        return 'var(--red)'
    if days <= 7:
        return 'var(--orange)'
    if days <= 14:
        return 'var(--gold)'
    return 'var(--green)'


@register.filter
def status_badge(status):
    mapping = {
        'OPEN':   'badge-open',
        'CLOSED': 'badge-closed',
        'AWARD':  'badge-award',
        'CANCEL': 'badge-cancel',
        'UNK':    'badge-closed',
    }
    return mapping.get(status, 'badge-closed')


@register.simple_tag
def query_transform(request, **kwargs):
    """Return URL with updated query params — used for filter links."""
    updated = request.GET.copy()
    for k, v in kwargs.items():
        if v:
            updated[k] = v
        elif k in updated:
            del updated[k]
    return updated.urlencode()


@register.filter
def format_value(value, currency=''):
    """Format large monetary values."""
    if value is None:
        return '—'
    try:
        v = float(value)
        if v >= 1_000_000:
            return f'{currency} {v/1_000_000:.1f}M'.strip()
        if v >= 1_000:
            return f'{currency} {v/1_000:.1f}K'.strip()
        return f'{currency} {v:,.0f}'.strip()
    except (TypeError, ValueError):
        return '—'


import json as _json
from django.utils.safestring import mark_safe
from django.utils.html import escape

@register.filter
def to_json(value):
    """Serialize a Python dict/list to safe JSON string for use in <script>."""
    return mark_safe(_json.dumps(value))


# ── Flag / country chip ───────────────────────────────────────────────────────
# Windows has no flag-emoji font, so regional-indicator emoji (🇴🇲) render as the
# bare letters "OM". Instead we render a clean, on-brand code chip that displays
# identically on every OS. Multi-country orgs (WB, UN, EU, ZZ…) get the same chip.

# ISO alpha-2 codes that are real countries get a subtle flag-style chip; the few
# non-country entities in the DB are labelled but keep the same neutral chip.
_ORG_CODES = {'WB', 'UN', 'EU', 'AB', 'AD', 'IB', 'BA', 'ZZ'}


@register.filter
def flag_code(country):
    """Return the 2-letter code (uppercase) for a country object or code string."""
    if country is None:
        return ''
    code = getattr(country, 'code', country)
    return (code or '').upper()


@register.simple_tag
def country_flag(country, size=''):
    """
    Render a clean country chip that displays consistently on every OS.
    Usage: {% country_flag t.country %}   or   {% country_flag t.country 'sm' %}
    """
    if country is None:
        return mark_safe('<span class="flag flag-org">—</span>')
    code = flag_code(country)
    cls = 'flag'
    if size:
        cls += f' flag-{size}'
    if code in _ORG_CODES:
        cls += ' flag-org'
    return mark_safe(f'<span class="{cls}" title="{escape(getattr(country, "name_en", "") or code)}">{escape(code) or "—"}</span>')


@register.simple_tag
def country_label(country, size=''):
    """Flag chip + English name, e.g. `[JO] Jordan`. Falls back gracefully."""
    if country is None:
        return mark_safe('<span class="flag flag-org">—</span>')
    chip = country_flag(country, size)
    name = escape(getattr(country, 'name_en', '') or flag_code(country))
    return mark_safe(f'{chip}<span class="flag-name">{name}</span>')


# -- Icon system --------------------------------------------------------------
# Font Awesome 6 Free, SELF-HOSTED from static/vendor/fontawesome. The internal
# network has no route to a CDN, so the stylesheet and webfonts ship with the
# project (see the <link> in templates/base.html).
#
# Templates are unchanged:
#     {% icon 'search' %}   {% icon 'star' 18 %}   {% icon 'check' 16 'ic-ok' %}
#
# Names are SEMANTIC, not Font Awesome class names. That indirection is the
# point: the whole icon set can be retuned here without editing a template, and
# a name reads as what it means ('deadline') rather than how it looks.
_FA_FALLBACK = 'fa-solid fa-circle-dot'

_FA_ICONS = {
    # -- navigation / shell --
    'dashboard':     'fa-solid fa-gauge-high',
    'list':          'fa-solid fa-list',
    'grip':          'fa-solid fa-grip-vertical',
    'search':        'fa-solid fa-magnifying-glass',
    'filter':        'fa-solid fa-filter',
    'settings':      'fa-solid fa-gear',
    'sliders':       'fa-solid fa-sliders',
    'help':          'fa-solid fa-circle-question',
    'logout':        'fa-solid fa-arrow-right-from-bracket',
    'login':         'fa-solid fa-right-to-bracket',
    'inbox':         'fa-solid fa-inbox',
    'external':      'fa-solid fa-arrow-up-right-from-square',
    'link':          'fa-solid fa-link',
    'home':          'fa-solid fa-house',

    # -- arrows / chevrons --
    'arrow-left':    'fa-solid fa-arrow-left',
    'arrow-right':   'fa-solid fa-arrow-right',
    'chevron-down':  'fa-solid fa-chevron-down',
    'chevron-up':    'fa-solid fa-chevron-up',
    'chevron-right': 'fa-solid fa-chevron-right',
    'chevron-left':  'fa-solid fa-chevron-left',

    # -- people / access --
    'user':          'fa-solid fa-user',
    'users':         'fa-solid fa-users',
    'user-shield':   'fa-solid fa-user-shield',
    'shield':        'fa-solid fa-shield-halved',
    'crown':         'fa-solid fa-crown',
    'key':           'fa-solid fa-key',
    'lock':          'fa-solid fa-lock',

    # -- organisation --
    'building':      'fa-solid fa-building',
    'department':    'fa-solid fa-sitemap',
    'org':           'fa-solid fa-sitemap',

    # -- tenders (the core domain) --
    'tender':        'fa-solid fa-file-contract',
    'newspaper':     'fa-solid fa-newspaper',
    # Outline, not solid: the News section reads as lighter than the
    # newspaper one beside it. fa-regular is used rather than a hollow
    # book because book-open exists ONLY in the solid font — checked
    # against fa-regular-400.ttf, which carries 436 glyphs and not that
    # one, so fa-regular fa-book-open would render an empty box.
    # Outline, and deliberately NOT an outline newspaper: that twinned the
    # solid newspaper sitting next to it in the nav, so the two sections
    # read as the same thing at two weights. Stacked pages say "several
    # publications", which is what the News section is.
    # fa-clone is confirmed present in fa-regular-400.ttf; many icons
    # (fa-book-open among them) exist only in the solid font and would
    # render an empty box here.
    'magazine':      'fa-regular fa-clone',
    'megaphone':     'fa-solid fa-bullhorn',
    'deadline':      'fa-solid fa-hourglass-half',
    'money':         'fa-solid fa-money-bill-wave',
    'coins':         'fa-solid fa-coins',
    'gavel':         'fa-solid fa-gavel',
    'stamp':         'fa-solid fa-stamp',

    # -- AEC / engineering categories --
    'engineering':   'fa-solid fa-helmet-safety',
    'construction':  'fa-solid fa-trowel-bricks',
    'infra':         'fa-solid fa-road',
    'water':         'fa-solid fa-droplet',
    'health':        'fa-solid fa-hospital',
    'power':         'fa-solid fa-bolt',
    'ruler':         'fa-solid fa-ruler-combined',
    'compass':       'fa-solid fa-compass-drafting',
    'project':       'fa-solid fa-diagram-project',

    # -- documents / files --
    'file':          'fa-solid fa-file-lines',
    'pdf':           'fa-solid fa-file-pdf',
    'excel':         'fa-solid fa-file-excel',
    'word':          'fa-solid fa-file-word',
    'zip':           'fa-solid fa-file-zipper',
    'folder':        'fa-solid fa-folder-open',
    'paperclip':     'fa-solid fa-paperclip',
    'book':          'fa-solid fa-book',
    'clipboard':     'fa-solid fa-clipboard-list',
    'cv':            'fa-solid fa-id-card',
    'image':         'fa-solid fa-image',

    # -- data / system --
    'database':      'fa-solid fa-database',
    'server':        'fa-solid fa-server',
    'cpu':           'fa-solid fa-microchip',
    'backup':        'fa-solid fa-box-archive',
    'restore':       'fa-solid fa-rotate-left',
    'scraper':       'fa-solid fa-robot',
    'ai':            'fa-solid fa-brain',
    'signal':        'fa-solid fa-signal',
    'gears':         'fa-solid fa-gears',
    'terminal':      'fa-solid fa-terminal',

    # -- analytics --
    'chart':         'fa-solid fa-chart-line',
    'chart-bar':     'fa-solid fa-chart-column',
    'chart-pie':     'fa-solid fa-chart-pie',
    'analytics':     'fa-solid fa-chart-pie',
    'target':        'fa-solid fa-bullseye',
    'trend-up':      'fa-solid fa-arrow-trend-up',
    'trend-down':    'fa-solid fa-arrow-trend-down',

    # -- time --
    'calendar':      'fa-solid fa-calendar-days',
    'clock':         'fa-solid fa-clock',
    'history':       'fa-solid fa-clock-rotate-left',
    'refresh':       'fa-solid fa-rotate',

    # -- communication --
    'bell':          'fa-solid fa-bell',
    'bell-off':      'fa-solid fa-bell-slash',
    'minus':         'fa-solid fa-minus',
    'message':       'fa-solid fa-comment-dots',
    'mail':          'fa-solid fa-envelope',
    'send':          'fa-solid fa-paper-plane',

    # -- geography --
    'globe':         'fa-solid fa-globe',
    'map':           'fa-solid fa-map-location-dot',
    'location':      'fa-solid fa-location-dot',
    'flag':          'fa-solid fa-flag',

    # -- actions --
    'plus':          'fa-solid fa-plus',
    'edit':          'fa-solid fa-pen-to-square',
    'trash':         'fa-solid fa-trash-can',
    'save':          'fa-solid fa-floppy-disk',
    'download':      'fa-solid fa-download',
    'upload':        'fa-solid fa-upload',
    'play':          'fa-solid fa-play',
    'pause':         'fa-solid fa-pause',
    'stop':          'fa-solid fa-stop',
    'eye':           'fa-solid fa-eye',
    'eye-off':       'fa-solid fa-eye-slash',
    'copy':          'fa-solid fa-copy',
    'print':         'fa-solid fa-print',

    # -- state / feedback --
    'check':         'fa-solid fa-check',
    'check-circle':  'fa-solid fa-circle-check',
    'x':             'fa-solid fa-xmark',
    'x-circle':      'fa-solid fa-circle-xmark',
    'alert':         'fa-solid fa-triangle-exclamation',
    'info':          'fa-solid fa-circle-info',
    'circle-dot':    'fa-solid fa-circle-dot',
    'spinner':       'fa-solid fa-spinner',
    'star':          'fa-regular fa-star',
    'star-filled':   'fa-solid fa-star',
    'bookmark':      'fa-solid fa-bookmark',
    'thumbs-up':     'fa-solid fa-thumbs-up',
    'thumbs-down':   'fa-solid fa-thumbs-down',
}

# Domain words that should render as an existing icon, so a template author does
# not have to remember which generic name to reach for.
_FA_ALIASES = {
    'company':       'building',
    'companies':     'building',
    'departments':   'department',
    'employees':     'users',
    'roles':         'shield',
    'notification':  'bell',
    'notifications': 'bell',
    'email':         'mail',
    'digest':        'mail',
    'log':           'clipboard',
    'logs':          'clipboard',
    'integration':   'message',
    'integrations':  'message',
    'source':        'database',
    'sources':       'database',
    'country':       'globe',
    'countries':     'globe',
    'feedback':      'message',
    'note':          'message',
    'notes':         'message',
    'attachment':    'paperclip',
    'category':      'list',
    'keyword':       'search',
    'archive':       'backup',
    'run':           'history',
    'runs':          'history',
    'edition':       'newspaper',
    'photo':         'image',
    'value':         'money',
    'entity':        'building',
    'organisation':  'building',
    'organization':  'building',
}


@register.simple_tag
def icon(name, size=16, cls=''):
    """
    Font Awesome 6 icon looked up by SEMANTIC name (see _FA_ICONS).

    Renders `<i class="icon fa-solid fa-...">`. The `icon` class is retained so
    the alignment rules already in base.html keep working, and `size` maps to
    font-size because Font Awesome scales with type rather than width/height.

    An unmapped name renders a neutral dot plus an HTML comment naming it, so a
    typo shows up in review instead of silently collapsing the layout.
    """
    key = _FA_ALIASES.get(name, name)
    fa = _FA_ICONS.get(key)
    if fa is None:
        return mark_safe(
            f'<i class="icon {_FA_FALLBACK}" style="font-size:{size}px" '
            f'aria-hidden="true"></i><!-- icon:{escape(str(name))} unmapped -->'
        )
    classes = f'icon {fa}' + (f' {cls}' if cls else '')
    return mark_safe(
        f'<i class="{classes}" style="font-size:{size}px" aria-hidden="true"></i>'
    )


@register.filter
def highlight_mentions(text):
    """Render @mentions as a chip carrying the mentioned user's avatar + name.
    Everything else is escaped. Unknown handles fall back to a plain name chip."""
    import re
    from django.utils.html import escape
    from django.utils.safestring import mark_safe
    from django.templatetags.static import static
    if not text:
        return ''

    handles = {h.lower() for h in re.findall(r'@([A-Za-z0-9_.\-]+)', text)}
    people = {}
    if handles:
        try:
            from django.contrib.auth import get_user_model
            U = get_user_model()
            for u in U.objects.filter(username__in=handles).select_related('profile'):
                av = ''
                try:
                    av = u.profile.avatar or ''
                except Exception:
                    av = ''
                people[u.username.lower()] = (u.get_full_name() or u.username, av)
        except Exception:
            people = {}

    av_base = static('img/avatars/')

    def chip(handle):
        display, av = people.get(handle.lower(), (handle, ''))
        if av:
            icon = ('<span class="ava ava-sm" aria-hidden="true">'
                    f'<img src="{escape(av_base)}{escape(av)}" alt=""></span>')
        else:
            icon = ('<span class="ava ava-sm" aria-hidden="true">'
                    f'{escape(display[:1].upper())}</span>')
        return (f'<span class="mention-chip">{icon}'
                f'<span class="mc-name">@{escape(display)}</span></span>')

    out, last = [], 0
    for m in re.finditer(r'@([A-Za-z0-9_.\-]+)', text):
        out.append(escape(text[last:m.start()]))
        out.append(chip(m.group(1)))
        last = m.end()
    out.append(escape(text[last:]))
    return mark_safe(''.join(out))


#: A figure a reader scans a construction story for: a value with its currency,
#: a length, an area, a capacity, a count of homes or rooms, a share, a year
#: or a quarter. Currency may come before ($1.6bn, AED 2.3 billion) or after
#: (2.3 billion dirhams); units may be abbreviated or written out.
_FIGURE_RE = re.compile(r"""
    (?<![\w.&#])                                               # not inside a word, an entity or a decimal
    (?:(?:US\$|\$|€|£|AED|SAR|QAR|KWD|JOD|OMR|BHD|EGP|USD|EUR|GBP|Dhs?|SR|QR|RO|KD|JD)\s?)?
    (?:Q[1-4]\s?)?                                             # Q1 2027
    [0-9٠-٩]+(?:,[0-9٠-٩]{3})*(?:\.[0-9٠-٩]+)?                # 1,200  2.3  ٢٥٠ - a comma only as a thousands separator
    (?:\s?(?:bn|billion|mn|million|m|k|thousand|trillion)(?![A-Za-z]))?     # the multiplier: $1.6bn, 2.3m
    (?:\s?(?:%|km²|m²|sq\s?km|sq\s?m|sqm|sq\s?ft|km|MW|GW|MWp|kV|ha|hectares?|
         units?|homes?|villas?|apartments?|rooms?|keys|beds?|floors?|storeys?|stories|
         towers?|metres?|meters?|tonnes?|tons?|barrels?|
         dirhams?|riyals?|dinars?|dollars?|euros?|years?|months?|days?)(?![A-Za-z]))?  # the unit: 400km, 2.3m sq ft
    (?![A-Za-z0-9٠-٩])                                          # whole figure only: not the 2 of "21st", nor a code like A350
""", re.X | re.I)


@register.filter(is_safe=True)
def focus_numbers(text):
    """
    The summary on a News card, with its figures set in bold on the yellow
    mark: the value, the length, the capacity, the date. Those are what a
    reader scans a construction story for, so they are the one thing the
    card makes loud. Everything else is escaped and left as it is.
    """
    from django.utils.html import escape
    from django.utils.safestring import mark_safe
    if not text:
        return ''
    safe = escape(str(text))
    return mark_safe(_FIGURE_RE.sub(
        lambda m: f'<strong class="nf-num">{m.group(0)}</strong>', safe))
