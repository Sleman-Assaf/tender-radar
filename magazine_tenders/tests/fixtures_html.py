# magazine_tenders/tests/fixtures_html.py
"""
Saved sample responses, one per magazine, so every scraper is tested offline.

A scraper test must never reach the network: the site changes, the network is
not always available, and a test that depends on either is not a test. When a
site's markup changes, the fix is to re-save its fixture here and update the
selector — the failing test is the signal.
"""

# A minimal listing page in the shape the template scraper expects. Used by the
# runner tests, which need a scraper that returns predictable items without
# depending on any real magazine being configured.
GENERIC_LISTING_HTML = """
<html><body>
  <div class="tender-card">
    <h3>Consultancy services for the design and supervision of a water network</h3>
    <a href="/tenders/1001">details</a>
    <span class="deadline">31/12/2026</span>
  </div>
  <div class="tender-card">
    <h3>Supply of office furniture</h3>
    <a href="/tenders/1002">details</a>
    <span class="deadline">15/11/2026</span>
  </div>
</body></html>
"""

EMPTY_LISTING_HTML = '<html><body><p>No announcements at this time.</p></body></html>'
