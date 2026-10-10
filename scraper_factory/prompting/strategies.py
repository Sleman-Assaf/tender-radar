"""Per-strategy generation briefs.

One generic prompt cannot produce eight different kinds of scraper. An API
scraper and an ASP.NET postback scraper share a house style and almost nothing
else: different imports, different control flow, different failure modes. Asking
for both with the same words means the model has to guess which one is wanted,
and guessing is exactly what the investigation exists to eliminate.

Each brief states the task for one strategy in the imperative, lists the steps
that strategy requires, and names the mistakes that strategy specifically
invites. All of it is static text — the measured facts are injected separately
by the prompt builder, so nothing here can drift away from the evidence.
"""

from __future__ import annotations

#: Applies to every strategy. Kept here rather than in each brief so a change
#: to the shared contract cannot be made in one strategy and forgotten in seven.
COMMON_CONTRACT = """\
  - scrape() returns list[TenderData]. The caller persists; you never do.
  - Build every TenderData with keyword arguments, one per line.
  - Fetch the ScraperSource row inside scrape() with
    scraper_class='{class_name}' and use it for source.name in the banner.
  - Wrap each row's parsing in try/except so one malformed row cannot end the run.
  - Print the house banner at the start and the house total at the end.
"""

_STATIC_HTML = """\
TASK — write a static HTML scraper.

The rows are already in the HTML that a plain GET returns. There is no browser,
no API call and no rendering step in this file.

Steps:
  1. GET the listing URL with the client named in the strategy block.
  2. Parse with BeautifulSoup(html, 'html.parser').
  3. Select the rows with the row selector given in the strategy block.
  4. For each row, extract the fields listed under FIELD CONTRACT.
  5. Follow the pagination strategy given in the strategy block. Do not stop
     after page one unless the strategy says NONE.

Mistakes this shape invites — avoid them:
  - Do NOT import playwright, selenium or any browser driver.
  - Do NOT invent a JSON endpoint. If the rows are in the HTML, parse the HTML.
  - Do NOT change the row selector that was measured on the live page.
"""

_API = """\
TASK — write a JSON API scraper.

The portal serves its tenders as JSON. This file makes HTTP requests and reads
dictionaries. It does not parse HTML at all.

Steps:
  1. Call the endpoint given in the strategy block with the stated method.
  2. Send the request headers given in the strategy block, if any.
  3. json.loads / .json() the response and walk to the record path given.
  4. Map each record's keys onto TenderData using the field names listed.
  5. Page with the parameter named in the strategy block until a page returns
     no records.

Mistakes this shape invites — avoid them:
  - Do NOT import bs4 or parse markup.
  - Do NOT invent query parameters that were not observed.
  - Do NOT assume the record array is at the document root; use the measured path.
  - Guard every dict access — a missing key must not end the run.
"""

_GRAPHQL = """\
TASK — write a GraphQL scraper.

Steps:
  1. POST the query document given in the strategy block to the endpoint.
  2. Send Content-Type: application/json and any headers given.
  3. Read the record array from the measured path inside data.
  4. Page with the cursor or offset variable named in the strategy block.

Mistakes this shape invites — avoid them:
  - Do NOT rewrite or 'improve' the query document; send what was observed.
  - Do NOT parse HTML.
  - Check for a top-level 'errors' key before trusting 'data'.
"""

_JAVASCRIPT = """\
TASK — write a scraper that reads the page's embedded JSON.

The portal ships its data inside the HTML as a JSON blob. No extra request and
no browser are needed: fetch the page, cut out the blob, parse it.

Steps:
  1. GET the listing URL.
  2. Extract the blob named in the strategy block with a regex
     (for example the contents of the __NEXT_DATA__ script tag).
  3. json.loads it and walk to the record path given.
  4. Map each record onto TenderData.

Mistakes this shape invites — avoid them:
  - Do NOT import playwright; the data is already in the response.
  - Do NOT parse the surrounding markup with bs4 for the row data.
  - Fail loudly if the blob is missing rather than returning an empty list
    silently.
"""

_ASPNET_POSTBACK = """\
TASK — write an ASP.NET postback scraper.

Page one is a normal GET. Every later page requires a POST that replays the
form state the server handed you.

Steps:
  1. GET page one and keep the session (requests.Session).
  2. Parse __VIEWSTATE, __VIEWSTATEGENERATOR and __EVENTVALIDATION out of the
     hidden inputs.
  3. Parse the rows of page one.
  4. For each subsequent page, POST the same form fields plus __EVENTTARGET and
     __EVENTARGUMENT as given in the strategy block, then re-read the three
     hidden fields from the response before the next POST — they change every
     time.
  5. Stop when a page returns no rows or the page number stops advancing.

Mistakes this shape invites — avoid them:
  - Do NOT reuse the first page's __VIEWSTATE for every POST; refresh it.
  - Do NOT drop the session cookies between requests.
  - Do NOT assume a ?page= parameter exists; this portal pages by postback.
"""

_LOGIN_SESSION = """\
TASK — write a scraper that authenticates before reading the listing.

Steps:
  1. Create a requests.Session.
  2. GET the login page and harvest any CSRF or anti-forgery token.
  3. POST the credentials from settings — never hard-code them in this file.
  4. Confirm the login succeeded before continuing.
  5. Read the listing with the same session and parse as described in the
     strategy block.

Mistakes this shape invites — avoid them:
  - Do NOT write credentials, cookies or tokens into the source.
  - Do NOT continue silently when the login fails; raise or log and return [].
"""

_BROWSER_ASSISTED = """\
TASK — write a browser-assisted scraper.

The rows exist only after JavaScript runs and no reproducible request was found,
so this file drives Playwright. This is the slowest and most fragile family in
the project; keep it minimal.

Steps:
  1. Launch chromium headless.
  2. Navigate to the listing URL and wait for the row selector to appear.
  3. Read the rendered rows.
  4. Follow the paging behaviour given in the strategy block (scroll or click),
     stopping when the row count stops growing.
  5. Always close the browser in a finally block.

Mistakes this shape invites — avoid them:
  - Do NOT leave the browser open on an exception.
  - Do NOT wait on fixed sleeps where a selector wait will do.
  - Do NOT re-request pages you have already read.
"""

_HYBRID = """\
TASK — write a hybrid scraper.

The listing and the detail pages come from different places. Follow the listing
directive first, then the detail directive for each row.

Steps:
  1. Collect the rows using the listing method in the strategy block.
  2. For each row, fetch its detail page or record only if a required field is
     missing from the listing.
  3. Merge the two into one TenderData per tender.

Mistakes this shape invites — avoid them:
  - Do NOT fetch a detail page for every row when the listing already has the
    fields; that multiplies runtime by the page size for no gain.
"""

#: strategy → brief. Keys mirror planning.planner.Strategy.
BRIEFS: dict[str, str] = {
    "STATIC_HTML": _STATIC_HTML,
    "API": _API,
    "GRAPHQL": _GRAPHQL,
    "JAVASCRIPT": _JAVASCRIPT,
    "ASPNET_POSTBACK": _ASPNET_POSTBACK,
    "LOGIN_SESSION": _LOGIN_SESSION,
    "BROWSER_ASSISTED": _BROWSER_ASSISTED,
    "HYBRID": _HYBRID,
}

_FALLBACK = """\
TASK — write a scraper for this portal.

No strategy brief is registered for '{strategy}', so follow the strategy block
and the reference files exactly and add nothing that the evidence does not
support.
"""


def brief_for(strategy: str, *, class_name: str = "") -> str:
    """The task section for one strategy, with the shared contract appended."""
    body = BRIEFS.get(strategy) or _FALLBACK.format(strategy=strategy or "UNKNOWN")
    contract = COMMON_CONTRACT.format(class_name=class_name or "<Class>")
    return f"{body}\nALWAYS, whatever the strategy:\n{contract}"


def known_strategies() -> list[str]:
    return sorted(BRIEFS)
