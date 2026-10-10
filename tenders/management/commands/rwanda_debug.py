# tenders/management/commands/rwanda_debug.py
"""
Diagnostic command to discover the correct Rwanda UMUCYO tender listing endpoint.

Usage:
    python manage.py rwanda_debug
    python manage.py rwanda_debug --save   # saves full HTML to rwanda_debug_*.html
"""

import re
import sys
from django.core.management.base import BaseCommand
import requests
from bs4 import BeautifulSoup

BASE = "https://www.umucyo.gov.rw"

CANDIDATES = [
    "/pt/pcm/moveMainPageDetail.do",
    "/pt/pcm/moveAdminAnnouncementList.do",
    "/pt/pcm/moveBidNtceLst.do",
    "/pt/pcm/movePublicBidNtceLst.do",
    "/pt/pcm/selectBidNtceLst.do",
    "/pt/pcm/selectBidNtceLstPublicBidNtceSrch.do",
    "/pt/pcm/bidNtceLst.do",
    "/pt/pcm/bidNoticeList.do",
    "/pt/pcm/publicBidNtceLst.do",
    "/pt/pcm/publicNtceLst.do",
    "/pt/pcm/publicNotice.do",
    "/pt/pcm/noticeListMain.do",
    "/pt/pcm/retrieveMainBidNtceLst.do",
    "/pt/pcm/selectMainBidNtceLst.do",
    "/pt/pcm/mainBidNtceLst.do",
    "/pt/tnd/moveTndInvtList.do",
    "/pt/tnd/selectTndInvtList.do",
    "/pt/invt/selectListTendInvitAL.do",
]

HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept":          "text/html,application/xhtml+xml,*/*;q=0.9",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":         BASE + "/pt/pcm/moveMainPageDetail.do",
}

POST_DATA = {
    "pageIndex": "1",
    "recordCountPerPage": "10",
    "searchCondition": "",
    "searchKeyword": "",
    "bidNtceNm": "",
    "ntceInsttCd": "",
    "bidNtceSttDt": "",
    "bidNtceEndDt": "",
}

XHR_PATHS = [
    "/pt/pcm/moveBidNtceLst.do",
    "/pt/pcm/retrieveMainBidNtceLst.do",
    "/pt/pcm/selectMainBidNtceLst.do",
    "/pt/pcm/mainBidNtceLst.do",
    "/pt/pcm/moveAdminAnnouncementList.do",
    "/pt/pcm/retrieveBidNtceLst.do",
    "/pt/pcm/selectBidNtceLst.do",
]


class Command(BaseCommand):
    help = "Diagnose Rwanda UMUCYO e-Procurement portal to find the correct tender listing URL"

    def add_arguments(self, parser):
        parser.add_argument(
            "--save", action="store_true",
            help="Save full HTML responses to rwanda_debug_*.html"
        )

    def handle(self, *args, **options):
        # Force UTF-8 output on Windows
        import sys, io
        if hasattr(sys.stdout, 'buffer'):
            try:
                sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
            except Exception:
                pass
        save = options["save"]
        out  = self.stdout

        s = requests.Session()
        s.headers.update(HEADERS)

        def p(msg=""):
            try:
                out.write(msg + "\n")
            except (UnicodeEncodeError, UnicodeDecodeError):
                safe = msg.encode("ascii", errors="replace").decode("ascii")
                out.write(safe + "\n")

        def probe(label, url, method="GET", data=None, extra_headers=None, silent=False):
            full_headers = dict(s.headers)
            if extra_headers:
                full_headers.update(extra_headers)
            try:
                if method == "GET":
                    r = s.get(url, headers=full_headers, timeout=15, allow_redirects=True)
                else:
                    r = s.post(url, data=data or {}, headers=full_headers, timeout=15, allow_redirects=True)
            except Exception as e:
                if not silent:
                    p(f"  ERROR: {e}")
                return None

            soup   = BeautifulSoup(r.text, "html.parser")
            tables = soup.find_all("table")
            rows   = sum(len(t.find_all("tr")) for t in tables)
            dos    = re.findall(r'["\']([^"\']*\.do[^"\']*)["\']', r.text)
            frames = soup.find_all(["frame", "iframe"])

            if not silent:
                p(f"\n{'='*60}")
                p(f"  {method} {url}")
                p(f"{'='*60}")
                p(f"  Status       : {r.status_code}")
                p(f"  Final URL    : {r.url}")
                p(f"  Length       : {len(r.text):,} chars")
                p(f"  Content-Type : {r.headers.get('content-type','')}")
                p(f"  Cookies      : {dict(s.cookies)}")
                p(f"  Tables       : {len(tables)}  |  Total rows: {rows}")
                p(f"  .do refs     : {len(set(dos))}")

                if frames:
                    p(f"\n  Frames ({len(frames)}):")
                    for f in frames:
                        p(f"    src={f.get('src','')}  name={f.get('name','')}")

                if tables:
                    p(f"\n  Tables detail:")
                    for i, t in enumerate(tables[:8]):
                        trows = t.find_all("tr")
                        txt   = t.get_text(separator="|", strip=True)[:200]
                        p(f"    [{i}] {len(trows)} rows : {txt!r}")

                if dos:
                    unique_dos = sorted(set(dos))
                    p(f"\n  .do URLs ({len(unique_dos)}):")
                    for d in unique_dos[:30]:
                        p(f"    {d}")

                if r.text.strip():
                    p(f"\n  First 2000 chars:")
                    for line in r.text[:2000].splitlines():
                        p(f"    {line}")
                else:
                    p("  (empty response body)")

                if save and r.text.strip():
                    fname = f"rwanda_debug_{label.replace(' ','_')}.html"
                    with open(fname, "w", encoding="utf-8") as f:
                        f.write(r.text)
                    p(f"\n  Saved -> {fname}")

            return r

        # ── Step 1: root page ─────────────────────────────────────────────────
        p("\n" + "="*60)
        p("  STEP 1 — Root page")
        p("="*60)
        probe("root", BASE + "/")

        # ── Step 2: main frame ────────────────────────────────────────────────
        p("\n" + "="*60)
        p("  STEP 2 — Main frame page")
        p("="*60)
        r_main = probe("main_frame", BASE + "/pt/pcm/moveMainPageDetail.do")

        # ── Step 3: all candidates quick table ────────────────────────────────
        p("\n" + "="*60)
        p("  STEP 3 — Quick scan of all candidate paths")
        p("="*60)
        p(f"\n  {'METHOD':<6} {'STATUS':>6} {'LEN':>8} {'TABLES':>7} {'ROWS':>6}  URL")
        p(f"  {'-'*6} {'-'*6} {'-'*8} {'-'*7} {'-'*6}  {'-'*45}")

        hits = []
        for path in CANDIDATES:
            url = BASE + path
            for method in ("GET", "POST"):
                data = POST_DATA if method == "POST" else None
                try:
                    if method == "GET":
                        r = s.get(url, timeout=8, allow_redirects=True)
                    else:
                        r = s.post(url, data=data, timeout=8, allow_redirects=True)

                    soup   = BeautifulSoup(r.text, "html.parser")
                    tables = soup.find_all("table")
                    rows   = sum(len(t.find_all("tr")) for t in tables)
                    flag   = "  ← HAS TABLE" if rows > 0 else ""
                    p(f"  {method:<6} {r.status_code:>6} {len(r.text):>8,} {len(tables):>7} {rows:>6}  {path}{flag}")
                    if rows > 0 and r.status_code == 200:
                        hits.append((method, url, r, rows))
                except Exception as e:
                    p(f"  {method:<6} {'ERR':>6} {'-':>8} {'-':>7} {'-':>6}  {path}  ({str(e)[:50]})")

        # ── Step 4: detail probe on every hit ─────────────────────────────────
        if hits:
            p("\n" + "="*60)
            p("  STEP 4 — Detail probe on URLs that returned tables")
            p("="*60)
            for method, url, r, rows in hits:
                label = url.split("/")[-1].replace(".do","")
                probe(label, url, method=method,
                      data=POST_DATA if method == "POST" else None)

        # ── Step 5: XHR / AJAX style ──────────────────────────────────────────
        p("\n" + "="*60)
        p("  STEP 5 — XHR / AJAX style POST (X-Requested-With header)")
        p("="*60)
        xhr_headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type":     "application/x-www-form-urlencoded; charset=UTF-8",
            "Referer":          BASE + "/pt/pcm/moveMainPageDetail.do",
        }
        for path in XHR_PATHS:
            url = BASE + path
            for extra_data in [
                POST_DATA,
                {**POST_DATA, "bidClseCd": ""},
                {**POST_DATA, "bidClseCd": "W"},    # Works
                {**POST_DATA, "ntceSttCd": "ing"},  # in-progress / open
            ]:
                try:
                    r = s.post(url, data=extra_data, headers={**dict(s.headers), **xhr_headers}, timeout=8)
                    soup   = BeautifulSoup(r.text, "html.parser")
                    tables = soup.find_all("table")
                    rows   = sum(len(t.find_all("tr")) for t in tables)
                    flag   = "  ← HAS TABLE" if rows > 0 else ""
                    p(f"  POST {r.status_code} len={len(r.text):>7,} tables={len(tables)} rows={rows}  {path}{flag}")
                    if rows > 0 and r.status_code == 200:
                        p(f"    preview: {r.text[:500]!r}")
                    break  # only try first payload per path unless 404
                except Exception as e:
                    p(f"  POST ERR  {path}  {str(e)[:60]}")

        # ── Summary ───────────────────────────────────────────────────────────
        p("\n" + "="*60)
        p("  SUMMARY")
        p("="*60)
        if hits:
            p(f"\n  ✓ URLs that returned tables ({len(hits)}):")
            for method, url, r, rows in hits:
                p(f"    {method} {url}  ({rows} rows)")
        else:
            p("\n  ✗ No URLs returned HTML tables — the site may require JavaScript")
            p("    or a specific session cookie from a prior login step.")
        p("")
