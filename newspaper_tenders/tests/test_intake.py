# newspaper_tenders/tests/test_intake.py

import hashlib
from unittest import mock

import requests
from django.test import TestCase, override_settings

from newspaper_tenders import intake

PDF = b'%PDF-1.4 minimal test bytes'


def _resp(status=200, content=PDF):
    r = mock.Mock(spec=requests.Response)
    r.status_code = status
    r.content = content
    return r


class DownloadTests(TestCase):
    @mock.patch('newspaper_tenders.intake.requests.get')
    def test_download_success(self, m_get):
        m_get.return_value = _resp()
        self.assertEqual(intake.download_pdf('https://alrai.com/x.pdf'), PDF)
        # Realistic headers actually sent
        headers = m_get.call_args.kwargs['headers']
        self.assertIn('Mozilla', headers['User-Agent'])
        self.assertEqual(headers['Referer'], 'https://alrai.com/')

    @mock.patch('newspaper_tenders.intake.time.sleep')
    @mock.patch('newspaper_tenders.intake.requests.get')
    def test_download_retries_5xx_then_succeeds(self, m_get, _sleep):
        m_get.side_effect = [_resp(status=503), _resp()]
        self.assertEqual(intake.download_pdf('https://alrai.com/x.pdf'), PDF)
        self.assertEqual(m_get.call_count, 2)

    @mock.patch('newspaper_tenders.intake.time.sleep')
    @mock.patch('newspaper_tenders.intake.requests.get')
    def test_download_gives_up_after_retries(self, m_get, _sleep):
        m_get.side_effect = requests.ConnectionError('reset')
        with self.assertRaises(intake.DownloadError):
            intake.download_pdf('https://alrai.com/x.pdf')

    @mock.patch('newspaper_tenders.intake.requests.get')
    def test_permanent_404_no_retry(self, m_get):
        m_get.return_value = _resp(status=404)
        with self.assertRaises(intake.DownloadError):
            intake.download_pdf('https://alrai.com/gone.pdf')
        self.assertEqual(m_get.call_count, 1)


class ValidateTests(TestCase):
    def test_valid_pdf_passes(self):
        intake.validate_pdf(PDF)   # no exception

    def test_empty_rejected(self):
        with self.assertRaises(intake.InvalidPdfError):
            intake.validate_pdf(b'')

    def test_bad_signature_rejected(self):
        with self.assertRaises(intake.InvalidPdfError):
            intake.validate_pdf(b'<html>Cloudflare challenge page</html>')

    @override_settings(NEWSPAPER_MAX_FILE_SIZE=5)
    def test_oversize_rejected(self):
        with self.assertRaises(intake.InvalidPdfError):
            intake.validate_pdf(PDF)

    def test_sha256(self):
        self.assertEqual(intake.sha256_hex(PDF),
                         hashlib.sha256(PDF).hexdigest())
