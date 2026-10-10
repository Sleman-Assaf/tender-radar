# newspaper_tenders/tests/fixtures_html.py
# Miniature fixtures reproducing the REAL Al-Rai markup shapes captured live
# on 2026-08-19 (Arabic hrefs served unencoded; `<embed src ="…">` with a
# space before `=`; article body published before PDFs are attached).

SECTION_PAGE = """<!doctype html><html><body>
<a href="https://alrai.com/الاعلانات-الورقية">section self-link (no date)</a>
<a href="https://alrai.com/article/10972357/الاعلانات-الورقية/يمكنكم-الآن-تصفح-إعلانات-صحيفة-الرأي-الورقية-لليوم-18-اب-2026-عبر-الموقع-الإلكتروني">18</a>
<a href="https://alrai.com/article/10972360/الاعلانات-الورقية/يمكنكم-الآن-تصفح-إعلانات-صحيفة-الرأي-الورقية-لليوم-19-اب-2026-عبر-الموقع-الإلكتروني">19</a>
<a href="https://alrai.com/article/10971387/الاعلانات-الورقية/يمكنكم-الآن-تصفح-إعلانات-صحيفة-الرأي-الورقية-لليوم-13-آب-2026-عبر-الموقع-الإلكتروني">13 with madda</a>
<a href="https://alrai.com/article/999/الاعلانات-الورقية/عنوان-بدون-تاريخ">no date</a>
</body></html>"""

ARTICLE_WITH_PDFS = """<!doctype html><html><body><p>
إعلانات مبوبة، وظائف، عطاءات
<div class="swiper-slide"><embed src ="https://alrai.com/alraijordan/uploads/images/2026/08/19/763671.pdf" width="100%"></div>
<div class="swiper-slide"><embed src ="https://alrai.com/alraijordan/uploads/images/2026/08/19/763672.pdf" width="100%"></div>
<div class="swiper-slide"><embed src ="https://alrai.com/alraijordan/uploads/images/2026/08/19/763671.pdf" width="100%"></div>
<embed src="/alraijordan/uploads/images/2026/08/19/763673.pdf">
<embed src="https://alrai.com/not-a-pdf.jpg">
</p></body></html>"""

ARTICLE_NO_PDFS = """<!doctype html><html><body>
<p>يمكنكم الآن تصفح إعلانات صحيفة الرأي الورقية</p>
</body></html>"""
