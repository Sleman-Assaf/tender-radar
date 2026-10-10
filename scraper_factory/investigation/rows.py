"""Stage 7 — candidate row detection.

Locates every repeating structure that *might* be a tender listing and scores
each one. Candidate rows are not tenders — that judgement belongs to Tender
Validation in Phase 2. This stage only answers: is there a repeating region
here, where is it, and what does one element of it look like?

The scoring is structural (does the region repeat, does it carry links, dates
and enough text?) rather than semantic, so it stays generic across portals and
languages. Procurement vocabulary contributes a bonus but never decides alone —
a nav menu full of the word "tenders" must not outrank the actual table.
"""

from __future__ import annotations

import re

from .contracts import Failure, Stage, StageResult

#: Structural noise that is never a tender listing.
_NOISE_CLASS = re.compile(
    r"nav|menu|header|footer|breadcrumb|sidebar|widget|banner|advert|"
    r"cookie|social|pagination|pager|filter|search-form|dropdown|modal|"
    r"carousel|slider|tab-|accordion", re.I)

_NOISE_TEXT = (
    "popular searches", "most viewed", "trending", "quick links",
    "related links", "follow us", "copyright", "all rights reserved",
    "روابط سريعة", "الأكثر مشاهدة", "جميع الحقوق محفوظة",
)

_TENDER_WORDS = (
    "tender", "bid", "notice", "procurement", "rfq", "rfp", "solicitation",
    "contract", "award", "deadline", "closing", "submission", "invitation",
    "مناقصة", "مناقصات", "عطاء", "عطاءات", "منافسة", "إعلان", "دعوة",
)

_DATE_RE = re.compile(
    r"\d{1,4}[/\-.]\d{1,2}[/\-.]\d{1,4}"
    r"|\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)"
    r"|\d{4}-\d{2}-\d{2}", re.I)


class CandidateRowDetector:
    """Finds repeating regions and ranks them by how listing-like they are."""

    def __init__(self, sample_chars: int | None = None):
        self.sample_chars = int(sample_chars or 14000)

    def run(self, url: str, http: StageResult, rendering: StageResult,
            api: StageResult) -> StageResult:
        result = StageResult(stage=Stage.CANDIDATE_ROWS)
        body = http.data.get("body") or ""

        # When the data source is a proven API, the "rows" are its records and
        # there is nothing to find in the DOM.
        if api.data.get("proven_endpoint"):
            info = next((e for e in api.data.get("endpoints", [])
                         if e.get("url") == api.data["proven_endpoint"]), {})
            count = info.get("records", 0)
            result.conclude("API_RECORDS", 90)
            result.add(f"{count} record(s) from the proven endpoint",
                       where=info.get("items_path") or "response root",
                       detail=f"fields: {', '.join((info.get('fields') or [])[:10])}")
            result.data.update({"candidates": [], "best": None,
                                "record_count": count,
                                "fields": info.get("fields") or []})
            return result

        if rendering.conclusion == "JAVASCRIPT_RENDERED":
            result.conclude("NOT_IN_HTML", 80)
            result.add("listing is rendered by JavaScript; nothing to match in "
                       "the served HTML", where="rendering stage")
            result.unknowns.append("candidate rows require Tier 2 rendering")
            result.data["candidates"] = []
            return result

        soup = self._soup(body)
        if soup is None:
            result.fail(Failure.UNKNOWN, "HTML parser unavailable")
            return result

        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()

        candidates = self._collect(soup)
        candidates.sort(key=lambda c: -c["score"])
        result.data["candidates"] = candidates[:8]

        if not candidates:
            result.conclude("NONE", 65)
            result.add("no repeating region of three or more similar elements",
                       where="parsed DOM")
            result.data["best"] = None
            return result

        best = candidates[0]
        result.data["best"] = best
        result.data["sample"] = best.get("sample", "")[:self.sample_chars]

        # Confidence reflects both the strength of the winner and how clearly it
        # beats the runner-up: two equally plausible regions is genuine doubt.
        margin = best["score"] - (candidates[1]["score"] if len(candidates) > 1 else 0)
        confidence = min(90, 40 + best["score"] // 2 + min(20, margin))
        result.conclude("FOUND", confidence)
        result.add(f"{best['count']} repeating '{best['selector']}' element(s)",
                   where=best["where"],
                   detail=f"score {best['score']}, "
                          f"{best['with_links']} with links, "
                          f"{best['with_dates']} with dates")
        if len(candidates) > 1:
            runners = ", ".join(f"{c['selector']}({c['count']})"
                                for c in candidates[1:4])
            result.add(f"other repeating regions: {runners}",
                       where="parsed DOM",
                       detail="kept as fallbacks for the repair loop")
        if margin < 10 and len(candidates) > 1:
            result.unknowns.append(
                "two regions scored almost equally; the listing may be the runner-up")
        if not best["with_dates"]:
            result.unknowns.append("no dates inside the candidate rows")
        return result

    # ── collection ────────────────────────────────────────────────────────────

    def _collect(self, soup) -> list[dict]:
        out: list[dict] = []

        # Tables: the most explicit listing structure there is.
        for table in soup.find_all("table"):
            rows = [r for r in table.find_all("tr") if r.find_all("td")]
            if len(rows) < 3:
                continue
            out.append(self._describe(
                rows, selector=self._table_selector(table),
                where="table > tr", container=table))

        # Repeating classes: cards, list items, generated divs.
        counts: dict[str, int] = {}
        for el in soup.find_all(class_=True):
            for cls in el.get("class") or []:
                counts[cls] = counts.get(cls, 0) + 1
        for cls, n in sorted(counts.items(), key=lambda kv: -kv[1])[:40]:
            if n < 3 or _NOISE_CLASS.search(cls):
                continue
            elements = soup.find_all(class_=cls)
            if len(elements) < 3:
                continue
            # Skip elements nested inside a bigger candidate of the same class.
            if elements[0].find_parent(class_=cls) is not None:
                continue
            out.append(self._describe(elements, selector=f".{cls}",
                                      where="repeating CSS class"))

        # Plain lists.
        for ul in soup.find_all(["ul", "ol"]):
            kids = ul.find_all("li", recursive=False)
            if len(kids) < 4:
                continue
            classes = " ".join(ul.get("class") or [])
            if _NOISE_CLASS.search(classes):
                continue
            out.append(self._describe(kids, selector=self._list_selector(ul),
                                      where="ul > li", container=ul))

        return [c for c in out if c["score"] > 0]

    def _describe(self, elements: list, *, selector: str, where: str,
                  container=None) -> dict:
        sample_elements = elements[:12]
        texts = [e.get_text(" ", strip=True) for e in sample_elements]
        joined = " ".join(texts).lower()

        with_links = sum(1 for e in sample_elements if e.find("a", href=True))
        with_dates = sum(1 for t in texts if _DATE_RE.search(t))
        avg_len = int(sum(len(t) for t in texts) / max(len(texts), 1))
        noise = any(n in joined for n in _NOISE_TEXT)
        tender_words = sum(1 for w in _TENDER_WORDS if w in joined)

        score = 0
        score += min(30, len(elements) * 2)          # repetition
        score += 20 if avg_len >= 40 else (10 if avg_len >= 15 else 0)
        score += int(20 * with_links / max(len(sample_elements), 1))
        score += int(20 * with_dates / max(len(sample_elements), 1))
        score += min(10, tender_words * 3)           # bonus, never decisive
        if avg_len < 8:
            score -= 25                              # menus and chips
        if noise:
            score -= 30
        score = max(0, score)

        holder = container if container is not None else (
            elements[0].parent if elements[0].parent is not None else elements[0])

        return {
            "selector": selector,
            # An unambiguous CSS selector for ONE ROW, not for the container.
            # `selector` for a table is the table itself ("table#grd"), which
            # matches one element while `count` says 14. Handing that pair to a
            # model invites it to select the container and extract a single row —
            # observed happening in an end-to-end run.
            "css": self._row_css(selector, where, container),
            "where": where,
            "count": len(elements),
            "with_links": with_links,
            "with_dates": with_dates,
            "avg_text_length": avg_len,
            "tender_words": tender_words,
            "noise": noise,
            "score": score,
            "first_text": (texts[0] if texts else "")[:200],
            "sample": str(holder)[:self.sample_chars],
        }

    @staticmethod
    def _row_css(selector: str, where: str, container=None) -> str:
        """A selector matching ONE ROW, based on this page's actual markup.

        The tbody question has to be answered from the page, not by convention.
        Browsers insert a tbody that is not in the source; BeautifulSoup's
        html.parser does not. So `table#g tbody tr` matches nothing on markup
        that omits the tag, while 20 corpus modules use `tbody tr` correctly
        because their portals do include it. Emitting whichever one this page
        actually supports is the only safe answer.
        """
        low = (where or "").lower()
        if "table" in low:
            has_tbody = bool(container is not None
                             and container.find("tbody") is not None)
            return f"{selector} tbody tr" if has_tbody else f"{selector} tr"
        if "ul >" in low or selector.endswith("> li"):
            return selector                     # already row-level
        return selector                         # a repeating class IS the row

    @staticmethod
    def _table_selector(table) -> str:
        for attr in ("id", "class"):
            value = table.get(attr)
            if value:
                token = value if isinstance(value, str) else value[0]
                return (f"table#{token}" if attr == "id" else f"table.{token}")
        return "table"

    @staticmethod
    def _list_selector(ul) -> str:
        classes = ul.get("class") or []
        return f"{ul.name}.{classes[0]} > li" if classes else f"{ul.name} > li"

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
