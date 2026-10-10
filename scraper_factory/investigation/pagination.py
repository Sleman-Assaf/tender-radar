"""Stage 6 — pagination detection.

Reports how to reach page two, because "never stop after page one unless
evidence proves there are no more pages" can only be honoured if the strategy
is known. NONE and UNKNOWN are different answers and must not be conflated:
NONE means the stage found positive evidence of a single page, UNKNOWN means it
could not tell.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from .contracts import Stage, StageResult

_PAGE_PARAMS = ("page", "p", "pageindex", "pagenumber", "pageno", "pg",
                "start", "offset", "skip", "from")
_SIZE_PARAMS = ("size", "pagesize", "limit", "take", "per_page", "count", "rows")
_CURSOR_KEYS = ("cursor", "nextcursor", "next_cursor", "continuationtoken",
                "continuation_token", "nexttoken", "next_token", "after",
                "scrollid", "scroll_id")
_TOTAL_KEYS = ("total", "totalcount", "total_count", "totalrecords",
               "totalresults", "recordstotal", "count", "totalpages",
               "pagecount", "numberofpages")
_MORE_TEXT = ("load more", "show more", "view more", "المزيد", "عرض المزيد",
              "تحميل المزيد")


class PaginationDetector:
    """Determines the pagination strategy from the URL, DOM and API payloads."""

    def run(self, url: str, http: StageResult, api: StageResult,
            rendering: StageResult) -> StageResult:
        result = StageResult(stage=Stage.PAGINATION)
        body = http.data.get("body") or ""

        # An API data source paginates through its own parameters, not the DOM.
        proven = api.data.get("proven_endpoint")
        if proven:
            verdict = self._api_pagination(proven, api)
            if verdict is not None:
                strategy, confidence, note, data = verdict
                result.conclude(strategy, confidence)
                result.add(note, where="API endpoint")
                result.data.update(data)
                return result

        query = parse_qs((urlparse(url).query or "").lower())
        for param in _PAGE_PARAMS:
            if param in query:
                result.conclude("QUERY_PARAM", 90)
                result.add(f"the requested URL already carries ?{param}=",
                           where="URL query",
                           detail=f"value {query[param][0][:20]}")
                result.data.update({"param": param, "style": "query"})
                self._note_page_size(result, query)
                return result

        soup = self._soup(body)
        if soup is None:
            result.conclude("UNKNOWN", 25)
            result.unknowns.append("HTML parser unavailable; cannot inspect links")
            return result

        # Numbered links are the strongest DOM evidence.
        param_hits: dict[str, int] = {}
        path_hits = 0
        for a in soup.find_all("a", href=True)[:600]:
            href = a["href"].lower()
            m = re.search(r"[?&](page|p|pageindex|pagenumber|pageno|start|offset)=(\d+)",
                          href)
            if m:
                param_hits[m.group(1)] = param_hits.get(m.group(1), 0) + 1
            if re.search(r"/page/\d+", href):
                path_hits += 1

        if param_hits:
            param = max(param_hits, key=param_hits.get)
            result.conclude("QUERY_PARAM", 88)
            result.add(f"{param_hits[param]} link(s) use ?{param}=<n>",
                       where="anchor hrefs")
            result.data.update({"param": param, "style": "query",
                                "link_count": param_hits[param]})
            return result

        if path_hits:
            result.conclude("PATH", 85)
            result.add(f"{path_hits} link(s) use /page/<n>", where="anchor hrefs")
            result.data.update({"style": "path", "link_count": path_hits})
            return result

        # ASP.NET postback paging. Checked before the "no controls, so single
        # page" branch: a __doPostBack link is a real next page, and calling it
        # NONE would silently cap the scraper at page one.
        postback = self._postback_targets(soup)
        if postback:
            result.conclude("POSTBACK", 85)
            result.add(f"{len(postback)} paging control(s) call __doPostBack",
                       where="anchor href/onclick",
                       detail=f"first target: {postback[0][0]} / {postback[0][1]}")
            result.data.update({"style": "postback",
                                "event_targets": postback[:5]})
            result.unknowns.append(
                "page two requires a POST carrying __VIEWSTATE and "
                "__EVENTVALIDATION harvested from the current page")
            return result

        # Load-more / infinite scroll: a control with no href behind it.
        text = soup.get_text(" ", strip=True).lower()
        more_hit = next((t for t in _MORE_TEXT if t in text), "")
        if more_hit:
            result.conclude("LOAD_MORE", 65)
            result.add(f"'{more_hit}' control present", where="page text",
                       detail="the request behind it is not visible without a browser")
            result.unknowns.append(
                "the load-more request must be captured in Tier 2 to be reproduced")
            result.data["style"] = "load_more"
            return result

        if soup.find(attrs={"data-infinite-scroll": True}) or re.search(
                r"infinite[-_]?scroll", body, re.I):
            result.conclude("INFINITE_SCROLL", 65)
            result.add("infinite-scroll marker found", where="markup/script")
            result.unknowns.append("scroll trigger and cursor need Tier 2 capture")
            result.data["style"] = "infinite_scroll"
            return result

        container = soup.find(class_=re.compile(r"pagination|pager|paging", re.I))
        if container is not None:
            result.conclude("QUERY_PARAM", 55)
            result.add("a pagination container exists but exposes no numbered links",
                       where="CSS class",
                       detail=(container.get("class") or [""])[0])
            result.unknowns.append(
                "pagination is probably script-driven; parameter name unproven")
            result.data["style"] = "unproven"
            return result

        rows = (rendering.data.get("structure") or {}).get("rows", 0)
        if rows:
            result.conclude("NONE", 70)
            result.add(f"{rows} row(s) present with no pagination controls anywhere",
                       where="parsed DOM", detail="single-page listing")
            result.data["style"] = "none"
            return result

        result.conclude("UNKNOWN", 30)
        result.unknowns.append("no listing and no pagination controls found")
        return result

    # ── API-side pagination ───────────────────────────────────────────────────

    @staticmethod
    def _api_pagination(endpoint: str, api: StageResult):
        info = next((e for e in api.data.get("endpoints", [])
                     if e.get("url") == endpoint), None)
        if info is None:
            return None
        fields = {f.lower() for f in info.get("fields") or []}
        query = parse_qs((urlparse(endpoint).query or "").lower())

        for key in _CURSOR_KEYS:
            if key in fields:
                return ("CURSOR", 88,
                        f"response carries a '{key}' continuation field",
                        {"style": "cursor", "cursor_key": key})
        for param in _PAGE_PARAMS:
            if param in query:
                return ("QUERY_PARAM", 88,
                        f"endpoint already uses ?{param}=",
                        {"style": "query", "param": param})
        for key in _TOTAL_KEYS:
            if key in fields:
                return ("QUERY_PARAM", 70,
                        f"response reports '{key}', implying a paged collection",
                        {"style": "query", "total_key": key, "param": ""})
        return ("NONE", 55, "endpoint returned a plain array with no paging metadata",
                {"style": "none"})

    @staticmethod
    def _postback_targets(soup) -> list[tuple[str, str]]:
        """Extract (__EVENTTARGET, __EVENTARGUMENT) pairs from paging controls."""
        pattern = re.compile(
            r"""__doPostBack\s*\(\s*['"]([^'"]*)['"]\s*,\s*['"]([^'"]*)['"]""",
            re.I)
        out: list[tuple[str, str]] = []
        for el in soup.find_all(["a", "input", "button"])[:600]:
            blob = " ".join(filter(None, (el.get("href", ""),
                                          el.get("onclick", ""))))
            m = pattern.search(blob)
            if m and (m.group(1), m.group(2)) not in out:
                out.append((m.group(1), m.group(2)))
        return out

    @staticmethod
    def _note_page_size(result: StageResult, query: dict) -> None:
        for param in _SIZE_PARAMS:
            if param in query:
                result.add(f"page size controlled by ?{param}=",
                           where="URL query", detail=query[param][0][:10])
                result.data["size_param"] = param
                return

    @staticmethod
    def _soup(body: str):
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            return None
        try:
            return BeautifulSoup(body, "html.parser")
        except Exception:  # noqa: BLE001
            return None
