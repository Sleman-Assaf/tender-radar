# magazine_tenders/summaries.py
"""
The summary on a News card, written by AJi AI.

    runner -> summarise_tender() -> POST {AJIAI_API_BASE}/api/general-chat/ -> model

A card shows a photograph, a headline, a short summary and the way to the
article. The publisher's own excerpt served as the summary, but a third of
the sources publish none (Zawya's feed is headline and photo only) and the
rest write for a general reader. What an engineer scans a construction story
for is the figures - value, size, length, capacity, dates, who is building
for whom - so the summary is written for that, once, when the article is
first stored, and kept on the provenance row. Nothing is generated while a
page renders.

The endpoint is the same AJi AI General Chat API the scraper factory uses,
with the same settings (AJIAI_API_BASE, AJIAI_CHAT_PATH, AJIAI_API_KEY,
AJIAI_SEED, AJIAI_MODEL, AJIAI_TRUST_PROXY) - see
scraper_factory/models_ai/ajiai_provider.py for the contract it documents.
No `model` key is sent: AJi AI resolves its active model itself.

Every function here fails soft. A summary is a nicety; the article it
describes must never be lost because the model was slow or down. After a few
consecutive transport failures in one run the rest of the run stops asking,
so a dead service costs seconds, not hours.

HOW THE LOAD IS KEPT GENTLE. The first version asked for each summary inline
as the sweep stored the article - right after the classifier's own call for
the same row - and a sweep of thirteen sources became a burst of hundreds of
back-to-back requests on a 27B model, which the AI host did not survive.
Now nothing is asked during the sweep. When the whole sweep has finished,
`summarise_backlog()` takes the newest articles without a summary, a capped
number of them, one at a time with a pause between, after checking the
service reports its model online - and stops at the first failure. The same
function serves the `summarise_news` command.
"""
import logging
import re
import time

from django.conf import settings

logger = logging.getLogger('tenders')

SYSTEM_PROMPT = (
    "You write detailed summaries of construction and engineering news for AJi, "
    "a design and supervision consultancy. Write one paragraph of four to six "
    "plain sentences, 90 to 140 words. Cover, in this order where the article "
    "gives it: what is being built, procured or designed and its scope; the "
    "project value and how it is financed; size, length, area or capacity; the "
    "location; the client or owner, the contractor, and the designer, consultant "
    "or supervisor; the dates, stages and deadlines; and any design, supervision "
    "or consultancy services involved. Keep every figure exactly as the article "
    "gives it, with its currency and units. Write in the language of the "
    "article. Say only what the article says; never remark on what it does not "
    "give. No preamble, no bullet points, no markdown, no quotation marks."
)

#: Shorter than this and the text is a headline, not an article: fetch the page.
MIN_ARTICLE_CHARS = 300
#: The model reads this much of the article at most. Sized to sit inside the
#: server's default context with the instructions and the answer: sending a
#: larger num_ctx makes Ollama reload the model, which is what the timeouts
#: on 1 October turned out to be.
MAX_ARTICLE_CHARS = 3000
#: A summary longer than this is not a summary.
MAX_SUMMARY_CHARS = 1400
#: Consecutive transport failures after which a run stops asking.
BREAKER_TRIPS = 3
#: How long the breaker stays open before the next article is allowed to try
#: again. A sweep of thirteen sources resets it once at the start; without the
#: cooldown a long-lived server process that tripped it in the morning would
#: never summarise again until restarted.
BREAKER_COOLDOWN_S = 15 * 60

_consecutive_failures = 0
_tripped_at = 0.0


def enabled() -> bool:
    return bool(getattr(settings, 'MAGAZINE_AI_SUMMARIES', True))


def reset_breaker():
    """Called at the start of a sweep: a new run gets a fresh chance."""
    global _consecutive_failures, _tripped_at
    _consecutive_failures = 0
    _tripped_at = 0.0


def breaker_open() -> bool:
    if _consecutive_failures < BREAKER_TRIPS:
        return False
    if time.monotonic() - _tripped_at > BREAKER_COOLDOWN_S:
        reset_breaker()                 # the cooldown has passed: one more try
        return False
    return True


def _record_failure():
    global _consecutive_failures, _tripped_at
    _consecutive_failures += 1
    if _consecutive_failures >= BREAKER_TRIPS:
        _tripped_at = time.monotonic()
        logger.warning(f'[summaries] AJi AI failed {BREAKER_TRIPS} times in a row; '
                       f'not asking again for {BREAKER_COOLDOWN_S // 60} minutes')


# ── the article text ────────────────────────────────────────────────────────

def extract_article_text(html: str) -> str:
    """
    The readable paragraphs of an article page, as one string.

    Prefers an <article> element when the page has one; drops navigation,
    scripts and the furniture around the text; keeps paragraphs long enough
    to be sentences. Capped, because the model needs the story, not the
    comments section.
    """
    if not html:
        return ''
    try:
        from bs4 import BeautifulSoup
    except ImportError:                                     # pragma: no cover
        return ''
    soup = BeautifulSoup(html, 'html.parser')
    for tag in soup(['script', 'style', 'nav', 'header', 'footer', 'aside',
                     'form', 'noscript', 'figure', 'iframe']):
        tag.decompose()
    root = soup.find('article') or soup.body or soup
    parts = []
    for p in root.find_all(['p', 'h2', 'h3', 'li']):
        text = ' '.join(p.get_text(' ', strip=True).split())
        if len(text) >= 40:
            parts.append(text)
    text = ' '.join(parts)
    return text[:MAX_ARTICLE_CHARS]


def fetch_article_text(url: str, timeout: int = 25) -> str:
    """The article's text from its page, or '' when it cannot be read."""
    if not (url or '').lower().startswith(('http://', 'https://')):
        return ''
    try:
        from curl_cffi import requests as cr
        r = cr.get(url, impersonate='chrome', timeout=timeout)
        if r.status_code != 200:
            logger.info(f'[summaries] HTTP {r.status_code} fetching {url[:90]}')
            return ''
        return extract_article_text(r.text)
    except Exception as exc:                                # noqa: BLE001
        logger.info(f'[summaries] could not fetch {url[:90]}: {exc}')
        return ''


def text_for(tender, td=None, fetch: bool = True) -> str:
    """
    What the model reads: the article body the scraper already had, or the
    page itself when the feed gave only a headline, or - last - whatever
    standfirst was stored.
    """
    text = ''
    if td is not None:
        text = (getattr(td, 'article_text', '') or '').strip()
    if len(text) < MIN_ARTICLE_CHARS and fetch:
        meta = getattr(tender, 'magazine_meta', None)
        url = (getattr(meta, 'listing_url', '') or tender.source_url or '').strip()
        fetched = fetch_article_text(url)
        if len(fetched) > len(text):
            text = fetched
    if not text:
        from .views import standfirst_of
        text = standfirst_of(tender.description)
    return text[:MAX_ARTICLE_CHARS]


# ── the model ───────────────────────────────────────────────────────────────

def _config() -> dict:
    g = lambda name, default: getattr(settings, name, None) or default   # noqa: E731
    base = str(g('AJIAI_API_BASE', 'http://172.30.0.4:8000')).strip().rstrip('/')
    return {
        'url':         base + str(g('AJIAI_CHAT_PATH', '/api/general-chat/')),
        'api_key':     str(getattr(settings, 'AJIAI_API_KEY', '') or '').strip(),
        'seed':        int(g('AJIAI_SEED', 42)),
        'model':       str(getattr(settings, 'AJIAI_MODEL', '') or '').strip(),
        'trust_proxy': bool(getattr(settings, 'AJIAI_TRUST_PROXY', False)),
        'timeout':     int(g('MAGAZINE_SUMMARY_TIMEOUT', 60)),
    }


def _clean(answer: str) -> str:
    quotes = ' "\'“”«»'
    text = ' '.join((answer or '').split()).strip(quotes)
    text = re.sub(r'^(?:summary|ملخص)\s*[:：]\s*', '', text, flags=re.I)
    return text.strip(quotes)[:MAX_SUMMARY_CHARS]


def summarise(title: str, text: str) -> tuple[str, str]:
    """
    (summary, model) from AJi AI, or ('', '') when there is nothing usable.

    Never raises. Transport failures count towards the breaker; a reply that
    is merely unusable (empty, cut off) does not - the service is up.
    """
    global _consecutive_failures
    if not enabled() or breaker_open():
        return '', ''
    body = (text or '').strip()
    if len(body) < 40 and not (title or '').strip():
        return '', ''
    cfg = _config()
    payload = {
        'system':      SYSTEM_PROMPT,
        'prompt':      f'Headline: {title}\n\nArticle:\n{body[:MAX_ARTICLE_CHARS]}',
        'temperature': 0,
        'seed':        cfg['seed'],
        'max_tokens':  500,
        'timeout':     cfg['timeout'],
        'stream':      False,
        # The active model is a reasoning model. Thinking triples the tokens
        # and the time for a summary and adds nothing to it; pinned off so a
        # change of the server's default cannot slow the sweep.
        'think':       False,
    }
    if cfg['model']:
        payload['model'] = cfg['model']
    headers = {'X-API-Key': cfg['api_key']} if cfg['api_key'] else {}
    try:
        import requests
        session = requests.Session()
        # Internal address: the corporate proxy would answer instead of AJi AI.
        session.trust_env = cfg['trust_proxy']
        started = time.monotonic()
        resp = session.post(cfg['url'], json=payload, headers=headers,
                            timeout=cfg['timeout'] + 30)
        try:
            data = resp.json() if resp.content else {}
        except ValueError:
            data = None
        if resp.status_code >= 400 or data is None:
            _record_failure()
            logger.warning(f'[summaries] AJi AI HTTP {resp.status_code} '
                           f'({(data or {}).get("error", "") if isinstance(data, dict) else "non-JSON reply"})')
            return '', ''
    except Exception as exc:                                # noqa: BLE001
        _record_failure()
        logger.warning(f'[summaries] AJi AI unreachable: {exc}')
        return '', ''

    _consecutive_failures = 0
    summary = _clean(str(data.get('answer') or ''))
    model = str(data.get('model') or '')
    if len(summary) < 20:
        logger.info(f'[summaries] empty or too short answer '
                    f'(done_reason {data.get("done_reason")!r})')
        return '', ''
    logger.info(f'[summaries] {len(summary)} chars in '
                f'{int((time.monotonic() - started) * 1000)}ms from {model or "?"}')
    return summary, model


def service_online(timeout: int = 10) -> bool:
    """
    Does AJi AI report its model online? Read from /info/, never raises.

    A weak check - the service has reported online while its model was not
    answering - but it is free, and it keeps a batch from starting against a
    host that already says it is down.
    """
    cfg = _config()
    info_url = cfg['url'].rstrip('/') + '/info/'
    try:
        import requests
        session = requests.Session()
        session.trust_env = cfg['trust_proxy']
        resp = session.get(info_url, timeout=timeout)
        return resp.status_code == 200 and bool(resp.json().get('llm_online', True))
    except Exception as exc:                                # noqa: BLE001
        logger.info(f'[summaries] AJi AI info not reachable: {exc}')
        return False


def _batch_settings() -> tuple[int, float]:
    """(articles per batch, seconds between articles)."""
    limit = int(getattr(settings, 'MAGAZINE_SUMMARY_BATCH', 40) or 40)
    pause = float(getattr(settings, 'MAGAZINE_SUMMARY_PAUSE', 5) or 0)
    return max(1, limit), max(0.0, pause)


def pending_rows(limit: int, magazine: str = '', redo: bool = False):
    """The provenance rows due a summary, newest article first."""
    from . import sources
    from .models import MagazineTenderMeta
    qs = (MagazineTenderMeta.objects.select_related('tender')
          .filter(tender__is_mirror=False)
          .exclude(magazine__in=sources.forthcoming_codes())
          .order_by('-tender__publication_date', '-tender__scraped_at'))
    if magazine:
        qs = qs.filter(magazine=magazine)
    if not redo:
        qs = qs.filter(summary='')
    return list(qs[:limit])


def summarise_backlog(limit: int = None, pause: float = None, magazine: str = '',
                      redo: bool = False, report=None) -> dict:
    """
    Write summaries for the newest articles that have none - gently.

    One article at a time, `pause` seconds apart, at most `limit` per call,
    only after the service reports its model online, and stopping at the
    first failure (the breaker). Returns {'written', 'tried', 'stopped'}.
    `report(line)` receives one line per article for the command's output.
    """
    out = {'written': 0, 'tried': 0, 'stopped': ''}
    if not enabled():
        out['stopped'] = 'MAGAZINE_AI_SUMMARIES is off'
        return out
    batch_limit, batch_pause = _batch_settings()
    limit = batch_limit if limit is None else max(1, int(limit))
    pause = batch_pause if pause is None else max(0.0, float(pause))
    if not service_online():
        out['stopped'] = 'AJi AI reports its model offline; not asking'
        logger.info(f'[summaries] {out["stopped"]}')
        return out
    rows = pending_rows(limit, magazine, redo)
    reset_breaker()
    for i, meta in enumerate(rows, 1):
        if i > 1 and pause:
            time.sleep(pause)
        tender = meta.tender
        tender.magazine_meta = meta
        ok = summarise_tender(tender)
        out['tried'] += 1
        out['written'] += bool(ok)
        if report:
            report(f'  {"ok " if ok else "-- "} {i:3}/{len(rows)} {meta.magazine:16} {tender.title[:70]}')
        if not ok and breaker_open():
            out['stopped'] = 'AJi AI stopped answering; the rest waits for the next run'
            logger.warning(f'[summaries] {out["stopped"]}')
            break
        if not ok and _consecutive_failures:
            # One transport failure is enough to back off this batch: a
            # struggling host is not helped by the next request arriving
            # five seconds later. The breaker still decides the long pause.
            out['stopped'] = 'AJi AI failed once; stopping this batch early'
            logger.warning(f'[summaries] {out["stopped"]}')
            break
    logger.info(f'[summaries] batch done: {out["written"]} written of {out["tried"]} tried'
                + (f' ({out["stopped"]})' if out['stopped'] else ''))
    return out


def summarise_tender(tender, td=None, fetch: bool = True) -> bool:
    """
    Write the summary onto the tender's provenance row. True when stored.

    Reads the article the scraper carried (td.article_text) when it has one,
    otherwise fetches the page. Called by the runner for new rows and by the
    `summarise_news` command for the backlog.
    """
    meta = getattr(tender, 'magazine_meta', None)
    if meta is None or not enabled():
        return False
    # A forthcoming-projects notice (Ashghal's pipeline, a DevelopmentAid
    # forecast) is not an article: the page behind it is the donor's
    # boilerplate, and a summary of that is noise on the card. Its photo slot
    # already shows the expected quarter, which is the fact that matters.
    from . import sources
    if meta.magazine in sources.forthcoming_codes():
        return False
    text = text_for(tender, td, fetch=fetch)
    summary, model = summarise(tender.title, text)
    if not summary:
        return False
    meta.summary = summary
    meta.summary_model = model[:60]
    meta.save(update_fields=['summary', 'summary_model'])
    return True
