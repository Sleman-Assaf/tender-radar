# magazine_tenders/scrapers/_newsfeed.py
"""
Shared base for magazines that publish a NEWS FEED rather than a tender board.

Why this exists
---------------
Four of the registered magazines (Construction Week, CBNME, Arabian Business,
Healthcare MEA) are trade-news publications. They have no tender listing page:
what they publish is articles, a small minority of which ARE announcements of a
tender that is open for bids ("RTA invites bids for the Dubai Metro Gold Line").

So the job here is the opposite of Gulf Construction's, where every bullet in the
column is a tender. Here, most items must be REJECTED. The filter below is
deliberately strict: a loose one would fill the Magazines page with industry
news wearing a tender's clothes, which is worse than an empty page.

Measured yield on 2026-09-02, 100 most recent items per feed:

    Construction Week (Projects and Tenders) ... 1
    CBNME (tender search) ..................... 1   (the same RTA story)
    Arabian Business (tender search) .......... 0
    Healthcare MEA (Hospitals) ................ 0

That is the honest shape of these sources. They are a thin supplement to the
real tender boards, not a substitute for them.

Two filters, both required
--------------------------
1. ANNOUNCEMENT SIGNAL — the language a site uses when bids are actually being
   invited ("invites bids", "request for proposals", "prequalification",
   "tender no", "closing date"). Without one of these the item is not a tender.
2. RETROSPECTIVE VETO — an item that reads as a completed story ("X wins
   contract", "awarded", "inaugurated") is dropped even if it also carries a
   signal word, because it reports someone else's finished procurement.

Whether a surviving item is AEC is NOT decided here. That is the shared AJiAI
service's job, exactly as for every other tender in the system.
"""
import html as _html
import json
import logging
import re
import datetime as _dt
from datetime import date, datetime, timedelta
from typing import Optional

from django.conf import settings

from tenders.scrapers.base import BaseScraper, TenderData

from .. import freshness

logger = logging.getLogger('tenders')


# ── The two filters ───────────────────────────────────────────────────────────
ANNOUNCEMENT_RE = re.compile(
    r'(?:invit\w*\s+(?:bids|tenders|offers|proposals)|'
    # An invitation is often addressed to WHO may bid rather than to "bids":
    # "DEWA Invites Global Developers For MBR Solar Park Phase 7".
    # PROCUREMENT roles only — "investors", "companies" and "firms" appear in
    # ordinary business prose ("Lootah invited investors ... to the Forum") and
    # including them let plain news through.
    r'invit\w*\s+(?:\w+\s+){0,2}'
    r'(?:developers|contractors|consultants|bidders|suppliers|tenderers)|'
    r'call\s+for\s+(?:bids|tenders|proposals|expressions)|'
    r'request\s+for\s+(?:proposals?|qualifications?|tenders?|information)|'
    r'\bRFP\b|\bRFQ\b|\bEOI\b|expressions?\s+of\s+interest|'
    r'pre-?qualif\w*|'
    r'tender\s+(?:no|number|ref\w*|document|notice|floated|issued|launched|open)|'
    r'bids?\s+(?:are\s+)?(?:invited|due|sought|close|closing)|'
    r'float\w*\s+(?:a\s+)?tender|issu\w*\s+(?:a\s+)?tender|'
    r'launch\w*\s+(?:a\s+)?tender|'
    r'seek\w*\s+(?:bids|contractors|consultants|proposals)|'
    r'submission\s+deadline|closing\s+date|bid\s+deadline)',
    re.I,
)

# Arabic equivalents, for the Arabic feeds (Zawya publishes both).
ANNOUNCEMENT_AR_RE = re.compile(
    r'(مناقص|عطاء|عطاءات|طرح\s*مناقص|طلب\s*عروض|دعوة\s*لتقديم|'
    r'التأهيل\s*المسبق|كراسة\s*الشروط|الموعد\s*النهائي)'
)

RETROSPECTIVE_RE = re.compile(
    r'\b(?:awarded?|awards|wins?|won|secure[sd]?|clinch\w*|bag[s]?|'
    r'complet\w+|inaugurat\w+|open\w*\s+(?:its|the)\s+|hand\w*\s+over|'
    # 'Consultants appointed for X' and 'Consultant appointed' report a
    # decision already taken — useful intelligence, but not biddable, so it
    # belongs on the audit page rather than the tenders page.
    r'appoint\w*|selected\s+(?:as|for)|named\s+(?:as|design)|'
    r'break\w*\s+ground|deliver\w*)\b',
    re.I,
)

REFERENCE_RE = re.compile(
    r'(?:tender|contract|rfp|rfq|bid|reference|ref)\.?\s*(?:no\.?|number|#)?\s*'
    r'[:\-]?\s*([A-Z0-9][A-Z0-9/\-_.]{3,40})',
    re.I,
)

_MONTH = (r'(?:January|February|March|April|May|June|July|August|September|'
          r'October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)')

DEADLINE_RE = re.compile(
    r'(?:closing\s+date|submission\s+deadline|bids?\s+(?:are\s+)?due|'
    r'deadline|last\s+date|closes?)\D{0,30}?'
    r'(\d{1,2})\s+(' + _MONTH + r')\s*(\d{4})?'
    r'|(?:closing\s+date|submission\s+deadline|deadline)\D{0,30}?'
    r'(' + _MONTH + r')\s+(\d{1,2}),?\s*(\d{4})?',
    re.I,
)

_MONTH_NUM = {
    'jan': 1, 'january': 1, 'feb': 2, 'february': 2, 'mar': 3, 'march': 3,
    'apr': 4, 'april': 4, 'may': 5, 'jun': 6, 'june': 6, 'jul': 7, 'july': 7,
    'aug': 8, 'august': 8, 'sep': 9, 'sept': 9, 'september': 9,
    'oct': 10, 'october': 10, 'nov': 11, 'november': 11, 'dec': 12, 'december': 12,
}


def strip_html(raw: str) -> str:
    """Tag-free, whitespace-collapsed text."""
    txt = re.sub(r'(?is)<(script|style).*?</\1>', ' ', raw or '')
    txt = re.sub(r'<[^>]+>', ' ', txt)
    return re.sub(r'\s+', ' ', _html.unescape(txt)).strip()


def reads_as_tender_announcement(text: str) -> bool:
    """
    True only when `text` announces a tender that is open for bids.

    Pure and unit-tested: this single predicate is what keeps trade news out of
    the Magazines page, so it is worth being able to test on its own.
    """
    blob = text or ''
    has_signal = bool(ANNOUNCEMENT_RE.search(blob) or ANNOUNCEMENT_AR_RE.search(blob))
    if not has_signal:
        return False
    # A retrospective headline loses even with a signal word: "Contractor wins
    # tender for X" is a result, not an invitation.
    return not RETROSPECTIVE_RE.search(blob)


# ── "all news that is إشراف or تصميم" ────────────────────────────────────────
# The business wants design and supervision news from these sites, not only
# notices open for bids. Deliberately looser than the equivalent patterns in
# tenders/aec_scope.py: those decide whether a TENDER TITLE describes separable
# design work, and so require a scope context ("design of", "design services")
# to keep out "relaunched with a new design". Here a bare mention is the point
# — a story about a tower's new design IS design news — so the context
# requirement is dropped on purpose.
#
# Arabic is matched with and without the hamza (إشراف / اشراف), because the
# feeds are inconsistent and a missed hamza silently drops the item.
DESIGN_SUPERVISION_RE = re.compile(
    r'\b(?:'
    r'design|designs|designed|designing|redesign|re-design|'
    r'masterplan\w*|master\s?plan\w*|'
    r'supervis\w+|'
    r'engineering\s+consultanc\w+|design\s+consultanc\w+|'
    r'resident\s+engineer|site\s+supervision'
    r')\b', re.I)

DESIGN_SUPERVISION_AR_RE = re.compile(
    r'\u0625\u0634\u0631\u0627\u0641|\u0627\u0634\u0631\u0627\u0641|'          # إشراف / اشراف
    r'\u062a\u0635\u0645\u064a\u0645|\u062a\u0635\u0627\u0645\u064a\u0645|'   # تصميم / تصاميم
    r'\u0645\u062e\u0637\u0637\u0627\u062a|'                                        # مخططات
    r'\u0627\u0644\u0625\u0634\u0631\u0627\u0641|\u0627\u0644\u062a\u0635\u0645\u064a\u0645')

# French, for the francophone items Zawya carries.
DESIGN_SUPERVISION_FR_RE = re.compile(
    r'conception|ma[i\u00ee]trise\s+d.\s*(?:oe|\u0153)uvre|'
    r'surveillance\s+des\s+travaux|contr[o\u00f4]le\s+des\s+travaux', re.I)


def reads_as_design_or_supervision(text: str) -> bool:
    """True when `text` is about design or supervision work."""
    blob = text or ''
    return bool(DESIGN_SUPERVISION_RE.search(blob)
                or DESIGN_SUPERVISION_AR_RE.search(blob)
                or DESIGN_SUPERVISION_FR_RE.search(blob))


# ── design/supervision as COMMISSIONED WORK ─────────────────────────────────
# The bare-mention rule above was too loose in a way a noun list cannot fix:
# hospitals and airports appear on BOTH sides. Measured against 355 stored
# rows, a bare mention kept 254 and let through "children ordering food
# without supervision", "AI to design synthetic viruses", "free access to
# Adobe AI tools" and a hotel review.
#
# What separates the wanted from the unwanted is not the subject but whether
# design or supervision is being COMMISSIONED OR DELIVERED — a contract, a
# role, an appointment, a scope of work. That test keeps 169 of the 355 and
# costs only three genuine items.
#: "still inside the same sentence", for the rules that allow a gap between
#: two words they need near each other.
#:
#: The rules write that gap as `[^.]`, which was wrong in a way nothing
#: failed on: it also refused the dot in a DECIMAL. "Aldar launches The
#: Canopies at $1.6bn Yas Point development" was dropped, while the identical
#: headline reading "$16bn" was kept - the gap could not cross the point in
#: 1.6. Money figures with a decimal are everywhere in this trade press, so
#: the rule was quietly losing real stories on the size of the contract.
#:
#: A dot is allowed unless whitespace follows it, which is what actually ends
#: a sentence. Decimals and abbreviations pass; "... the contract. The next
#: story ..." still does not.
_GAP = r'(?:[^.]|\.(?!\s))'


def _same_sentence(pattern: str) -> str:
    """Expand the `[^.]` shorthand in a rule into the real gap."""
    return pattern.replace('[^.]', _GAP)


#: design/supervision as COMMISSIONED WORK, one rule per entry.
#:
#: The bare-mention rule above was too loose in a way a noun list cannot fix:
#: hospitals and airports appear on BOTH sides. Measured over 355 stored rows,
#: a bare mention kept 254 and let through "children ordering food without
#: supervision", "AI to design synthetic viruses", "free access to Adobe AI
#: tools" and a hotel review.
#:
#: What separates wanted from unwanted is not the subject but whether design
#: or supervision is being COMMISSIONED OR DELIVERED — a contract, a role, an
#: appointment, a scope of work.
#:
#: Kept as a list rather than one re.X blob because the blob silently matched
#: almost nothing, and a single rule per line can be tested on its own.
_DESIGN_WORK_PATTERNS = (
    # an explicit scope: "design of the airport", "supervision for the villa"
    r'\b(?:designs?|designing|supervision|masterplans?)\s+(?:of|for)\b',
    # a deliverable or instrument
    r'\b(?:design|supervision|consultanc\w+|consultant|engineering)\s+'
    r'(?:contracts?|agreements?|deal|role|package|scope|services?|'
    r'consultant|consultancy|tender|commission|mandate)\b',
    # named phases of design work
    r'\b(?:detailed|schematic|concept|conceptual|preliminary|architectural|'
    r'structural|lead|principal|main)\s+design\b',
    r'\bdesign[\s-]?(?:build|and\s+build|&\s*build)\b',
    # supervision of works, however worded
    r'\b(?:construction|site|works|project)\s+supervision\b',
    r'\bsupervision\s+(?:consultant\w*|works)\b',
    r'\bsupervis(?:e|es|ing|ory)\s+(?:the\s+)?'
    r'(?:works|work|construction|project|steel|delivery)\b',
    # a firm being engaged
    r'\b(?:lead|principal|main|design|supervision)\s+consultant\w*\b',
    r'\bMEP\s+consultant\w*\b',
    r'\b(?:appointed|selected|named|engaged)\s+(?:as|to|for)\b[^.]{0,40}'
    r'(?:design|supervis|consultan)',
    r'\b(?:wins?|won|secures?|secured|awards?|awarded|grants?|granted|'
    r'signs?|signed|issues?|issued)\b[^.]{0,50}'
    r'(?:design|supervision|consultanc\w+|consultant)',
    r'\b(?:design|supervision|consultanc\w+)\b[^.]{0,30}'
    r'(?:contract|role|agreement|appointment|mandate)\b',
    # a consultancy engaged to deliver a scheme. Recovers "Mace Consult To
    # Drive Delivery Of Oman's Flagship Cultural Complex", which names no
    # contract and would otherwise be lost.
    r'\bconsult(?:s|ing|ancy|ants?)?\b[^.]{0,45}'
    r'(?:deliver\w*|project|complex|development|scheme|masterplan)',
    # Arabic. Note the grammar: a genitive construct needs no preposition,
    # so "تصميم مستشفى" already means "design OF a hospital". Demanding an
    # of/for equivalent the way the English rules do would drop real scopes,
    # so here design/supervision followed by another Arabic word counts.
    # [أا] so both أعمال and اعمال match: the hamza is
    # written inconsistently in these feeds, and a miss silently drops the row.
    r'(?:[\u0623\u0627]\u0639\u0645\u0627\u0644|\u062e\u062f\u0645\u0627\u062a)\s*'
    r'(?:\u0627\u0644)?(?:\u0625\u0634\u0631\u0627\u0641|\u0627\u0634\u0631\u0627\u0641|'
    r'\u062a\u0635\u0645\u064a\u0645|\u062a\u0635\u0627\u0645\u064a\u0645)',
    r'(?:\u0627\u0644)?(?:\u0625\u0634\u0631\u0627\u0641|\u0627\u0634\u0631\u0627\u0641)'
    r'\s*(?:\u0639\u0644\u0649|\u0648|[\u0621-\u064a])',
    r'(?:\u0627\u0644)?(?:\u062a\u0635\u0645\u064a\u0645|\u062a\u0635\u0627\u0645\u064a\u0645)'
    r'\s*(?:\u0648|\u0644|\u0639\u0644\u0649|[\u0621-\u064a])',
)

DESIGN_WORK_RE = re.compile(_same_sentence('|'.join(_DESIGN_WORK_PATTERNS)), re.I)


#: Arabic negations: "without supervision", "with no design". The English
#: rules refuse a bare mention by demanding that the work be commissioned;
#: the Arabic rules accept a genitive construct instead, which is right for
#: "تصميم مستشفى" (design OF a hospital) and wrong for a negated one. The
#: module docstring names "children ordering food without supervision" as the
#: false positive the English tightening was for; its Arabic twin still
#: passed. This refuses that shape and nothing else.
_ARABIC_NEGATION_RE = re.compile(
    r'(?:\u0628\u062f\u0648\u0646|\u062f\u0648\u0646|\u0628\u0644\u0627)\s*'
    r'(?:\u0627\u0644)?'
    r'(?:\u0625\u0634\u0631\u0627\u0641|\u0627\u0634\u0631\u0627\u0641|'
    r'\u062a\u0635\u0645\u064a\u0645|\u062a\u0635\u0627\u0645\u064a\u0645)')


#: تصميم is design AND resolve. "وتصميمنا أقوى" - "our RESOLVE is
#: stronger" - is a Prime Minister's speech, and the design rule below kept it,
#: because that rule only asks for تصميم followed by another letter and the
#: attached pronoun نا is another letter.
#:
#: What separates the two senses is an object: design in a news headline is
#: always the design OF something. So تصميم carrying a possessive pronoun is
#: read as resolve UNLESS the text also names built work or the engineering
#: trade. تصميمها stays eligible on its own - "the buildings whose تصميمها was
#: completed" is ordinary usage - and is caught by the same context test.
#: إشراف is supervision of WORKS and oversight by a REGULATOR. "خاضع
#: لإشراف البنك المركزي" - "subject to the oversight of the Central Bank" -
#: is a crypto exchange being licensed, and it reached the News page labelled
#: DESIGN because the bare إشراف rule asks only for another letter after it.
#:
#: The regulator is always named, so naming the regulators is the test.
_ARABIC_OVERSIGHT_RE = re.compile(
    r'(?:\u0625\u0634\u0631\u0627\u0641|\u0627\u0634\u0631\u0627\u0641)\s*'
    r'(?:\u0627\u0644)?'
    r'(?:\u0628\u0646\u0643|\u0627\u0644\u0628\u0646\u0643|'          # بنك
    r'\u0647\u064a\u0626\u0629|\u0627\u0644\u0647\u064a\u0626\u0629|'  # هيئة
    r'\u0648\u0632\u0627\u0631\u0629|\u0627\u0644\u0648\u0632\u0627\u0631\u0629|'  # وزارة
    r'\u0645\u062c\u0644\u0633|\u0627\u0644\u062c\u0647\u0627\u062a|'  # مجلس / الجهات
    r'\u0627\u0644\u0645\u0631\u0643\u0632\u064a|'                        # المركزي
    r'\u0627\u0644\u0646\u064a\u0627\u0628\u0629|\u0627\u0644\u0642\u0636\u0627\u0621)')  # النيابة/القضاء


_ARABIC_RESOLVE_RE = re.compile(
    r'(?:\u0627\u0644)?\u062a\u0635\u0645\u064a\u0645'
    r'(?:\u0646\u0627|\u0647\u0645|\u0647\u0646|\u0643\u0645|'
    r'\u0643\u0646|\u0647\u0627|\u0647|\u064a|\u0643)\b')


def reads_as_design_work(text: str) -> bool:
    """
    True when `text` describes design or supervision being commissioned or
    delivered — not merely mentioned.

    This is the rule the feeds actually use. reads_as_design_or_supervision()
    is kept below as the looser predicate it was, because it states the
    vocabulary plainly and the tests contrast the two.
    """
    blob = text or ''
    if _ARABIC_NEGATION_RE.search(blob):
        return False
    # تصميم carrying a possessive pronoun is resolve, not design, unless the
    # text also names built work or the engineering trade.
    if (_ARABIC_RESOLVE_RE.search(blob)
            and not _ENGINEERING_AR_RE.search(blob)
            and not re.search(_BUILT_WORK_AR, blob)):
        return False
    # Oversight by a named regulator is not supervision of works - unless the
    # text also names built work, which is how "the ministry supervising the
    # hospital contract" keeps its place. A built-work word that the veto list
    # has already disowned does not count as naming anything.
    if _ARABIC_OVERSIGHT_RE.search(blob):
        names_real_work = (re.search(_BUILT_WORK_AR, blob)
                           and not _NOT_A_PROJECT_AR_RE.search(blob))
        if not names_real_work:
            return False
    # The engineering trade named outright - but not when the money desk or
    # the ceremony page is what is actually being reported.
    if _ENGINEERING_AR_RE.search(blob) and not _NOT_A_PROJECT_AR_RE.search(blob):
        return True
    return bool(DESIGN_WORK_RE.search(blob))


# ── construction PROJECT news ───────────────────────────────────────────────
# The third keep-route, and the one that decides whether the News page looks
# current. The two routes above answer "is a tender open?" and "is design being
# commissioned?". Both are narrow on purpose, and together they kept 5 of the
# 200 most recent posts across Construction Week and CBNME on 13 September
# 2026 — so the page sat eleven days stale while ~90 recent articles were
# refused, among them ground-breakings, awarded construction contracts and new
# master-planned communities.
#
# The rule is EVENT + BUILT WORK. Something must be HAPPENING (a contract
# awarded, ground broken, a scheme unveiled, a firm engaged to deliver) TO a
# named piece of built work (a stadium, a metro, a development, a hospital).
# Either half alone is what the noise looks like:
#
#   event only       "Careem unveils a new subscription tier"
#   built work only  "A day in the life of a Dubai hospital porter"
#   shares a verb    "Dubai launches Future Week for investors"
#   industry, not a project   "Cement prices rise 4% across the GCC"
#
# Whether a kept item is AEC is still NOT decided here. That remains the shared
# classification service's job, exactly as for every other tender in the
# system — this route widens what reaches the classifier, nothing more.

#: A piece of built work. Deliberately concrete nouns: an abstract one
#: ("solution", "platform", "network") is what let software and airline stories
#: through when this was first tried.
_BUILT_WORK = (
    r'(?:project|projects|development|developments|scheme|schemes|'
    r'master[\s-]?plan\w*|master[\s-]?planned|mixed[\s-]?use|'
    # NOT a bare 'community': it names a group of people at least as
    # often as a housing development, and on the live feed it was the
    # only thing holding up a seasonal market and a charity's digital
    # platform. The residential sense always says so another way -
    # 'master-planned community', 'residential community' - and both
    # of those match on their own words above.
    r'tower|towers|complex|district|precinct|'
    r'residential\s+communit\w+|gated\s+communit\w+|'
    r'stadium|arena|metro|railway|rail\s+line|train\s+line|tram|'
    r'airport|seaport|port|'
    r'hospital|clinic|school|campus|university|museum|mosque|'
    r'resort|hotel|mall|retail|residential|villa|villas|housing|'
    r'road|roads|highway|bridge|tunnel|interchange|'
    r'sewer\w*|drainage|water\s+network|pipeline|'
    r'power\s+plant|substation|desalination|treatment\s+plant|'
    r'terminal|facility|facilities|infrastructure|building|buildings|'
    r'construction\s+works|enabling\s+works|contract)'
)

#: One rule per line, each an EVENT bound to built work. Kept as a tuple
#: rather than one re.X blob for the same reason as _DESIGN_WORK_PATTERNS:
#: a blob silently matched almost nothing and could not be tested a line
#: at a time.
_PROJECT_NEWS_PATTERNS = (
    # a contract or package changing hands
    r'\b(?:awards?|awarded|wins?|won|secures?|secured|lands?|clinch\w*)\b'
    r'[^.]{0,60}?\b(?:contract|deal|package|role|mandate|works)\b',
    r'\b(?:construction|build|design[\s-]?build|epc|mep|civil|enabling)\s+'
    r'contract\b',
    # ground broken, work started, work finished
    r'\b(?:break\w*|broke)\s+ground\b',
    r'\bground[\s-]?break\w*\b',
    r'\b(?:begins?|began|starts?|started|commenc\w+|resum\w+)\s+'
    r'(?:\w+\s+){0,2}(?:construction|works|work|building)\b',
    # and the same sentence written the other way round:
    # 'Construction Of Hafeet Rail Begins'.
    r'\b(?:construction|building|works)\s+(?:of|on)\b[^.]{0,60}?'
    r'\b(?:begins?|began|starts?|started|commenc\w+|under\s?way)\b',
    r'\b(?:completes?|completed|inaugurat\w+|hands?\s+over|'
    r'handed\s+over|tops?\s+out|topped\s+out)\b[^.]{0,50}?\b' + _BUILT_WORK + r'\b',
    # a scheme put before the market
    r'\b(?:unveil\w*|reveal\w*|announc\w*|introduc\w*|launch\w*|'
    r'approv\w*|greenlight\w*|advanc\w*|progress\w*)\b'
    r'[^.]{0,60}?\b' + _BUILT_WORK + r'\b',
    # a firm engaged to do the work
    r'\b(?:appoint\w*|engag\w*|select\w*|name[sd]?|tap\w*)\b'
    r'[^.]{0,60}?\b(?:to\s+(?:deliver|build|construct|design|supervise|'
    r'develop)|as\s+(?:the\s+)?(?:main|lead|principal)\s+'
    r'(?:contractor|consultant|developer)|for\s+(?:the\s+)?(?:\w+\s+){0,2}(?:works|construction|package))\b',
    # a contract signed for built work
    r'\bsign\w*\b[^.]{0,40}?\b(?:contract|agreement|deal)\b'
    r'[^.]{0,50}?\b' + _BUILT_WORK + r'\b',
)

#: Story shapes that borrow project vocabulary without being a project.
#: Each one was let through by the patterns above when the route was first
#: measured against the stored feeds: a partnership announcement at an airport,
#: a funding round for a supply chain, a rental index "for shared housing", a
#: market-boom column, an awards shortlist. The subject is right and the event
#: is not, which no amount of noun-list tuning fixes — so they are named.
#: ── Arabic ────────────────────────────────────────────────────────────────
#: The three routes above are English-only apart from the tender wording, so an
#: Arabic source was judged by half the filter: TENDER could fire, PROJECT
#: never could. Al-Rai's اقتصاد and محليات sections are the first wholly Arabic
#: feed, and PROJECT is the route that carries construction news.
#:
#: Same shape as the English rule - EVENT + BUILT WORK in one sentence - for
#: the same reason. Measured over 504 Al-Rai headlines, a bare word list kept
#: 13 and among them a speech about resolve, a bill before parliament and a
#: training programme.

#: A piece of built work. Concrete nouns only, as in _BUILT_WORK.
_BUILT_WORK_AR = (
    # NOT bare مشروع/مشاريع. "Project" is what a macro announcement, a
    # ministerial inspection and a progress percentage all say, and it put
    # eight such stories on the News page. Every real scheme names the
    # thing itself - مشروع الصرف الصحي matches on صرف صحي, the hospital
    # extension on مستشفى, the carrier on الناقل الوطني.
    r'(?:\u0645\u0628\u0646\u0649|\u0645\u0628\u0627\u0646|\u0623\u0628\u0646\u064a\u0629|\u0627\u0628\u0646\u064a\u0629|'  # مبنى/مبان/أبنية
    r'\u0637\u0631\u064a\u0642|\u0637\u0631\u0642|\u0634\u0627\u0631\u0639|'          # طريق/طرق/شارع
    r'\u062c\u0633\u0631|\u062c\u0633\u0648\u0631|\u0646\u0641\u0642|\u062a\u0642\u0627\u0637\u0639|'  # جسر/جسور/نفق/تقاطع
    r'\u0633\u062f|\u0633\u062f\u0648\u062f|'                                                # سد/سدود
    r'\u0645\u0633\u062a\u0634\u0641\u0649|\u0645\u0633\u062a\u0634\u0641\u064a\u0627\u062a|'          # مستشفى
    r'\u0645\u062f\u0631\u0633\u0629|\u0645\u062f\u0627\u0631\u0633|\u062c\u0627\u0645\u0639\u0629|\u0643\u0644\u064a\u0629|'  # مدرسة/مدارس/جامعة
    r'\u0625\u0633\u0643\u0627\u0646|\u0627\u0633\u0643\u0627\u0646|'                    # إسكان
    r'\u0645\u0637\u0627\u0631|\u0645\u064a\u0646\u0627\u0621|'                            # مطار/ميناء
    r'\u0645\u0635\u0646\u0639|\u0645\u0635\u0627\u0646\u0639|'                            # مصنع/مصانع
    r'\u0635\u0646\u0627\u0639\u064a\u0629|'                                                    # صناعية
    r'\u0641\u0646\u062f\u0642|\u0645\u0646\u062a\u062c\u0639|\u0628\u0631\u062c|\u0623\u0628\u0631\u0627\u062c|'  # فندق/منتجع/برج
    r'\u0635\u0631\u0641\s*(?:\u0627\u0644)?\u0635\u062d\u064a|'                                              # صرف صحي
    # More of the objects Jordanian project news actually names.
    # NOT خزان / بئر / محكمة / حديقة / رصف: each is a substring of a
    # commoner word once ال or و is attached, and Arabic gives no word
    # boundary to anchor against. خزان matched وزير الخزانة - the US
    # Treasury Secretary - and put him on the News page.
    r'\u0633\u0643\u0629\s*(?:\u0627\u0644)?\u062d\u062f\u064a\u062f|\u0642\u0637\u0627\u0631|\u0645\u0633\u062a\u0648\u062f\u0639|'      # سكة حديد/قطار/مستودع
    r'\u0645\u0644\u0639\u0628|\u0627\u0633\u062a\u0627\u062f|\u0645\u062a\u062d\u0641|\u0645\u0633\u062c\u062f|'              # ملعب/استاد/متحف/مسجد
    r'\u0645\u0631\u0643\u0632\s*(?:\u0627\u0644)?\u0635\u062d\u064a|\u0645\u062a\u0646\u0632\u0647|'                      # مركز صحي/متنزه
    r'\u0645\u062e\u062a\u0628\u0631|\u0645\u062e\u062a\u0628\u0631\u0627\u062a|'                    # مختبر/مختبرات
    r'(?:\u0627\u0644)?\u0628\u0646\u064a\u0629\s*(?:\u0627\u0644)?\u062a\u062d\u062a\u064a\u0629|'                        # بنية تحتية
    r'\u0645\u062d\u0637\u0629\s*(?:\u0627\u0644)?(?:\u062a\u0646\u0642\u064a\u0629|\u0645\u0639\u0627\u0644\u062c\u0629|'
    r'\u062a\u062d\u0644\u064a\u0629|\u062a\u0648\u0644\u064a\u062f|\u0643\u0647\u0631\u0628\u0627\u0621)|'  # محطة + plant word only
    r'\u0634\u0628\u0643\u0629\s*(?:\u0627\u0644)?(?:\u0645\u064a\u0627\u0647|\u0643\u0647\u0631\u0628\u0627\u0621|'
    r'\u0635\u0631\u0641)|'                                                                        # شبكة مياه/كهرباء/صرف
    r'(?:\u0627\u0644)?\u062e\u0637\s*(?:\u0627\u0644)?\u0646\u0627\u0642\u0644|\u0627\u0644\u0646\u0627\u0642\u0644\s*\u0627\u0644\u0648\u0637\u0646\u064a)'  # خط ناقل/الناقل الوطني
)

#: Something HAPPENING to it.
_PROJECT_EVENT_AR = (
    r'(?:\u0625\u062d\u0627\u0644\u0629|\u0627\u062d\u0627\u0644\u0629|\u062a\u0631\u0633\u064a\u0629|'  # إحالة/ترسية
    r'\u062d\u062c\u0631\s*\u0627\u0644[\u0623\u0627]\u0633\u0627\u0633|'                # حجر الأساس
    r'\u0627\u0641\u062a\u062a\u0627\u062d|\u064a\u0641\u062a\u062a\u062d|\u0627\u0641\u062a\u062a\u062d|'  # افتتاح
    r'\u0625\u0637\u0644\u0627\u0642|\u0627\u0637\u0644\u0627\u0642|\u064a\u0637\u0644\u0642|'  # إطلاق
    r'\u0628\u062f\u0621|\u0627\u0644\u0628\u062f\u0621|\u0627\u0646\u0637\u0644\u0627\u0642|\u0627\u0644\u0634\u0631\u0648\u0639|'  # بدء/انطلاق
    r'\u0625\u0646\u062c\u0627\u0632|\u0627\u0646\u062c\u0627\u0632|\u0627\u0633\u062a\u0643\u0645\u0627\u0644|'  # إنجاز/استكمال
    r'\u0625\u0646\u0634\u0627\u0621|\u0627\u0646\u0634\u0627\u0621|\u062a\u0634\u064a\u064a\u062f|'  # إنشاء/تشييد
    r'\u062a\u0648\u0633\u0639\u0629|\u062a\u0637\u0648\u064a\u0631|'                    # توسعة/تطوير
    r'\u0628\u0643\u0644\u0641\u0629|\u0628\u0642\u064a\u0645\u0629|\u0628\u0627\u0633\u062a\u062b\u0645\u0627\u0631|'  # بكلفة/بقيمة
    r'\u062a\u0645\u0648\u064a\u0644|'                              # تمويل
    r'\u0627\u0633\u062a\u062b\u0645\u0627\u0631\u064a\u0629|\u0627\u0633\u062a\u062b\u0645\u0627\u0631\u064a|'  # استثمارية
    r'\u062a\u0648\u0642\u064a\u0639\s*(?:\u0627\u062a\u0641\u0627\u0642\u064a\u0629|\u0639\u0642\u062f))'  # توقيع عقد
)

#: The engineering trade itself, for "any field related to engineering".
_ENGINEERING_AR_RE = re.compile(
    r'\u0627\u0644\u0642\u0637\u0627\u0639\s*\u0627\u0644\u0647\u0646\u062f\u0633\u064a|'      # القطاع الهندسي
    r'\u0642\u0637\u0627\u0639\s*(?:\u0627\u0644\u0625\u0646\u0634\u0627\u0621\u0627\u062a|'
    r'\u0627\u0644\u0627\u0646\u0634\u0627\u0621\u0627\u062a|\u0627\u0644\u0628\u0646\u0627\u0621)|'  # قطاع الإنشاءات/البناء
    r'\u0646\u0642\u0627\u0628\u0629\s*\u0627\u0644\u0645\u0647\u0646\u062f\u0633\u064a\u0646|'  # نقابة المهندسين
    r'\u0627\u0644\u0645\u0647\u0646\u062f\u0633\u064a\u0646|'                                        # المهندسين
    r'\u0627\u0644\u0645\u0642\u0627\u0648\u0644\u0627\u062a|\u0645\u0642\u0627\u0648\u0644|'      # المقاولات/مقاول
    r'\u062a\u0631\u0627\u062e\u064a\u0635\s*\u0627\u0644[\u0623\u0627]\u0628\u0646\u064a\u0629|'  # تراخيص الأبنية
    r'\u062f\u0631\u0627\u0633\u0629\s*\u062c\u062f\u0648\u0649|'                                    # دراسة جدوى
    r'\u0645\u062e\u0637\u0637\s*\u0634\u0645\u0648\u0644\u064a')                                    # مخطط شمولي

#: What the Arabic money desk and the ceremony pages publish. Includes the
#: false friends: مشروع قانون is a BILL, حيز التنفيذ is "comes into effect",
#: المحطة الأولى/الثانية... is a STAGE, and تأهيل الكفاءات is training people.
_NOT_A_PROJECT_AR_RE = re.compile(
    # ── crime and conflict ───────────────────────────────
    # إطلاق نار is gunfire, not a launch. A school shooting was kept
    # as a PROJECT because إطلاق is a launch verb and مدرسة is built
    # work — the building was only where it happened.
    r'\u0625\u0637\u0644\u0627\u0642\s*\u0646\u0627\u0631|\u0625\u0635\u0627\u0628\u0627\u062a|\u0625\u0635\u0627\u0628\u0629|\u0645\u0642\u062a\u0644|\u0642\u062a\u0644\u0649|'
    r'\u0627\u0639\u062a\u062f\u0627\u0621|\u062c\u0631\u064a\u0645\u0629|\u0627\u0646\u0641\u062c\u0627\u0631|\u0625\u0637\u0644\u0627\u0642\s*\u0633\u0631\u0627\u062d|'
    # ── procurement is already over ───────────────────────
    # Opened, running, finished. Real construction news, and worth
    # nothing to a design and supervision practice — by the ribbon the
    # designer was appointed years ago.
    r'\u0627\u0641\u062a\u062a\u0627\u062d|\u064a\u0641\u062a\u062a\u062d|\u0627\u0641\u062a\u062a\u062d|\u062a\u0641\u062a\u062a\u062d|'
    r'\u0627\u0643\u062a\u0645\u0627\u0644|\u0627\u0643\u062a\u0645\u0644|'
    # Operating disqualifies only when finished work is being brought
    # into service. إنشاء وتشغيل is a concession being offered and
    # nobody has designed it yet — a bare تشغيل veto lost the Ajloun
    # hotel, which is precisely the work this practice wants.
    r'\u0628\u062f\u0621\s*(?:\u0627\u0644)?\u062a\u0634\u063a\u064a\u0644|(?:\u0627\u0644)?\u062a\u0634\u063a\u064a\u0644\s*(?:\u0627\u0644)?\u062a\u062c\u0631\u064a\u0628\u064a|'
    # The foundation stone, dropped by the reader's own instruction:
    # the design is finished by then and the supervision appointed
    # with it.
    r'\u062d\u062c\u0631\s*(?:\u0627\u0644)?\u0623\u0633\u0627\u0633|'
    # Commentary on a scheme already under way — it boosts, it cuts,
    # it contributes. No procurement event anywhere in it.
    r'\u064a\u0639\u0632\u0632|\u062a\u0639\u0632\u0632|\u064a\u062e\u0641\u0636|\u062a\u062e\u0641\u0636|\u064a\u0633\u0627\u0647\u0645|\u062a\u0633\u0627\u0647\u0645|'
    # ── general news, not construction ─────────────────────
    # A hoard found in a factory wall. جدار and مصنع are both built
    # work and العثور is an event; it is a crime story.
    r'\u0643\u0646\u0632|\u0639\u062b\u0648\u0631|\u064a\u0639\u062b\u0631|'
    # An airline opening a route is not anybody building an airport.
    r'\u0631\u062d\u0644\u0627\u062a|\u0644\u0644\u0637\u064a\u0631\u0627\u0646|'
    # The engineers' syndicate on admissions and the state of the
    # profession. نقابة المهندسين has now kept three non-engineering
    # stories — a cooperation meeting, a research grant and this — so
    # what it says has to be work, not commentary.
    r'\u0637\u0644\u0627\u0628|\u0645\u062c\u0627\u0645\u064a\u0639|(?:\u0627\u0644)?\u0645\u0647\u0646\u0629|'
    # ── an activity held at a place ──────────────────────
    # Festivals, seminars, workshops, ceremonies — things that HAPPEN in
    # a building rather than things done to one.
    r'\u0641\u0639\u0627\u0644\u064a\u0627\u062a|\u0645\u0647\u0631\u062c\u0627\u0646|\u0646\u062f\u0648\u0629|\u0648\u0631\u0634\u0629\s*\u0639\u0645\u0644|\u0627\u062d\u062a\u0641\u0627\u0644|'
    # Training, but only as a programme. The bare word stays out of this:
    # مركز تدريب مهني is a building somebody has to put up.
    r'\u062a\u062f\u0631\u064a\u0628\s*\u0645\u064a\u062f\u0627\u0646\u064a|\u0645\u0646\u0638\u0648\u0645\u0629\s*\u062a\u062f\u0631\u064a\u0628|'
    r'\u0628\u0631\u0646\u0627\u0645\u062c\s*\u062a\u062f\u0631\u064a\u0628\u064a|\u0628\u0631\u0627\u0645\u062c\s*\u062a\u062f\u0631\u064a\u0628\u064a\u0629|'
    r'\u062f\u0648\u0631\u0629\s*\u062a\u062f\u0631\u064a\u0628\u064a\u0629|\u062f\u0648\u0631\u0627\u062a\s*\u062a\u062f\u0631\u064a\u0628\u064a\u0629|'
    # ── nothing has been commissioned ───────────────────────
    # Residents CALLING FOR works. Plural دعوات only: the singular دعوة
    # is an invitation to bid, which is the opposite and must pass.
    r'\u062f\u0639\u0648\u0627\u062a|\u0645\u0637\u0627\u0644\u0628\u0627\u062a|\u064a\u0637\u0627\u0644\u0628\u0648\u0646|\u0646\u0627\u0634\u062f|'
    # Operating a thing is not building it. A traffic signal going live
    # is traffic management, whatever works came before it.
    r'(?:\u0627\u0644)?\u0625\u0634\u0627\u0631\u0629\s*(?:\u0627\u0644)?\u0636\u0648\u0626\u064a\u0629|'
    # A body meeting to discuss procedures, routes or ways and means.
    r'\u062a\u0628\u062d\u062b\s*(?:\u0627\u0644)?(?:\u0625\u062c\u0631\u0627\u0621\u0627\u062a|\u0633\u0628\u0644|\u0622\u0644\u064a\u0627\u062a)|'
    # An intention to build, and buying the land to build on. Both come
    # before anybody is appointed to design or build anything.
    r'\u062a\u0648\u062c\u0647\s*(?:\u0644|\u0625\u0644\u0649)|\u0627\u0633\u062a\u0645\u0644\u0627\u0643|'
    # Academic and cultural money. A university is real built work and
    # funding is a real event, so a research grant to two universities
    # read as a construction scheme. The funder is what gives it away.
    r'\u0627\u0644\u0645\u062c\u0644\u0633\s*\u0627\u0644\u062b\u0642\u0627\u0641\u064a|\u0645\u0646\u062d\u0629\s*(?:\u0628\u062d\u062b\u064a\u0629|\u062f\u0631\u0627\u0633\u064a\u0629)|\u0627\u0644\u0628\u062d\u062b\s*\u0627\u0644\u0639\u0644\u0645\u064a|\u0623\u0643\u0627\u062f\u064a\u0645\u064a|'
    r'\u0645\u0634\u0631\u0648\u0639\s*(?:\u0642\u0627\u0646\u0648\u0646|\u0646\u0638\u0627\u0645|'
    r'\u0627\u0644\u0645\u0648\u0627\u0632\u0646\u0629|\u0642\u0631\u0627\u0631)|'          # مشروع قانون
    r'\u062d\u064a\u0632\s*\u0627\u0644\u062a\u0646\u0641\u064a\u0630|'                      # حيز التنفيذ
    r'\u0627\u0644\u0645\u062d\u0637\u0629\s*\u0627\u0644(?:\u0623\u0648\u0644\u0649|'
    r'\u062b\u0627\u0646\u064a\u0629|\u062b\u0627\u0644\u062b\u0629|\u0631\u0627\u0628\u0639\u0629|'
    r'\u062e\u0627\u0645\u0633\u0629)|'                                                              # المحطة الرابعة
    r'\u062a\u0623\u0647\u064a\u0644\s*(?:\u0627\u0644\u0643\u0641\u0627\u0621\u0627\u062a|'
    r'\u0627\u0644\u0643\u0648\u0627\u062f\u0631|\u0627\u0644\u0645\u0648\u0627\u0631\u062f)|'  # تأهيل الكفاءات
    r'[\u0623\u0627]\u0633\u0639\u0627\u0631\s*\u0627\u0644\u0641\u0627\u0626\u062f\u0629|'      # أسعار الفائدة
    r'\u0627\u0644\u0630\u0647\u0628|\u0627\u0644\u0646\u0641\u0637|\u0627\u0644\u062f\u0648\u0644\u0627\u0631|'  # الذهب/النفط/الدولار
    r'\u0627\u0644\u0628\u0648\u0631\u0635\u0629|\u0627\u0644\u0623\u0633\u0647\u0645|\u0627\u0644\u062a\u062f\u0627\u0648\u0644|'  # البورصة/الأسهم
    r'\u0627\u0644\u062a\u0636\u062e\u0645|\u0627\u0644\u0645\u0648\u0627\u0632\u0646\u0629|\u0627\u0644\u062f\u064a\u0646\s*\u0627\u0644\u0639\u0627\u0645|'  # التضخم/الموازنة
    r'\u0635\u0627\u062f\u0631\u0627\u062a|\u0648\u0627\u0631\u062f\u0627\u062a|'              # صادرات/واردات
    r'\u0627\u0644\u0628\u0646\u0648\u0643|\u0627\u0644\u0645\u0631\u0643\u0632\u064a|\u0642\u0631\u0648\u0636|'  # البنوك/المركزي/قروض
    r'\u0646\u062f\u0648\u0629|\u0645\u0624\u062a\u0645\u0631|\u0648\u0631\u0634\u0629|'      # ندوة/مؤتمر/ورشة
    r'\u062c\u0627\u0626\u0632\u0629|\u062a\u0643\u0631\u064a\u0645|'
    # مختبر is a laboratory and a regulatory SANDBOX. A crypto
    # exchange entering the central bank's المختبر التنظيمي is not
    # a fit-out.
    r'\u0645\u062e\u062a\u0628\u0631\s*(?:\u0627\u0644)?\u062a\u0646\u0638\u064a\u0645\u064a|'
    # البنية التحتية is civil works and DIGITAL infrastructure. Data
    # centres and connectivity are not ours; the qualifier says which.
    r'\u062a\u062d\u062a\u064a\u0629\s*(?:\u0627\u0644)?(?:\u0631\u0642\u0645\u064a\u0629|\u062a\u0642\u0646\u064a\u0629|\u0645\u0639\u0644\u0648\u0645\u0627\u062a\u064a\u0629|\u0633\u062d\u0627\u0628\u064a\u0629)|'                          # جائزة/تكريم
    # A minister touring sites, or signing a memorandum, is a diary
    # item. توقيع عقد stays - that is an award; اتفاقية is not.
    r'\u064a\u062a\u0641\u0642\u062f|\u062a\u0641\u0642\u062f|\u062c\u0648\u0644\u0629|'                # يتفقد/تفقد/جولة
    r'\u0645\u0630\u0643\u0631\u0629\s*\u062a\u0641\u0627\u0647\u0645|\u062a\u0648\u0642\u064a\u0639\s*\u0627\u062a\u0641\u0627\u0642\u064a|'  # مذكرة تفاهم
    r'\u0634\u0631\u0627\u0643\u0629|\u0645\u062c\u0644\u0633\s*(?:\u0627\u0644)?\u0627\u0633\u062a\u0634\u0627\u0631\u064a|'
    # ... and two bodies meeting to discuss it is the same thing.
    r'\u062a\u0639\u0632\u064a\u0632\s*(?:\u0627\u0644)?\u062a\u0639\u0627\u0648\u0646|\u0633\u0628\u0644\s*(?:\u0627\u0644)?\u062a\u0639\u0627\u0648\u0646|\u062a\u0628\u062d\u062b\u0627\u0646|'          # شراكة/مجلس استشاري
    # Impact, not work: jobs created, costs reduced, percent complete.
    r'\u0641\u0631\u0635\s*\u0639\u0645\u0644|'                              # فرص عمل
    r'\u064a\u062e\u0641\u0641|\u062a\u062e\u0641\u064a\u0636\s*\u0643\u0644\u0641|'  # يخفف/تخفيض كلف
    r'\u0646\u0633\u0628\u0629\s*\u0627\u0644\u0625\u0646\u062c\u0627\u0632|\u0625\u0646\u062c\u0627\u0632\s*(?:\u0623\u0643\u062b\u0631|\d)|'        # إنجاز 81%
    # A tender to BUY something is not our tender. توريد وتركيب
    # (supply AND install) is, so the commodity is named, not the verb.
    r'\u0627\u0644\u0642\u0645\u062d|\u0634\u0639\u064a\u0631|\u0623\u0639\u0644\u0627\u0641|\u0627\u0639\u0644\u0627\u0641|'            # قمح/شعير/أعلاف
    r'\u0623\u062f\u0648\u064a\u0629|\u0642\u0631\u0637\u0627\u0633\u064a\u0629|\u0645\u062d\u0631\u0648\u0642\u0627\u062a|\u0648\u0642\u0648\u062f|'      # أدوية/قرطاسية/وقود
    r'\u0627\u0644\u0646\u0641\u0627\u064a\u0627\u062a|\u0627\u0644\u0646\u0638\u0627\u0641\u0629|\u0627\u0644\u062a\u0623\u0645\u064a\u0646')                                  # النفايات/النظافة/التأمين

#: Arabic sentences break on ، and ؛ as well as the full stop.
_GAP_AR = r'(?:[^.\u060c\u061b]|\.(?!\s))'
PROJECT_NEWS_AR_RE = re.compile(
    f'{_PROJECT_EVENT_AR}{_GAP_AR}{{0,60}}{_BUILT_WORK_AR}'
    f'|{_BUILT_WORK_AR}{_GAP_AR}{{0,60}}{_PROJECT_EVENT_AR}')


_NOT_A_PROJECT_RE = re.compile(
    r'\b(?:partnership|partners\s+with|memorandum\s+of\s+understanding|\bMoU\b|'
    r'funding\s+round|series\s+[A-D]\s+(?:funding|round)|raises?\s+\$|'
    r'investment\s+window|property\s+boom|real\s+estate\s+boom|'
    r'rental\s+index|price\s+index|index\s+for|'
    r'survey|white\s?paper|market\s+report|quarterly\s+report|'
    r'shortlist|awards?\s+(?:19|20)\d{2}|award\s+winners?|'
    r'open\s+for\s+business|'
    # '...building' as an ABSTRACT noun, not a structure. A beverages
    # company launching a "capability building" programme led the live
    # News page, because the gerund is spelt exactly like the thing a
    # contractor puts up.
    r'(?:capability|capacity|team|confidence|trust|brand|nation|'
    r'peace|coalition|skills?|consensus)\s+building|'
    # A bank relaunching its logo is not a building project, and a
    # charity opening a donation drive is not a construction scheme.
    # Both reached the page through 'unveils'/'launches'.
    r'brand\s+identity|rebrand\w*|new\s+logo|visual\s+identity|'
    r'donation\s+(?:drive|campaign)|fundrais\w+|charit\w+\s+campaign|'
    r'loyalty\s+programme|subscription\s+(?:tier|plan)|'
    r'forum|summit|conference|webinar|exhibition|'
    r'prices?\s+(?:rise|fall|climb|drop)|sales\s+(?:rise|fall|stay|flat))\b',
    re.I)


PROJECT_NEWS_RE = re.compile(_same_sentence('|'.join(_PROJECT_NEWS_PATTERNS)), re.I)


def project_news_enabled() -> bool:
    """
    Whether the project-news route is on. Read at call time so the setting can
    be flipped without a restart, and so tests can override it.
    """
    return bool(getattr(settings, 'MAGAZINE_KEEP_PROJECT_NEWS', True))


def reads_as_project_news(text: str) -> bool:
    """
    True when `text` reports something happening to a piece of built work.

    Pure and unit-tested on its own, like the other two predicates, because it
    is the rule that decides how current the News page looks.
    """
    blob = text or ''
    if _NOT_A_PROJECT_RE.search(blob) or _NOT_A_PROJECT_AR_RE.search(blob):
        return False
    return bool(PROJECT_NEWS_RE.search(blob)
                or PROJECT_NEWS_AR_RE.search(blob))


# ── people, not projects ────────────────────────────────────────────────────
#: A trade magazine's ranking of individuals. The entries look like
#: "17. Talal Shair, Chairman and CEO, Dar Al-Handasah" and their standfirsts
#: are company blurb full of consultancy language, so with that context the
#: classifier reads them as AEC. It is not wrong about the words — the item is
#: simply not a commission.
_RANKED_ENTRY_RE = re.compile(r'^\s*\d{1,2}\s*[.\uff0e]\s+\S')

#: A job title in an appositive: "..., Managing Partner, ...".
_JOB_TITLE_RE = re.compile(
    r',\s*(?:[A-Z][a-z]+\s+)?'
    r'(?:CEO|CTO|COO|CFO|Chairman|Chairwoman|Chair|President|'
    r'Managing\s+(?:Director|Partner)|Executive\s+Director|'
    r'Technical\s+Director|Project\s+Director|Design\s+Director|'
    r'Regional\s+Director|Director|Partner|Founder|Principal|'
    # "13. Simon Baines, General Manager, House of Consulting Office" slipped
    # through without these: a ranking entry with a headshot for a photo.
    # Not bare "Manager": ", manager appointed" is a real headline.
    r'General\s+Manager|Country\s+Manager|Vice\s+President|'
    r'Head\s+of\s+\w+)\b', re.I)

#: Recurring people-feature column names on these sites.
_PEOPLE_COLUMN_RE = re.compile(
    r'^\s*(?:unsung\s+heroes?|power\s+hour|meet\s+the\s+|'
    r'in\s+conversation\s+with|\d+\s+under\s+\d+)\b', re.I)


#: A ranking marker inside a branded column title: 'FM Power 30:', 'Top 25 -'.
_RANKED_COLUMN_RE = re.compile(r'\b(?:power|top|hot|best|leading)\s*\d{1,3}\b'
                               r'|\b\d{1,3}\s*:\s*\S', re.I)


def reads_as_person_profile(text: str) -> bool:
    """
    True when `text` is about a PERSON rather than a project or a commission.

    Requires a numbered-list opening AND a job title, so a real title that
    happens to begin with a number — "1. Design of the new hospital" — is not
    caught. The named people-columns are matched on their own because their
    format is unmistakable.
    """
    head = (text or '').strip().split('\n')[0][:160]
    if _PEOPLE_COLUMN_RE.search(head):
        return True
    if _RANKED_ENTRY_RE.search(head) and _JOB_TITLE_RE.search(head):
        return True
    # The branded variant: the ranking marker sits mid-title rather than at
    # the start, so the entry above cannot match it.
    return bool(_RANKED_COLUMN_RE.search(head) and _JOB_TITLE_RE.search(head))



#: Which rendition of a WordPress featured image to store, best first. The
#: cards render at roughly 400px wide, so the ~768px `medium_large` is the
#: right size; `medium` (300px) is acceptable; the original is a last resort
#: because it can be 4000px and several megabytes, hotlinked on every visit.
_WP_IMAGE_SIZES = ('medium_large', 'large', 'medium', 'full')

#: The rendition for the article hero, which displays at up to ~1,400 px.
#: `1536x1536` is WordPress's own high-DPI size; `full` on a modern install is
#: the auto-scaled 2560 px version; `large` (1024) is the floor before we
#: would rather reuse the card image than upscale.
_WP_HERO_SIZES = ('1536x1536', 'full', 'large')

#: Seconds allowed for a WordPress listing WITH embedded media. See _fetch_wp.
WP_EMBED_TIMEOUT = 90

#: Agency and stock-library names as they appear in image filenames. A frame
#: the desk pulled from a library is a picture of a city or a hard hat, not of
#: the project — six of the first 47 stored images were one. Deterministic
#: and cheap; what it cannot catch is a stock frame renamed by the publisher.
_STOCK_RE = re.compile(
    r'getty|shutterstock|istock|unsplash|pexels|depositphotos|adobestock|'
    r'dreamstime|123rf|alamy|freepik|stock[-_]?photo', re.I)


def looks_like_stock(url: str) -> bool:
    """True when the image's filename names a stock library."""
    name = (url or '').rsplit('/', 1)[-1]
    return bool(_STOCK_RE.search(name))


def wp_featured_image(post: dict, prefer=_WP_IMAGE_SIZES) -> str:
    """
    The featured image URL embedded in a WordPress post fetched with ?_embed.

    `prefer` is the rendition order to try — the card sizes by default, the
    hero sizes when asked. Returns '' rather than raising when there is none:
    a post without a photo is normal, not an error, and the page has a
    fallback for it.
    """
    try:
        media = (post.get('_embedded') or {}).get('wp:featuredmedia') or []
        media = media[0] if media else {}
        if not isinstance(media, dict):
            return ''
        sizes = (media.get('media_details') or {}).get('sizes') or {}
        for name in prefer:
            url = (sizes.get(name) or {}).get('source_url')
            if url:
                return url
        return media.get('source_url') or ''
    except (AttributeError, IndexError, TypeError):
        return ''


#: Tashkeel and the kashida. Ad-Dustour writes يُخفّض where Al-Rai writes
#: يخفض, and every Arabic pattern in this module is written bare — so a
#: vowelled headline walked straight past the vetoes. Stripped once, at the
#: top of why_keep(), rather than in each of the thirty-odd patterns.
_TASHKEEL_RE = re.compile('[ً-ْٰـ]')


def strip_tashkeel(text: str) -> str:
    """Arabic vowel marks off. They are decoration; the patterns are bare."""
    return _TASHKEEL_RE.sub('', text or '')


def why_keep(text: str, mode: str = 'either'):
    """
    Decide whether to keep an item, and say which rule kept it.

    Returns '' to drop, or 'TENDER' / 'DESIGN' as the reason. The reason is
    logged and stored, so an operator looking at the page can tell an
    invitation to bid from a design story without opening the article.
    """
    # Vowel marks off before anything is matched: the patterns below are
    # written bare, so يُخفّض would otherwise slip past a veto on يخفض.
    text = strip_tashkeel(text)

    # A person profile is neither, however much consultancy language its
    # standfirst carries. Vetoed before either route is considered.
    if reads_as_person_profile(text):
        return ''

    if mode in ('either', 'tender') and reads_as_tender_announcement(text):
        # An Arabic tender for wheat, fuel or refuse collection is a tender,
        # but not one of ours. The same veto the PROJECT route uses says so,
        # and it is applied here because the tender wording fires first.
        if not _NOT_A_PROJECT_AR_RE.search(text or ''):
            return 'TENDER'
    # The TIGHTER predicate: design/supervision has to be work being
    # commissioned or delivered. A bare mention is not enough — see
    # DESIGN_WORK_RE for what that cost and what it bought.
    if mode in ('either', 'design') and reads_as_design_work(text):
        return 'DESIGN'
    # Last, and only in the widest mode: construction project news. Checked
    # AFTER the other two so a design commission keeps its DESIGN label and an
    # invitation keeps TENDER — an operator reads those labels on the audit
    # page to tell an open bid from a story about one.
    if (mode == 'either' and project_news_enabled()
            and reads_as_project_news(text)):
        return 'PROJECT'
    return ''


def extract_reference(text: str) -> str:
    m = REFERENCE_RE.search(text or '')
    if not m:
        return ''
    ref = (m.group(1) or '').strip(' .,:;-')
    # A bare year or a single word is not a reference number.
    if len(ref) < 4 or re.fullmatch(r'(?:19|20)\d{2}', ref):
        return ''
    return ref


def extract_deadline(text: str, published: Optional[date]) -> Optional[date]:
    """
    A closing date from the article prose, or None.

    These feeds usually omit the year, so it is taken from the publication date
    and rolled forward when the month has already passed — a September article
    saying "closing date 5 March" means next March.
    """
    m = DEADLINE_RE.search(text or '')
    if not m:
        return None

    day = month = year = None
    if m.group(1):                                    # "12 August 2026"
        day, month, year = m.group(1), m.group(2), m.group(3)
    elif m.group(4):                                  # "August 12, 2026"
        month, day, year = m.group(4), m.group(5), m.group(6)
    if not (day and month):
        return None

    num = _MONTH_NUM.get(month.lower().rstrip('.'))
    if not num:
        return None

    base = published or date.today()
    yr = int(year) if year else base.year
    try:
        got = date(yr, num, int(day))
    except ValueError:
        return None
    if not year and got < base - timedelta(days=1):
        try:
            got = date(yr + 1, num, int(day))
        except ValueError:
            return None
    return got



# ── Arabic datelines ────────────────────────────────────────────────────────
# Both Jordanian papers print the date on the ARTICLE page and not in the
# listing, in Arabic prose. The table is here rather than in either scraper
# because it is the same table: a month name fixed for one paper is fixed
# for both.

#: Month names in both systems. Ad-Dustour prints them side by side —
#: "أيلول / سبتمبر" — while Al-Ghad writes the Levantine name alone, so
#: both spellings map to the same number.
ARABIC_MONTHS = {
    '\u0643\u0627\u0646\u0648\u0646 \u0627\u0644\u062b\u0627\u0646\u064a': 1,
    '\u064a\u0646\u0627\u064a\u0631': 1,
    '\u0634\u0628\u0627\u0637': 2,
    '\u0641\u0628\u0631\u0627\u064a\u0631': 2,
    '\u0622\u0630\u0627\u0631': 3,
    '\u0645\u0627\u0631\u0633': 3,
    '\u0646\u064a\u0633\u0627\u0646': 4,
    '\u0623\u0628\u0631\u064a\u0644': 4,
    '\u0625\u0628\u0631\u064a\u0644': 4,
    '\u0623\u064a\u0627\u0631': 5,
    '\u0645\u0627\u064a\u0648': 5,
    '\u062d\u0632\u064a\u0631\u0627\u0646': 6,
    '\u064a\u0648\u0646\u064a\u0648': 6,
    '\u062a\u0645\u0648\u0632': 7,
    '\u064a\u0648\u0644\u064a\u0648': 7,
    '\u0622\u0628': 8,
    '\u0623\u063a\u0633\u0637\u0633': 8,
    '\u0623\u064a\u0644\u0648\u0644': 9,
    '\u0633\u0628\u062a\u0645\u0628\u0631': 9,
    '\u062a\u0634\u0631\u064a\u0646 \u0627\u0644\u0623\u0648\u0644': 10,
    '\u0623\u0643\u062a\u0648\u0628\u0631': 10,
    '\u062a\u0634\u0631\u064a\u0646 \u0627\u0644\u062b\u0627\u0646\u064a': 11,
    '\u0646\u0648\u0641\u0645\u0628\u0631': 11,
    '\u0643\u0627\u0646\u0648\u0646 \u0627\u0644\u0623\u0648\u0644': 12,
    '\u062f\u064a\u0633\u0645\u0628\u0631': 12,
}

#: "20 <something> 2026" inside a dateline, once the tags are off.
_DAY_MONTH_YEAR_RE = re.compile(r'(\d{1,2})\s+(.+?)\s+(\d{4})')


def arabic_date_from_text(text: str):
    """
    A date out of Arabic dateline prose, or None.

        "نشر في: الأحد 20 أيلول / سبتمبر 2026. 05:05 مـساءً" -> 2026-09-20
        "الإثنين 21 أيلول 2026"                              -> 2026-09-21

    Month names are tried LONGEST FIRST, so "كانون الثاني" is never read as
    a bare "الثاني" and mistaken for "تشرين الثاني".
    """
    for match in _DAY_MONTH_YEAR_RE.finditer(' '.join((text or '').split())):
        day, middle, year = match.groups()
        for name in sorted(ARABIC_MONTHS, key=len, reverse=True):
            if name in middle:
                try:
                    return _dt.date(int(year), ARABIC_MONTHS[name], int(day))
                except ValueError:
                    return None
    return None

class NewsFeedMagazineScraper(BaseScraper):
    """
    Base class. A subclass sets the constants and, for a WordPress site, needs
    nothing else.

        CODE            registry code, used in log prefixes
        BASE_URL        site root
        FEED_MODE       'wp_rest' | 'sitemap'
        WP_QUERIES      list of query dicts for /wp-json/wp/v2/posts
        SITEMAP_URLS    list of sitemap URLs (FEED_MODE='sitemap')
        IMAGE_BASE_URL  host a bare image path in the sitemap belongs to, when
                        it is not the site itself (Zawya serves its photos
                        from media.zawya.com); empty means BASE_URL
        DEFAULT_COUNTRY CountryConfig code when none can be inferred
        IMPERSONATE     True when the site 403s a plain requests session
    """

    CODE            = 'NEWSFEED'
    BASE_URL        = ''
    FEED_MODE       = 'wp_rest'          # 'wp_rest' | 'sitemap'
    WP_QUERIES:     list = []
    SITEMAP_URLS:   list = []
    IMAGE_BASE_URL  = ''
    DEFAULT_COUNTRY = 'AE'
    IMPERSONATE     = False
    PER_PAGE        = 100
    #: 'either'  keep tender announcements AND design/supervision news
    #: 'tender'  announcements only (the original behaviour)
    #: 'design'  design/supervision news only
    RELEVANCE       = 'either'

    # Country inference from the text, so one regional magazine can feed several
    # countries instead of filing everything under its head office.
    _COUNTRY_HINTS = (
        ('AE', re.compile(r'\b(?:UAE|Dubai|Abu Dhabi|Sharjah|Ajman|Fujairah|'
                          r'Ras Al Khaimah|Umm Al Quwain|Emirates)\b', re.I)),
        ('SA', re.compile(r'\b(?:Saudi|KSA|Riyadh|Jeddah|Dammam|NEOM|Mecca|Medina)\b', re.I)),
        ('QA', re.compile(r'\b(?:Qatar|Doha|Ashghal)\b', re.I)),
        ('KW', re.compile(r'\b(?:Kuwait)\b', re.I)),
        ('OM', re.compile(r'\b(?:Oman|Muscat)\b', re.I)),
        ('BH', re.compile(r'\b(?:Bahrain|Manama)\b', re.I)),
        ('JO', re.compile(r'\b(?:Jordan|Amman)\b', re.I)),
        ('EG', re.compile(r'\b(?:Egypt|Cairo|Alexandria)\b', re.I)),
        ('IQ', re.compile(r'\b(?:Iraq|Baghdad|Basra)\b', re.I)),
        ('LB', re.compile(r'\b(?:Lebanon|Beirut)\b', re.I)),
        ('SY', re.compile(r'\b(?:Syria|Damascus)\b', re.I)),
        ('YE', re.compile(r'\b(?:Yemen|Aden)\b', re.I)),
        # ── North Africa. Missing these was a real bug: "Tunisia seeks
        #    consultants for high-speed rail study" was filed as UAE, which
        #    handed an African notice the permissive Middle East scope.
        ('TN', re.compile(r'\b(?:Tunisia|Tunis)\b', re.I)),
        ('MA', re.compile(r'\b(?:Morocco|Rabat|Casablanca|Marrakech)\b', re.I)),
        ('DZ', re.compile(r'\b(?:Algeria|Algiers)\b', re.I)),
        ('LY', re.compile(r'\b(?:Libya|Tripoli|Benghazi)\b', re.I)),
        # South Sudan first: 'South Sudan' contains 'Sudan'.
        ('SS', re.compile(r'\b(?:South Sudan|Juba)\b', re.I)),
        ('SD', re.compile(r'\b(?:Sudan|Khartoum|Omdurman)\b', re.I)),
        ('MR', re.compile(r'\b(?:Mauritania|Nouakchott)\b', re.I)),
        # ── Sub-Saharan Africa, the countries these titles actually name.
        ('KE', re.compile(r'\b(?:Kenya|Nairobi|Mombasa)\b', re.I)),
        # Nigeria before Benin: 'Benin City' is Nigerian, and a bare
        # word match on 'Benin' would hand it to the wrong country.
        ('NG', re.compile(r'\b(?:Nigeria|Lagos|Abuja|Benin City|Kano|'
                          r'Port Harcourt)\b', re.I)),
        ('ZA', re.compile(r'\b(?:South Africa|Johannesburg|Cape Town|Pretoria|Durban)\b', re.I)),
        ('ET', re.compile(r'\b(?:Ethiopia|Addis Ababa)\b', re.I)),
        ('GH', re.compile(r'\b(?:Ghana|Accra)\b', re.I)),
        ('TZ', re.compile(r'\b(?:Tanzania|Dar es Salaam|Dodoma)\b', re.I)),
        ('UG', re.compile(r'\b(?:Uganda|Kampala)\b', re.I)),
        ('SN', re.compile(r'\b(?:Senegal|S\u00e9n\u00e9gal|Dakar)\b', re.I)),
        ('CI', re.compile(r'\b(?:Ivory Coast|C\u00f4te d.Ivoire|Cote d.Ivoire|Abidjan)\b', re.I)),
        ('RW', re.compile(r'\b(?:Rwanda|Kigali)\b', re.I)),
        ('ZM', re.compile(r'\b(?:Zambia|Lusaka)\b', re.I)),
        ('ZW', re.compile(r'\b(?:Zimbabwe|Harare)\b', re.I)),
        ('MZ', re.compile(r'\b(?:Mozambique|Maputo)\b', re.I)),
        ('AO', re.compile(r'\b(?:Angola|Luanda)\b', re.I)),
        ('CD', re.compile(r'\b(?:DR Congo|DRC|Democratic Republic of the '
                          r'Congo|Kinshasa|Lubumbashi)\b', re.I)),
        # Congo-Brazzaville AFTER DR Congo, which contains the same word.
        ('CG', re.compile(r'\b(?:Congo|Brazzaville|Pointe-Noire)\b', re.I)),

        # ── the rest of the Arab League ─────────────────────────────────
        ('PS', re.compile(r'\b(?:Palestine|Palestinian|Gaza|Ramallah|'
                          r'West Bank)\b', re.I)),
        ('DJ', re.compile(r'\bDjibouti\b', re.I)),
        ('SO', re.compile(r'\b(?:Somalia|Somaliland|Mogadishu|Hargeisa)\b', re.I)),
        ('KM', re.compile(r'\b(?:Comoros|Moroni)\b', re.I)),

        # ── the rest of Africa. Named before the short-name countries
        #    they contain, longest first, so 'Equatorial Guinea' is never
        #    read as 'Guinea'.
        ('GQ', re.compile(r'\b(?:Equatorial Guinea|Malabo)\b', re.I)),
        ('GW', re.compile(r'\b(?:Guinea-Bissau|Guinea Bissau|Bissau)\b', re.I)),
        ('PG', re.compile(r'\b(?:Papua New Guinea|Port Moresby)\b', re.I)),
        ('GN', re.compile(r'\b(?:Guinea|Conakry)\b', re.I)),

        ('BF', re.compile(r'\b(?:Burkina Faso|Ouagadougou|Bobo-Dioulasso)\b', re.I)),
        ('BI', re.compile(r'\b(?:Burundi|Bujumbura|Gitega)\b', re.I)),
        ('BJ', re.compile(r'\b(?:Benin|Cotonou|Porto-Novo)\b', re.I)),
        ('BW', re.compile(r'\b(?:Botswana|Gaborone)\b', re.I)),
        ('CF', re.compile(r'\b(?:Central African Republic|Bangui)\b', re.I)),
        ('CM', re.compile(r'\b(?:Cameroon|Cameroun|Yaounde|Yaound\u00e9|Douala)\b', re.I)),
        ('CV', re.compile(r'\b(?:Cabo Verde|Cape Verde|Praia)\b', re.I)),
        ('ER', re.compile(r'\b(?:Eritrea|Asmara)\b', re.I)),
        ('GA', re.compile(r'\b(?:Gabon|Libreville)\b', re.I)),
        ('GM', re.compile(r'\b(?:Gambia|Banjul)\b', re.I)),
        ('LR', re.compile(r'\b(?:Liberia|Monrovia)\b', re.I)),
        ('LS', re.compile(r'\b(?:Lesotho|Maseru)\b', re.I)),
        ('MG', re.compile(r'\b(?:Madagascar|Antananarivo)\b', re.I)),
        ('ML', re.compile(r'\b(?:Mali|Bamako)\b', re.I)),
        ('MU', re.compile(r'\b(?:Mauritius|Port Louis)\b', re.I)),
        ('MW', re.compile(r'\b(?:Malawi|Lilongwe|Blantyre)\b', re.I)),
        ('NA', re.compile(r'\b(?:Namibia|Windhoek)\b', re.I)),
        ('NE', re.compile(r'\b(?:Niger|Niamey)\b', re.I)),
        ('SC', re.compile(r'\b(?:Seychelles|Victoria, Seychelles)\b', re.I)),
        ('SL', re.compile(r'\b(?:Sierra Leone|Freetown)\b', re.I)),
        ('ST', re.compile(r'\b(?:S\u00e3o Tom\u00e9[^,]{0,20}|Sao Tome[^,]{0,20})\b', re.I)),
        ('SZ', re.compile(r'\b(?:Eswatini|Swaziland|Mbabane)\b', re.I)),
        ('TD', re.compile(r'\b(?:Chad|Tchad|N.Djamena)\b', re.I)),
        ('TG', re.compile(r'\b(?:Togo|Lom\u00e9|Lome)\b', re.I)),
    )

    @property
    def headers(self) -> dict:
        return {
            'User-Agent': (
                getattr(settings, 'MAGAZINE_USER_AGENT', '') or
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/124.0.0.0 Safari/537.36'
            ),
            'Accept':          'application/json,text/html,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9,ar;q=0.8',
            'Referer':         self.BASE_URL,
        }

    # ── entry point ──────────────────────────────────────────────────────────
    def _fetchers(self) -> dict:
        """
        FEED_MODE -> the method that fetches it.

        A method rather than a literal in scrape() so a subclass can add its
        own mode: Al-Rai is not WordPress and has no sitemap worth reading, so
        it registers `alrai_api` here instead of the base growing a third
        site-specific fetcher it would have to know about.
        """
        return {'wp_rest': self._fetch_wp, 'sitemap': self._fetch_sitemap}

    def scrape(self) -> list[TenderData]:
        source = getattr(self, 'source', None)
        if source is None:
            print(f'  X  [MAG:{self.CODE}] self.source not set — skipping')
            logger.error(f'[MAG:{self.CODE}] scrape() called without a source')
            return []

        rel_src, cat_src = self._reload_keywords_from_db()
        rel_count = len(self._relevance_keywords)
        cat_count = sum(len(v) for v in self._category_keywords.values())

        print(f'\n{"_" * 62}')
        print(f'  SOURCE  : {source.name}')
        print(f'  SCRAPER : {type(self).__name__}')
        print(f'  MODE    : news feed ({self.FEED_MODE}) + tender/design-supervision filter')
        print(f'  URL     : {self.BASE_URL}')
        print(f'  FILTER  : tender announcement OR design/supervision news')
        print(f'  Keywords: {rel_count} relevance [{rel_src}]'
              f'  |  {cat_count} category [{cat_src}]')
        print(f'{"_" * 62}')

        try:
            fetch = self._fetchers().get(self.FEED_MODE)
            if fetch is None:
                raise ValueError(f'unknown FEED_MODE {self.FEED_MODE!r}')
            raw_items = fetch()
        except Exception as exc:
            print(f'  X  [MAG:{self.CODE}] Error: {exc}')
            logger.error(f'[MAG:{self.CODE}] fetch error: {exc}')
            return []

        print(f'       > {len(raw_items)} feed item(s) fetched')

        results = self.build_items(raw_items)
        kept, dropped = len(results), len(raw_items) - len(results)
        print(f'       > {dropped} rejected: neither an announcement nor design/supervision')
        print(f'  TOTAL   : {kept} tender announcement(s)')
        logger.info(f'[MAG:{self.CODE}] complete — fetched={len(raw_items)} '
                    f'kept={kept} rejected={dropped}')
        return results

    # ── parsing (no network, so tests run offline) ───────────────────────────
    def build_items(self, raw_items: list) -> list[TenderData]:
        out = []
        for raw in raw_items:
            try:
                td = self.build_one(raw)
            except Exception as exc:
                logger.warning(f'[MAG:{self.CODE}] item skipped: {exc}')
                continue
            if td is not None:
                out.append(td)
        return out

    def build_one(self, raw: dict) -> Optional[TenderData]:
        title    = strip_html(raw.get('title', ''))
        summary  = strip_html(raw.get('summary', ''))
        body     = strip_html(raw.get('body', ''))
        url      = raw.get('url', '') or self.BASE_URL
        pub      = raw.get('published')

        if not title:
            return None

        # The signal must be in the HEADLINE or STANDFIRST. Requiring it there
        # is what separates an announcement from an article that merely mentions
        # a tender: on the live CBNME feed, matching against the body let three
        # unrelated stories through (a truck-driver competition among them),
        # because one sentence deep inside them used the word.
        headline = f'{title} {summary}'
        kept_for = why_keep(headline, self.RELEVANCE)
        if not kept_for:
            return None

        # The body IS read for the detail — a closing date and a reference
        # number legitimately appear in the article, not the headline.
        detail   = f'{headline} {body}'
        deadline = extract_deadline(detail, pub)
        ref      = extract_reference(detail)
        today    = date.today()
        status   = 'OPEN' if deadline is None or deadline >= today else 'CLOSED'

        td = TenderData(
            title               = title[:500],
            title_ar            = raw.get('title_ar', '')[:500],
            source_url          = url,
            external_id         = '',      # runner hashes one
            reference_no        = ref[:120],
            entity              = self._entity(detail)[:200],
            entity_ar           = '',
            # Prefixed so an operator can tell an invitation to bid from a
            # design story without opening the article.
            description         = (f'[{kept_for}] ' + (summary or body))[:4000],
            description_ar      = '',
            category            = self._safe_category(detail),
            status              = status,
            publication_date    = pub,
            submission_deadline = deadline,
            country_code        = self._country(detail),
        )
        # The publisher's featured image, hotlinked. Empty when the feed had
        # none — or when the filename says it is a stock frame, which is a
        # picture of a city, not of the project. The page shows a designed
        # block for both.
        img = raw.get('image') or ''
        stock = looks_like_stock(img)
        td.image_url = '' if stock else img[:2000]
        # The hero rendition of the SAME image, so one stock verdict covers both.
        td.image_url_large = '' if stock else (raw.get('image_large') or '')[:2000]
        # The article as the feed carried it, for the summary the runner asks
        # AJi AI to write. A feed that gives only a headline leaves this
        # short, and the summariser fetches the page instead.
        td.article_text = (body or summary)[:6000]
        return td

    # ── fetchers ─────────────────────────────────────────────────────────────
    def _client_get(self, url, params=None, timeout=None):
        """
        GET with the session, falling back to curl_cffi impersonation.

        Some publishers 403 a plain requests session outright (Arabian Business
        does). Both libraries are already in
        requirements.txt, so this adds no dependency — it is the same ladder the
        existing kuwait_capt and era_ethiopia scrapers use.

        """
        timeout = timeout or self.timeout
        if not self.IMPERSONATE:
            resp = self.session.get(url, params=params, headers=self.headers,
                                    timeout=timeout)
            if resp.status_code != 403:
                resp.raise_for_status()
                return resp.text
            logger.info(f'[MAG:{self.CODE}] 403 — retrying with impersonation')

        from curl_cffi import requests as cr
        r = cr.get(url, params=params, impersonate='chrome', timeout=timeout)
        if r.status_code != 200:
            raise RuntimeError(f'HTTP {r.status_code} for {url}')
        return r.text

    def _fetch_wp(self) -> list[dict]:
        """WordPress REST: /wp-json/wp/v2/posts, one pass per configured query."""
        endpoint = f'{self.BASE_URL}/wp-json/wp/v2/posts'
        seen, items = set(), []
        for query in self.WP_QUERIES or [{}]:
            # _embed makes WordPress inline the featured image's record, so
            # the photo URL arrives in this same response. Without it the
            # post carries only a media ID and each photo costs a request.
            # Narrowed to the image: a bare _embed also pulls author, terms
            # and comments, none of which is used.
            params = {'per_page': self.PER_PAGE, 'orderby': 'date',
                      '_embed': 'wp:featuredmedia', **query}
            # Layer one of the freshness window: let the PUBLISHER filter.
            # WordPress understands `after`, so the old posts never travel —
            # Construction Week returns 24,425 posts unfiltered and a handful
            # with a cutoff applied. The source's own query is preserved; this
            # only adds a lower bound on the date.
            cut = freshness.cutoff_date()
            if cut is not None:
                params.setdefault('after', f'{cut.isoformat()}T00:00:00')
            try:
                # Resolving 100 media records is slow on some hosts — CBNME
                # takes 32-36 s against a 45 s default, and one slow day
                # silently emptied its feed. The sweep runs in the background,
                # so a longer ceiling costs nothing.
                text = self._client_get(endpoint, params=params,
                                        timeout=max(self.timeout, WP_EMBED_TIMEOUT))
                posts = json.loads(text)
            except Exception as exc:
                logger.warning(f'[MAG:{self.CODE}] query {query} failed: {exc}')
                continue
            if not isinstance(posts, list):
                continue
            for p in posts:
                link = p.get('link') or ''
                if link in seen:
                    continue
                seen.add(link)
                items.append(self._from_wp_post(p))
        return items

    def _from_wp_post(self, p: dict) -> dict:
        return {
            'title':     (p.get('title') or {}).get('rendered', ''),
            'summary':   (p.get('excerpt') or {}).get('rendered', ''),
            'body':      (p.get('content') or {}).get('rendered', ''),
            'url':       p.get('link') or '',
            'published': self._iso_date(p.get('date') or p.get('date_gmt') or ''),
            'image':     wp_featured_image(p),
            'image_large': wp_featured_image(p, _WP_HERO_SIZES),
        }

    def _fetch_sitemap(self) -> list[dict]:
        """Google-news sitemap: one <url> per article, with title and date."""
        import xml.etree.ElementTree as ET

        ns = {'s': 'http://www.sitemaps.org/schemas/sitemap/0.9',
              'n': 'http://www.google.com/schemas/sitemap-news/0.9',
              'i': 'http://www.google.com/schemas/sitemap-image/1.1'}
        items = []
        for sm in self.SITEMAP_URLS:
            try:
                text = self._client_get(sm)
                root = ET.fromstring(text.encode('utf-8'))
            except Exception as exc:
                logger.warning(f'[MAG:{self.CODE}] sitemap {sm} failed: {exc}')
                continue
            for url_el in root.findall('s:url', ns):
                loc = (url_el.findtext('s:loc', '', ns) or '').strip()
                news = url_el.find('n:news', ns)
                title = pub = ''
                if news is not None:
                    title = (news.findtext('n:title', '', ns) or '').strip()
                    pub = (news.findtext('n:publication_date', '', ns) or '').strip()
                if not title:
                    # Fall back to the slug so the filter still has words to read.
                    title = loc.rstrip('/').rsplit('/', 1)[-1].replace('-', ' ')
                # Zawya writes the image as a bare path (/prod/images/...).
                # It has to be resolved against the host that actually serves
                # the pictures - for Zawya that is media.zawya.com; the same
                # path on www.zawya.com is a 404, which is why every Zawya
                # card used to fall back to its grey placeholder.
                from urllib.parse import urljoin
                img = url_el.find('i:image/i:loc', ns)
                img_url = (img.text or '').strip() if img is not None else ''
                if img_url and not img_url.lower().startswith(('http://', 'https://')):
                    img_url = urljoin(self.IMAGE_BASE_URL or self.BASE_URL, img_url)
                small, large = self._sitemap_image_sizes(img_url)
                items.append({
                    'title':     title,
                    'summary':   '',
                    'body':      '',
                    'url':       loc,
                    'published': self._iso_date(pub),
                    'image':     small,
                    'image_large': large,
                })
        return items

    def _sitemap_image_sizes(self, img_url: str) -> tuple[str, str]:
        """(card image, hero image) for a sitemap photo. A sitemap names one
        file; a site that also serves a lighter rendition under a predictable
        path can override this so the cards do not pull multi-megabyte
        originals."""
        return img_url, img_url

    # ── helpers ──────────────────────────────────────────────────────────────
    @staticmethod
    def _iso_date(raw: str) -> Optional[date]:
        raw = (raw or '').strip()
        if not raw:
            return None
        cleaned = raw.replace('Z', '+00:00')
        try:
            return datetime.fromisoformat(cleaned).date()
        except ValueError:
            pass
        for fmt in ('%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
            try:
                return datetime.strptime(raw[:19], fmt).date()
            except ValueError:
                continue
        return None

    def _country(self, text: str) -> str:
        for code, pattern in self._COUNTRY_HINTS:
            if pattern.search(text or ''):
                return code
        return self.DEFAULT_COUNTRY

    @staticmethod
    def _entity(text: str) -> str:
        """
        Best-effort issuing body: the subject of the announcing verb, e.g.
        "RTA invites bids …" → "RTA". Left blank when nothing is confident —
        a wrong entity is worse than none, and the AI does not need it.
        """
        m = re.search(
            r'([A-Z][A-Za-z&.\'\- ]{2,60}?)\s+'
            r'(?:invit\w*|issu\w*|launch\w*|float\w*|seek\w*|calls?\s+for)\b',
            text or '')
        if not m:
            return ''
        candidate = m.group(1).strip(' .,-')
        return candidate if 2 <= len(candidate) <= 60 else ''

    def _safe_category(self, text: str) -> str:
        """
        Provisional category, or 'OTHER' when the keyword tables are not loaded.

        `detect_category` needs `_reload_keywords_from_db()` to have run, which
        happens in `scrape()`. Parsing is deliberately callable on its own so
        tests can run against a saved fixture with no database.
        """
        try:
            return self.detect_category(text)
        except AttributeError:
            return 'OTHER'
