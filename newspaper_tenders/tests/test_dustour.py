# newspaper_tenders/tests/test_dustour.py
# Ad-Dustour discovery (flip-book archive panel).

from datetime import date
from unittest import mock

import requests
from django.test import TestCase

from newspaper_tenders import dustour

PANEL_HTML = """
<div id="mySidepanel" class="sidepanel">
  <a href="/pdf-viewer/pdf-flip-book-viewer.php?fileid=768213"
     title="الخميس 7 ربيع الأول 1448 هـ الموافق 20 آب 2026م العدد 21188">x</a>
  <a href="/pdf-viewer/pdf-flip-book-viewer.php?fileid=768009"
     title="الاربعاء 6 ربيع الأول 1448 هـ الموافق 19 آب 2026م العدد 21187">x</a>
  <a href="/pdf-viewer/pdf-flip-book-viewer.php?fileid=767772"
     title="الثلاثاء 5 ربيع الأول 1448 هـ الموافق 18 آب 2026م العدد 21186">x</a>
  <a href="/pdf-viewer/pdf-flip-book-viewer.php?fileid=999999"
     title="عنوان بدون تاريخ ميلادي">x</a>
</div>
"""


def _resp(text, status=200):
    r = mock.Mock(spec=requests.Response)
    r.status_code = status
    r.text = text
    r.raise_for_status = mock.Mock(
        side_effect=None if status < 400 else requests.HTTPError(str(status)))
    return r


class DustourTests(TestCase):
    def test_parse_title_date(self):
        self.assertEqual(
            dustour.parse_title_date('الخميس 7 ربيع الأول 1448 هـ الموافق 20 آب 2026م العدد 21188'),
            date(2026, 8, 20))
        # Hijri month must NOT be picked up; Gregorian follows الموافق only
        self.assertEqual(
            dustour.parse_title_date('السبت 1 محرم 1448 هـ الموافق 5 تشرين الاول 2026م'),
            date(2026, 10, 5))
        self.assertIsNone(dustour.parse_title_date('عنوان بدون تاريخ'))

    @mock.patch('newspaper_tenders.dustour.requests.get')
    def test_list_editions(self, m_get):
        m_get.return_value = _resp(PANEL_HTML)
        eds = dustour.list_editions()
        self.assertEqual([(d.isoformat(), f) for d, f in eds], [
            ('2026-08-20', '768213'),
            ('2026-08-19', '768009'),
            ('2026-08-18', '767772'),
        ])

    @mock.patch('newspaper_tenders.dustour.requests.get')
    def test_discover_today(self, m_get):
        m_get.return_value = _resp(PANEL_HTML)
        result = dustour.discover(date(2026, 8, 20))
        self.assertIsNotNone(result)
        self.assertEqual(result.pdf_urls, [
            'https://www.addustour.com/pdf-viewer/download-pdf.php?fileid=768213'])
        self.assertIn('fileid=768213', result.article.url)

    @mock.patch('newspaper_tenders.dustour.requests.get')
    def test_discover_missing_day(self, m_get):
        m_get.return_value = _resp(PANEL_HTML)
        self.assertIsNone(dustour.discover(date(2026, 8, 15)))

    @mock.patch('newspaper_tenders.dustour.requests.get',
                side_effect=requests.ConnectionError('down'))
    def test_site_down_raises(self, m_get):
        with self.assertRaises(requests.RequestException):
            dustour.discover(date(2026, 8, 20))
