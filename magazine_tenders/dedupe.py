# magazine_tenders/dedupe.py
"""
The same story, twice.

Five Jordanian sources now cover one country, and a wire agency plus three
dailies report the same events. On 21 September the News page carried the
Ajloun hotel from both Al-Rai and Al-Ghad, one row above the other.

It is not a Jordan problem. Measured over all 282 rows that day, nine pairs
scored 0.70 or better on normalised title overlap and every one was a real
duplicate — Gulf Construction repeating a maintenance notice, Zawya running
the same ZAKH story under two headlines, Construction Week and Zawya both
carrying the Nakheel award.

HOW IT WORKS
    A title is reduced to a set of significant words: punctuation and digits
    off, the attached ال/و/ب/ل dropped so معان and بمعان are one word, stop
    words and two-letter fragments discarded. Two titles are the same story
    when their sets overlap by THRESHOLD or more (Jaccard) and they were
    published within WINDOW_DAYS of each other.

WHY 0.70
    It is where the live data separates. Above it, every pair was a genuine
    duplicate. The next two candidates below are 0.64 and 0.62 — Meraas and
    Nakheel announcing DIFFERENT contracts in near-identical house style —
    and merging those would lose a real award. The tests pin both sides.

WHAT IT DELIBERATELY DOES NOT CATCH
    Al-Rai's two Ma'an gas pieces, «رئيس الوزراء يطلق مشروع إيصال الغاز
    الطبيعي» and «أبو تايه: تزويد الروضة الصناعية بالغاز الطبيعي», score
    about 0.24. Same event, genuinely different articles: a ground-breaking
    and an executive's comment on it. Catching that pair needs a threshold
    low enough to start merging unrelated stories, which costs more than it
    saves.

NOTHING IS DELETED
    The later copy is marked `is_mirror` and pointed at the first with
    `mirror_of` — the fields the project already uses to mean "this row
    duplicates that one". It stays in the database, stays in admin (where it
    renders with a 🔁), and stays reachable by search. It simply does not
    appear on the page twice.
"""
from __future__ import annotations

import logging
import re
from datetime import timedelta

from django.db.models import Q

logger = logging.getLogger('tenders')

#: Overlap at or above which two headlines are one story. See WHY 0.70.
THRESHOLD = 0.70

#: How far apart two copies may be published and still be the same story.
#: A headline repeated a month later is a new announcement — a scheme
#: re-tendered, a second phase opening — not a duplicate.
WINDOW_DAYS = 7

#: How many recent rows to compare against. Generous enough to cover a week
#: of every source at once, bounded so a sweep cannot turn quadratic.
COMPARE_LIMIT = 400

#: Tashkeel and the tatweel — decoration, not spelling. Al-Rai writes يخفض
#: where Ad-Dustour writes يُخفّض, and untouched those are two tokens: the
#: two papers' reports of the same Ma'an gas story scored 0.545 on it.
#: U+064B–U+0652 are the vowel marks and the shadda, U+0670 the superscript
#: alef, U+0640 the kashida used to stretch a word in typesetting.
_ARABIC_MARKS_RE = re.compile('[ً-ْٰـ]')

#: Arabic attaches these to the front of a word with no boundary, so معان
#: and بمعان are the same place and must reduce to the same token.
_AR_PREFIX_RE = re.compile(r'^(?:ال|و|ب|ل|ف|ك)+')

#: Words that carry no identity. Arabic particles and the English articles
#: and prepositions that show up in every trade headline.
_STOP = {
    'في', 'من', 'على', 'إلى', 'عن', 'مع', 'هذا', 'هذه', 'التي', 'الذي',
    'أن', 'إن', 'بعد', 'قبل', 'خلال', 'حول', 'بين', 'كما', 'ما',
    'the', 'and', 'for', 'with', 'from', 'that', 'this', 'its', 'has',
}


def normalise(title: str) -> set:
    """
    A headline reduced to the words that identify it.

    Punctuation and digits go — one paper writes "4 نجوم" and the other
    "أربع نجوم", so the numeral is noise either way. Fragments of two
    characters or fewer go with them: what is left of a word after its
    prefix is stripped is rarely worth comparing.
    """
    text = _ARABIC_MARKS_RE.sub('', title or '')
    text = re.sub(r'[^\w\s]', ' ', text)
    out = set()
    for word in text.split():
        word = _AR_PREFIX_RE.sub('', word).strip().lower()
        if word.isdigit():
            # A bare single digit is noise — one paper writes "فندق 4 نجوم"
            # and the other "فندق أربع نجوم". A longer number is not: it is
            # how Ashghal tells Area 11 from Area 12.
            if len(word) > 1:
                out.add(word)
            continue
        if len(word) > 2 and word not in _STOP:
            out.add(word)
    return out


def similarity(a: str, b: str) -> float:
    """Jaccard overlap of two headlines, 0.0 when either says nothing."""
    ta, tb = normalise(a), normalise(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


#: Word stems that name the ROLE a notice is procuring, mapped to the role.
#: A works package and its supervision package share nearly every word —
#: "Foul Sewer GAP tunnel" against "Supervision for Foul Sewer GAP tunnel"
#: scores far above the threshold — but they are two separate procurements,
#: and the consultancy one is the sort AJi bids for. The first backfill
#: merged eleven such pairs on the Ashghal pipeline.
_ROLE_STEMS = (
    ('supervis',   'supervision'),
    ('psa',        'supervision'),
    ('إشراف',      'supervision'),
    ('consult',    'consultancy'),
    ('استشار',     'consultancy'),
    ('design',     'design'),
    ('تصميم',      'design'),
    ('تصاميم',     'design'),
    ('دراسات',     'studies'),
)


def roles(title: str) -> set:
    """
    Which professional roles a headline names: supervision, design, studies.

    Empty for a plain works or news item, which is the common case and means
    the role rule simply does not apply.
    """
    tokens = normalise(title)
    found = set()
    for token in tokens:
        for stem, role in _ROLE_STEMS:
            if token.startswith(stem):
                found.add(role)
    return found


def numbers(title: str) -> set:
    """
    The bare numbers a headline carries — an area, a phase, a package.

    Money keeps its currency through normalisation ("AED590", "218mln"), so
    it stays a word and never lands here. What does land here is exactly
    what distinguishes "Area 11" from "Area 12".
    """
    return {t for t in normalise(title) if t.isdigit()}


def same_story(a: str, b: str) -> bool:
    """
    Are these two headlines the same story?

    Similarity opens the question; two things settle it, and both outrank
    the score:

    * THE ROLE. If one side names supervision, consultancy, design or
      studies and the other does not, they are separate procurements however
      much wording they share — and losing the consultancy one would cost
      far more than printing a story twice.

    * THE NUMBERS. "Road Rehabilitation Works - Qatar North Area 11" and
      "...Area 12" overlap on six words of eight and score 0.75. One digit
      is the whole difference between two contracts.
    """
    if similarity(a, b) < THRESHOLD:
        return False
    if roles(a) != roles(b):
        return False
    return numbers(a) == numbers(b)


def is_deduplicated(code: str) -> bool:
    """
    Should this source's rows be compared at all?

    No, for a source that publishes FORTHCOMING work. Ashghal's pipeline is
    not a news feed: its titles are systematic package names separated by an
    area, a phase or a three-letter system code — "Road Rehabilitation Works
    - Qatar North Area 1" against "...Qatar South Area 2", "FSN
    rehabilitation" against "TSE rehabilitation". Those are separate
    procurements that happen to be worded alike, and the first backfill
    merged a dozen of them.

    The threshold was measured on prose headlines from news desks. This
    keeps the rule to what it was measured on.
    """
    from . import sources
    return (code or '').upper() not in set(sources.forthcoming_codes())


def _magazine_code(tender) -> str:
    return (getattr(tender.source, 'scraper_class', '') or '').replace('MAGAZINE_', '')


def find_duplicate(tender):
    """
    The earlier row this one duplicates, or None.

    Only magazine rows are considered, and only ones that are not themselves
    mirrors: three copies of a story must all point at the first rather than
    form a chain, because the page follows mirror_of a single step.

    An undated row is compared against everything recent rather than skipped.
    Ad-Dustour and Al-Ghad both store undated rows when an article page will
    not load, and a duplicate is still a duplicate.
    """
    from tenders.models import Tender

    if not (tender.title or '').strip():
        return None
    if not is_deduplicated(_magazine_code(tender)):
        return None

    from . import sources
    pipeline = [f'MAGAZINE_{c}' for c in sources.forthcoming_codes()]

    candidates = (Tender.objects
                  .filter(magazine_meta__isnull=False, is_mirror=False)
                  .exclude(pk=tender.pk)
                  .exclude(source__scraper_class__in=pipeline)
                  .select_related('source')
                  .only('id', 'title', 'publication_date',
                        'source__scraper_class'))

    when = tender.publication_date
    if when is not None:
        span = timedelta(days=WINDOW_DAYS)
        candidates = candidates.filter(
            Q(publication_date__range=(when - span, when + span))
            | Q(publication_date__isnull=True))

    best, best_score = None, THRESHOLD
    for other in candidates.order_by('publication_date', 'id')[:COMPARE_LIMIT]:
        if not same_story(tender.title, other.title):
            continue
        score = similarity(tender.title, other.title)
        if score >= best_score:
            best, best_score = other, score
    return best


def link_if_duplicate(tender) -> bool:
    """
    Mark this row as a duplicate of an earlier one. True when it did.

    Already-marked rows are left alone, so a re-run is a no-op rather than
    a chance to repoint an existing mirror at something else.
    """
    if tender.is_mirror:
        return False

    original = find_duplicate(tender)
    if original is None:
        return False

    # The earlier row is the one that stays. On equal dates the lower pk
    # wins, which is the order they were scraped in.
    a, b = tender, original
    keys = [(x.publication_date or b.publication_date, x.pk) for x in (a, b)]
    if keys[0] < keys[1]:
        a, b = b, a           # `a` is the duplicate, `b` the original

    if a.is_mirror or a.pk == b.pk:
        return False

    a.is_mirror = True
    a.mirror_of = b
    a.save(update_fields=['is_mirror', 'mirror_of'])
    logger.info(f'[dedupe] #{a.pk} is a duplicate of #{b.pk}: {a.title[:70]!r}')
    return a.pk == tender.pk
