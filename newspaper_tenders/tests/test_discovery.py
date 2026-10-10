# newspaper_tenders/tests/test_discovery.py

from datetime import date
from unittest import mock

import requests
from django.test import TestCase

from newspaper_tenders import alrai

from .fixtures_html import ARTICLE_NO_PDFS, ARTICLE_WITH_PDFS, SECTION_PAGE


def _resp(text, status=200):
    r = mock.Mock(spec=requests.Response)
    r.status_code = status
    r.text = text
    r.raise_for_status = mock.Mock(
        side_effect=None if status < 400 else requests.HTTPError(f'HTTP {status}'))
    return r


class SlugDateTests(TestCase):
    def test_parses_plain_alef_month(self):
        href = ('https://alrai.com/article/10972360/x/'
                'يمكنكم-الآن-تصفح-إعلانات-صحيفة-الرأي-الورقية-لليوم-19-اب-2026-عبر-الموقع')
        self.assertEqual(alrai.parse_slug_date(href), date(2026, 8, 19))

    def test_parses_madda_month(self):
        href = 'https://alrai.com/article/1/x/لليوم-13-آب-2026-عبر'
        self.assertEqual(alrai.parse_slug_date(href), date(2026, 8, 13))

    def test_parses_two_word_month(self):
        href = 'https://alrai.com/article/1/x/لليوم-5-تشرين-الاول-2026-عبر'
        self.assertEqual(alrai.parse_slug_date(href), date(2026, 10, 5))

    def test_parses_percent_encoded(self):
        import urllib.parse
        slug = urllib.parse.quote('لليوم-19-اب-2026-عبر')
        href = f'https://alrai.com/article/1/x/{slug}'
        self.assertEqual(alrai.parse_slug_date(href), date(2026, 8, 19))

    def test_rejects_undated_slug(self):
        self.assertIsNone(alrai.parse_slug_date('https://alrai.com/article/999/x/عنوان-بدون-تاريخ'))


class DiscoveryTests(TestCase):
    @mock.patch('newspaper_tenders.alrai._get')
    def test_lists_dated_articles(self, m_get):
        m_get.return_value = _resp(SECTION_PAGE)
        session = mock.Mock()
        articles = alrai.list_edition_articles(session)
        dates = [a.edition_date for a in articles]
        self.assertEqual(dates, [date(2026, 8, 19), date(2026, 8, 18), date(2026, 8, 13)])

    @mock.patch('newspaper_tenders.alrai._get')
    def test_find_edition_today_exists(self, m_get):
        m_get.return_value = _resp(SECTION_PAGE)
        art = alrai.find_edition_for_date(mock.Mock(), date(2026, 8, 19))
        self.assertIsNotNone(art)
        self.assertEqual(art.article_id, '10972360')

    @mock.patch('newspaper_tenders.alrai._get')
    def test_find_edition_today_missing(self, m_get):
        m_get.return_value = _resp(SECTION_PAGE)
        # A weekend day with no edition
        self.assertIsNone(alrai.find_edition_for_date(mock.Mock(), date(2026, 8, 14)))

    @mock.patch('newspaper_tenders.alrai._get')
    def test_extract_pdf_urls_dedup_and_relative(self, m_get):
        m_get.return_value = _resp(ARTICLE_WITH_PDFS)
        art = alrai.EditionArticle('10972357', 'https://alrai.com/article/10972357/x/y',
                                   date(2026, 8, 18))
        urls = alrai.extract_pdf_urls(mock.Mock(), art)
        self.assertEqual(urls, [
            'https://alrai.com/alraijordan/uploads/images/2026/08/19/763671.pdf',
            'https://alrai.com/alraijordan/uploads/images/2026/08/19/763672.pdf',
            'https://alrai.com/alraijordan/uploads/images/2026/08/19/763673.pdf',
        ])  # duplicate collapsed, relative resolved, non-PDF ignored

    @mock.patch('newspaper_tenders.alrai._get')
    def test_article_without_pdfs_is_empty_not_error(self, m_get):
        m_get.return_value = _resp(ARTICLE_NO_PDFS)
        art = alrai.EditionArticle('10972360', 'https://alrai.com/article/10972360/x/y',
                                   date(2026, 8, 19))
        self.assertEqual(alrai.extract_pdf_urls(mock.Mock(), art), [])

    @mock.patch('newspaper_tenders.alrai._get',
                side_effect=requests.ConnectionError('cloudflare says no'))
    def test_site_unavailable_raises(self, m_get):
        with self.assertRaises(requests.RequestException):
            alrai.list_edition_articles(mock.Mock())

    def test_headers_use_realistic_ua(self):
        h = alrai._headers()
        self.assertIn('Mozilla', h['User-Agent'])
        self.assertEqual(h['Referer'], 'https://alrai.com/')
