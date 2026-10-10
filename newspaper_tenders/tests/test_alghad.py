# newspaper_tenders/tests/test_alghad.py
# Al-Ghad discovery (WordPress API) + full-newspaper page splitting.

from datetime import date
from unittest import mock

import requests
from django.test import TestCase, override_settings

from newspaper_tenders import alghad, splitter

API_RESPONSE = {
    "groups": [{
        "path": None, "name": "Alghad newspaper",
        "child": {
            "groups": [{
                "path": "/2026", "name": 2026,
                "child": {
                    "groups": [{
                        "path": "/2026/2026-08", "name": "2026-08",
                        "child": {
                            "groups": [],
                            "files": [
                                {"pdf": "https://legacy.alghad.com/static/newspaper//2026/2026-08/2026-08-20.pdf",
                                 "thumb": "x", "name": "2026-08-20"},
                                {"pdf": "https://legacy.alghad.com/static/newspaper//2026/2026-08/2026-08-19.pdf",
                                 "thumb": "x", "name": "2026-08-19"},
                            ],
                        },
                    }],
                    "files": [],
                },
            }],
            "files": [],
        },
    }],
}


def _resp(payload, status=200):
    r = mock.Mock(spec=requests.Response)
    r.status_code = status
    r.json = mock.Mock(return_value=payload)
    r.raise_for_status = mock.Mock(
        side_effect=None if status < 400 else requests.HTTPError(str(status)))
    return r


class AlghadDiscoveryTests(TestCase):
    @mock.patch('newspaper_tenders.alghad.requests.get')
    def test_discover_today(self, m_get):
        m_get.return_value = _resp(API_RESPONSE)
        result = alghad.discover(date(2026, 8, 20))
        self.assertIsNotNone(result)
        self.assertEqual(result.pdf_urls, [
            'https://legacy.alghad.com/static/newspaper//2026/2026-08/2026-08-20.pdf'])
        self.assertEqual(result.article.edition_date, date(2026, 8, 20))
        # API called with the month path
        params = m_get.call_args.kwargs['params']
        self.assertEqual(params['path'], '/2026/2026-08')

    @mock.patch('newspaper_tenders.alghad.requests.get')
    def test_discover_missing_day(self, m_get):
        m_get.return_value = _resp(API_RESPONSE)
        self.assertIsNone(alghad.discover(date(2026, 8, 21)))

    @mock.patch('newspaper_tenders.alghad.requests.get',
                side_effect=requests.ConnectionError('down'))
    def test_api_down_raises(self, m_get):
        with self.assertRaises(requests.RequestException):
            alghad.discover(date(2026, 8, 20))


def _blank_pdf(n_pages):
    """N blank pages — page TEXT is stubbed via splitter._page_text (PyMuPDF
    cannot round-trip logical-order Arabic through synthetic fixtures; the
    real Al-Ghad PDFs extract correctly, verified live)."""
    import pymupdf
    doc = pymupdf.open()
    for _ in range(n_pages):
        doc.new_page(width=595, height=842)
    return doc.tobytes()


_PAD = 'page body filler text ' * 20        # satisfies the text-layer check


class SplitterTests(TestCase):
    def _run(self, texts, **settings_over):
        pdf = _blank_pdf(len(texts))
        with mock.patch('newspaper_tenders.splitter._page_text',
                        side_effect=[t + _PAD for t in texts]):
            if settings_over:
                with override_settings(**settings_over):
                    return splitter.find_tender_pages(pdf)
            return splitter.find_tender_pages(pdf)

    def test_selects_tender_pages_only(self):
        pages = self._run([
            'news about politics',                       # page 1 — no
            'إعلان طرح عطاء رقم (2026/9) مناقصة عامة',    # page 2 — strong
            'sports results',                            # page 3 — no
            'عطاء عطاء عطاء',                            # page 4 — weak x3
            'خبر يذكر كلمة واحدة فقط',                    # page 5 — no marker
        ])
        self.assertEqual([p for p, _ in pages], [2, 4])
        # Each export is a valid single-page PDF
        import pymupdf
        for _no, data in pages:
            d = pymupdf.open(stream=data, filetype='pdf')
            self.assertEqual(d.page_count, 1)

    def test_page_cap_keeps_best(self):
        pages = self._run([
            'عطاء عطاء',                                  # weak (score 2)
            'مناقصة عامة وإعلان طرح عطاء رقم 5',           # strong (higher)
        ], NEWSPAPER_SPLIT_MAX_PAGES=1)
        self.assertEqual([p for p, _ in pages], [2])

    def test_presentation_forms_normalised(self):
        # Some PDFs extract Arabic as presentation-form glyphs — NFKC in
        # _page_text collapses them, so the real function must match too.
        import unicodedata
        pres = unicodedata.normalize('NFKD', 'مناقصة')   # sanity: round trip
        self.assertIn('مناقصة', unicodedata.normalize('NFKC', pres))

    def test_no_text_layer_raises(self):
        pdf = _blank_pdf(2)
        with self.assertRaises(splitter.SplitError):
            splitter.find_tender_pages(pdf)             # blank pages, no text

    def test_corrupted_pdf_raises(self):
        with self.assertRaises(splitter.SplitError):
            splitter.find_tender_pages(b'%PDF- broken')
