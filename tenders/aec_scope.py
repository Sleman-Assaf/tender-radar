# tenders/aec_scope.py
"""
Which tenders count as AEC *for display*.

Nothing here classifies anything. `Tender.is_aec` is set by the shared AJiAI
service and is never written by this module — what follows only decides which
rows a listing page shows.

The business rule
-----------------
AJi sells الاشراف والتصميم: supervision, design and engineering consultancy.
Notices arrive in English, Arabic and French — the development-bank registers
publish francophone-Africa work in French — so all three are matched.
It does not build. So a tender is relevant only when the design / supervision /
consultancy scope is something AJi could actually be awarded ON ITS OWN.

That makes it a two-part test, and the second part is the important one:

1. POSITIVE — the title describes design, supervision, consultancy,
   engineering services or the studies that accompany them.

2. VETO — the title bundles that scope INTO a construction contract, so it
   cannot be won separately. "Design and construction of a clinic" is a
   design-build (turnkey) package: one bidder does both, and a consultancy
   cannot bid the design half. Same for EPC, design-and-build, and
   "design, supply and installation".

   The veto is what keeps this from drifting into construction work. Without
   it, every turnkey package in the region would appear on the page.

A distinction worth stating because it looks like a contradiction:
"construction supervision" is NOT vetoed. Supervising somebody else's
construction is exactly AJi's service — the word "construction" there names
what is being supervised, not what AJi would build.

Pure construction, supply, execution (تنفيذ), building (انشاء) and maintenance
(صيانة) never match at all: they carry no positive term.

Reversibility
-------------
`Tender.is_aec` keeps whatever the classification service decided, so the audit
trail is intact and this cannot corrupt data. Set
AEC_INCLUDE_DESIGN_SUPERVISION=0 and the pages revert to showing exactly what
the service flagged.
"""
from django.conf import settings
from django.db.models import Q

# ── 1. POSITIVE: the service AJi sells ────────────────────────────────────────
# Each alternative requires the term to describe a SCOPE OF WORK, not merely to
# appear. Matching the bare word "design" pulled in the headline "Emaar to
# relaunch Dubai Creek Tower with new design in three months".
DESIGN_SUPERVISION_EN = (
    # supervision of / for something, supervisory services, construction
    # supervision (i.e. supervising a contractor — AJi's core service)
    # 'agreement|contract|role|deal|mandate' matter as much as 'of/for':
    # AJi's own "Signs Supervision Agreement For Miraf District" was
    # invisible without them, because a commission is announced by the
    # instrument it is awarded as.
    r'supervis\w*\s+(?:of|for|services|works|consultanc\w*|agreement|contract|role|deal|mandate)|'
    r'(?:construction|site|engineering|project)\s+supervis\w*|'
    # design OF / FOR something, or design services — but NOT "design and
    # construction of", which the veto below removes
    r'design\s+(?:of|for|services|works)|'
    r'(?:detailed|architectural|structural|concept|schematic|preliminary)\s+design|'
    # Masterplanning is design work and was missing entirely.
    r'master\s?plan\w*|'
    # consultancy and engineering services, and the studies that go with them
    r'consultanc\w*\s+services|consulting\s+services|'
    r'engineering\s+services|'
    r'feasibility\s+stud\w+|'
    r'(?:preparation\s+of|prepare)\s+(?:the\s+)?(?:studies|designs|documents)|'
    r'(?:technical|engineering)\s+stud\w+'
)

DESIGN_SUPERVISION_AR = (
    r'إشراف|اشراف|'                 # supervision (with and without hamza)
    r'تصميم|تصاميم|'                # design / designs
    r'استشار|'                      # consultancy / consultant
    r'دراسات|دراسة'                 # studies / study
)

# ── 2. VETO: the scope is welded to a construction contract ───────────────────
# A design-build / turnkey / EPC package cannot be won as a design commission,
# so it is not AJi's work even though the word "design" is in the title.
BUNDLED_WITH_CONSTRUCTION_EN = (
    # "design and construction", "design & build", "design, supply and install"
    r'design\s*(?:,|&|and)\s*(?:build|construct\w*|supply|install\w*|erect\w*)|'
    r'design\s*,\s*\w+\s*(?:,|and|&)\s*(?:construct\w*|install\w*|build)|'
    # the standard names for the same thing
    r'design[\s\-/]*build|'
    r'\bEPC\b|\bE\.P\.C\b|'
    r'turn[\s\-]?key|'
    r'engineering\s*,\s*procurement\s+and\s+construction|'
    # "supply, installation and construction" style packages that also mention
    # design somewhere
    r'(?:supply|install\w*)\s*(?:,|and|&)\s*(?:construct\w*|erect\w*)'
)

BUNDLED_WITH_CONSTRUCTION_AR = (
    r'تصميم\s*و\s*تنفيذ|'           # design and execution
    r'تصميم\s*و\s*إنشاء|تصميم\s*و\s*انشاء|'   # design and construction
    r'تصميم\s*و\s*بناء|'            # design and building
    r'تصميم\s*وتوريد|'              # design and supply
    r'تسليم\s*مفتاح'                # turnkey
)


# French, because the development-bank sources publish francophone-Africa
# notices in it — AfDB as "AMI" (Avis a Manifestation d'Interet) and IsDB as
# "Selection d'un cabinet ...". In French construction procurement the words for
# AJi's work are contrôle/surveillance (supervision), études (studies),
# conception (design) and maîtrise d'oeuvre (design + supervision management).
# Accents are optional in the pattern because the registers are inconsistent
# about them.
DESIGN_SUPERVISION_FR = (
    r'surveillance\s+(?:des|de|du|technique)|'
    r'contr[oô]le\s+(?:et\s+(?:la\s+)?surveillance|des\s+travaux|technique)|'
    r'ma[iî]trise\s+d[\'’]?\s*(?:oe|œ)uvre|'
    r'(?:[eé]tudes?|conception)\s+(?:de|du|des|technique|d[\'’]?)|'
    r'[eé]tudes?\s+(?:de\s+faisabilit|d[\'’]?avant-projet|techniques)|'
    r'consultant|cabinet\s+(?:d[\'’]?[eé]tudes?|de\s+conseil)|'
    r'bureau\s+d[\'’]?[eé]tudes?|'
    r'assistance\s+technique|ing[eé]nierie'
)

# The same veto in French: a design-build package cannot be won as a design
# commission. "conception-réalisation" is the French term for design-build.
BUNDLED_WITH_CONSTRUCTION_FR = (
    r'conception[\s\-]*r[eé]alisation|'
    r'conception\s+et\s+(?:r[eé]alisation|construction|ex[eé]cution)|'
    r'cl[eé]s?\s+en\s+main'
)


# ── 3. AFRICA: design and consultancy, but not supervision ────────────────────
# Design and desk consultancy travel; site supervision does not, and AJi has no
# African site presence. So Africa gets its own, narrower test.
#
# Three ingredients are needed, because the deciding case is a notice worded
# "Consultancy Services FOR Supervision of ...": the word consultancy is present
# but the work is supervision, so it has to lose — unless design is in scope too.
DESIGN_AFRICA_EN = (
    r'design\s+(?:of|for|services|works|review)|'
    r'(?:detailed|architectural|structural|concept|schematic|preliminary)\s+design|'
    r'(?:preparation\s+of|prepare)\s+(?:the\s+)?designs?|'
    r'designs?\s*(?:,|and|&)|'          # "preparation of designs, specifications"
    r'\bre-?design\b|'
    # Africa is design-only, so masterplanning has to count here or a
    # masterplan commission in Egypt or Kenya is silently excluded.
    r'master\s?plan\w*'
)

# In French procurement the design phase is "conception" and "études".
DESIGN_AFRICA_FR = (
    r'conception|'
    r'[eé]tudes?\s+(?:techniques?|d[\'’]?ex[eé]cution|architectural|de\s+conception)|'
    r'[eé]tudes?\s*,|'
    r'\bAPS\b|\bAPD\b'               # avant-projet sommaire / détaillé
)

DESIGN_AFRICA_AR = r'تصميم|تصاميم'

#: Desk consultancy that does not imply standing on a site.
CONSULTANCY_AFRICA = (
    r'consultanc\w*|consultant|consulting|'
    r'advisory|technical\s+assistance|'
    r'feasibility|\bstud(?:y|ies)\b|review\s+of|assessment|audit|'
    # 'bureau d' and 'cabinet d' alone match an office NAME: "Bureau de
    # Coordination des Projets Filets Sociaux" is the Ivorian social-
    # safety-net agency, and it put a social-protection programme on the
    # News page. The intent was the French for a design practice.
    r'(?:cabinet|bureau)\s+d.{0,2}\s*[eé]tudes?|'
    r'assistance\s+technique|ing[eé]nierie|'
    r'استشار|دراسات|دراسة'
)

#: Supervision — the thing AJi cannot staff in Africa. Includes the French
#: contrôle/surveillance/suivi vocabulary.
SUPERVISION_AFRICA = (
    r'supervis\w*|'
    r'contr[oô]le\s+(?:et\s+(?:la\s+)?surveillance|des\s+travaux)|'
    r'surveillance\s+(?:des|de|du)|'
    r'suivi\s+(?:et\s+)?(?:contr[oô]le|des\s+travaux)|'
    r'ma[iî]trise\s+d[\'’]?\s*(?:oe|œ)uvre|'
    r'resident\s+engineer|site\s+engineer|'
    r'إشراف|اشراف'
)

#: The 54 African countries. Explicit rather than derived so the list is
#: reviewable, and overridable through the AEC_AFRICA_COUNTRIES setting — if AJi
#: opens an office in, say, Cairo, drop EG and Egyptian supervision becomes
#: visible again without touching this file.
AFRICA_COUNTRY_CODES = frozenset({
    'DZ', 'AO', 'BJ', 'BW', 'BF', 'BI', 'CV', 'CM', 'CF', 'TD', 'KM', 'CG',
    'CD', 'CI', 'DJ', 'EG', 'GQ', 'ER', 'SZ', 'ET', 'GA', 'GM', 'GH', 'GN',
    'GW', 'KE', 'LS', 'LR', 'LY', 'MG', 'MW', 'ML', 'MR', 'MU', 'MA', 'MZ',
    'NA', 'NE', 'NG', 'RW', 'ST', 'SN', 'SC', 'SL', 'SO', 'ZA', 'SS', 'SD',
    'TZ', 'TG', 'TN', 'UG', 'ZM', 'ZW',
})


def africa_codes() -> frozenset:
    override = getattr(settings, 'AEC_AFRICA_COUNTRIES', None)
    if not override:
        return AFRICA_COUNTRY_CODES
    return frozenset(c.strip().upper() for c in override if c and c.strip())


def _africa_design_q() -> Q:
    return (Q(title__iregex=DESIGN_AFRICA_EN) |
            Q(title__iregex=DESIGN_AFRICA_FR) |
            Q(title_ar__iregex=DESIGN_AFRICA_AR))


def _africa_consultancy_q() -> Q:
    return (Q(title__iregex=CONSULTANCY_AFRICA) |
            Q(title_ar__iregex=CONSULTANCY_AFRICA))


def _africa_supervision_q() -> Q:
    return (Q(title__iregex=SUPERVISION_AFRICA) |
            Q(title_ar__iregex=SUPERVISION_AFRICA))


def africa_scope_q() -> Q:
    """
    African work AJi can actually deliver: design or desk consultancy, with
    supervision-only commissions removed.

    A notice that is supervision AND has no design scope is dropped even when it
    calls itself consultancy — "Consultancy Services for Supervision of ..." is
    still a site job.
    """
    design = _africa_design_q()
    return (design | _africa_consultancy_q()) & ~(_africa_supervision_q() & ~design)


#: A service that can be awarded on its own, even when the thing it covers is
#: a design-build contract. Deliberately narrow: it must name the SERVICE, not
#: merely mention consultancy somewhere.
SEPARABLE_SERVICE_EN = (
    r'(?:professional\s+)?consultanc\w*\s+services|'
    r'consulting\s+services|'
    # 'of|for' belongs here too: supervising something IS the separable
    # service. Without it, "Supervision for the design and build of X"
    # was vetoed as a bundle — but the scope being bought is the
    # supervision, and the design-build is merely what it covers.
    r'supervis\w*\s+(?:of|for|services|consultanc\w*|agreement|contract|role)|'
    r'(?:post|pre)[\s-]?contract\s+\w*\s*(?:services|consultanc\w*)|'
    r'project\s+management\s+services|'
    r'engineering\s+consultanc\w*'
)

SEPARABLE_SERVICE_AR = (
    r'\u062e\u062f\u0645\u0627\u062a\s*(?:\u0627\u0644)?'
    r'(?:\u0627\u0633\u062a\u0634\u0627\u0631|\u0625\u0634\u0631\u0627\u0641|\u0627\u0634\u0631\u0627\u0641)'
)


def design_supervision_q() -> Q:
    """
    Rows whose title describes design / supervision / consultancy work that
    could be awarded on its own.

    Positive match AND NOT bundled into a construction contract.
    """
    # French shares the `title` column with English — the registers put a
    # French notice's text straight in there — so both patterns test `title`.
    positive = (Q(title__iregex=DESIGN_SUPERVISION_EN) |
                Q(title__iregex=DESIGN_SUPERVISION_FR) |
                Q(title_ar__iregex=DESIGN_SUPERVISION_AR))
    bundled = (Q(title__iregex=BUNDLED_WITH_CONSTRUCTION_EN) |
               Q(title__iregex=BUNDLED_WITH_CONSTRUCTION_FR) |
               Q(title_ar__iregex=BUNDLED_WITH_CONSTRUCTION_AR))

    # A design-build bundle is excluded — unless the title ALSO names a
    # separable consultancy or supervision scope, in which case that scope is
    # what is being bought and the design-build is merely what it covers.
    #
    # The case that forced this: "Post-contract professional consultancy
    # services for the design and build of X". The veto fired on "design and
    # build" and hid a pure consultancy commission — exactly the work AJi
    # sells. The business's own rule was that a separable design or
    # consultancy scope counts; here it is separable and named first.
    separable = (Q(title__iregex=SEPARABLE_SERVICE_EN) |
                 Q(title_ar__iregex=SEPARABLE_SERVICE_AR))
    return positive & ~(bundled & ~separable)


def include_design_supervision() -> bool:
    return bool(getattr(settings, 'AEC_INCLUDE_DESIGN_SUPERVISION', True))


def aec_display_q() -> Q:
    """
    The filter a listing page should use in place of `Q(is_aec=True)`.

    Geography changes what counts, because what AJi can deliver changes:

      * MIDDLE EAST — design, supervision or engineering consultancy, plus
        anything the classification service flagged as AEC.
      * AFRICA — design and desk consultancy, but NOT supervision: it needs
        people on site and AJi has no African presence. The service's own
        is_aec flag is deliberately not honoured as a blanket pass there, since
        it knows nothing about the presence constraint and would let African
        supervision back in through the side door.

    In both regions a scope welded into a construction contract is excluded.
    With AEC_INCLUDE_DESIGN_SUPERVISION=0 the whole widening is off and the
    pages show exactly what the service flagged, everywhere.
    """
    if not include_design_supervision():
        return Q(is_aec=True)

    in_africa = Q(country__code__in=africa_codes())
    bundled = (Q(title__iregex=BUNDLED_WITH_CONSTRUCTION_EN) |
               Q(title__iregex=BUNDLED_WITH_CONSTRUCTION_FR) |
               Q(title_ar__iregex=BUNDLED_WITH_CONSTRUCTION_AR))

    africa_ok = in_africa & africa_scope_q() & ~bundled
    elsewhere_ok = ~in_africa & (Q(is_aec=True) | design_supervision_q())
    return africa_ok | elsewhere_ok


def non_aec_display_q() -> Q:
    """
    The complement, for the admin audit pages.

    Must be the exact inverse of `aec_display_q()`, or a tender caught by the
    widened rule would appear on both the user page and the "non-AEC" page and
    the two counts would stop adding up.
    """
    return ~aec_display_q()
