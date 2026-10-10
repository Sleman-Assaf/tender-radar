# newspaper_tenders/tests/test_page_gate.py
# The court-page gate: an Al-Rai page headed "إعلانات قضائية" is never sent
# to the AI, and the rows the AI invented from such pages can be purged.

import base64
from datetime import date, timedelta
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings

from tenders.models import Tender

from newspaper_tenders import alrai, page_gate, pipeline, sources
from newspaper_tenders.models import (NewspaperEdition, NewspaperEditionFile,
                                      NewspaperTenderMeta)

TARGET = date(2026, 10, 5)
FUTURE = (TARGET + timedelta(days=20)).isoformat()
ADS_URL   = 'https://alrai.com/alraijordan/uploads/images/2026/10/05/775087.pdf'
COURT_URL = 'https://alrai.com/alraijordan/uploads/images/2026/10/05/775093.pdf'
ADS_PDF   = b'%PDF-1.4 ads page'
COURT_PDF = b'%PDF-1.4 court page'

# The headers EasyOCR returned for the fifteen pages of the 5 October 2026
# edition (eight court pages, six ads pages, one local-news page).
REAL_HEADERS = {
    'إعلانات قضائية': True, 'إعلانات قضائية «': True,
    'إعلانات': False, 'اعلانات': False, 'محليات': False,
}


def _ai_tender(**over):
    base = {
        'title': 'دعوة عطاء', 'title_ar': 'دعوة عطاء',
        'entity': 'وزارة الزراعة', 'entity_ar': 'وزارة الزراعة',
        'description': '', 'description_ar': '', 'reference_no': '',
        'submission_deadline': FUTURE, 'opening_date': None,
        'confidence': 0.9, 'page_number': 1, 'raw_text': '',
        'ad_image_b64': base64.b64encode(b'\xff\xd8\xff\xe0fake').decode(),
    }
    base.update(over)
    return base


class HeaderDecisionTests(TestCase):
    def test_the_real_headers_of_the_5_october_edition(self):
        for text, is_court in REAL_HEADERS.items():
            self.assertEqual(page_gate.header_reads_as_court(text), is_court, text)

    def test_ocr_slips_still_read_as_court(self):
        self.assertTrue(page_gate.header_reads_as_court('اعلانات قضاييه'))
        self.assertTrue(page_gate.header_reads_as_court('إعلانات  قضائيه 27'))

    def test_empty_or_tender_words_never_read_as_court(self):
        for text in ('', None, 'عطاءات', 'دعوة عطاء رقم 12', 'محكمة بداية حقوق عمان'):
            self.assertFalse(page_gate.header_reads_as_court(text), repr(text))

    def test_classify_page_is_blank_when_the_header_cannot_be_read(self):
        with mock.patch.object(page_gate, 'available', return_value=True), \
             mock.patch.object(page_gate, 'header_text', side_effect=RuntimeError('no ocr')):
            self.assertEqual(page_gate.classify_page(COURT_PDF), '')

    def test_classify_page_is_blank_without_the_engines(self):
        with mock.patch.object(page_gate, 'available', return_value=False):
            self.assertEqual(page_gate.classify_page(COURT_PDF), '')

    def test_classify_page_reads_court(self):
        with mock.patch.object(page_gate, 'available', return_value=True), \
             mock.patch.object(page_gate, 'header_text', return_value='إعلانات قضائية'):
            self.assertEqual(page_gate.classify_page(COURT_PDF), page_gate.COURT)

    def test_gate_is_an_alrai_property(self):
        self.assertTrue(pipeline.court_gate_applies(sources.get_source_config('ALRAI')))
        self.assertFalse(pipeline.court_gate_applies(sources.get_source_config('ALGHAD')))
        self.assertFalse(pipeline.court_gate_applies(sources.get_source_config('DUSTOUR')))
        with override_settings(NEWSPAPER_SKIP_COURT_PAGES=False):
            self.assertFalse(pipeline.court_gate_applies(sources.get_source_config('ALRAI')))


class PipelineGateTests(TestCase):
    """The gate inside _process_file, with network, OCR and AI all mocked."""

    def run_alrai(self, verdicts):
        """verdicts: {pdf_bytes: '' | COURT} — what the header OCR says."""
        article = alrai.EditionArticle('1', 'https://alrai.com/article/1/x/y', TARGET)
        discovery = alrai.DiscoveryResult(article=article, pdf_urls=[ADS_URL, COURT_URL])
        downloads = {ADS_URL: ADS_PDF, COURT_URL: COURT_PDF}

        def fake_extract(pdf_bytes, **kw):
            return [_ai_tender(entity=f'entity for {pdf_bytes[-9:].decode()}')]

        with mock.patch.object(pipeline.alrai, 'discover', return_value=discovery), \
             mock.patch.object(pipeline.intake, 'download_pdf',
                               side_effect=lambda url, referer='': downloads[url]), \
             mock.patch.object(page_gate, 'classify_page',
                               side_effect=lambda data: verdicts.get(data, '')) as gate, \
             mock.patch.object(pipeline.ai_client, 'extract_tenders',
                               side_effect=fake_extract) as extract, \
             mock.patch.object(pipeline.ai_client, 'extract_from_image', return_value=[]), \
             mock.patch('newspaper_tenders.photo_verify.available', return_value=False), \
             mock.patch('tenders.ajibrain_client.classify_and_translate_tender',
                        return_value={'is_aec_checked': True, 'is_aec': True,
                                      'category': 'BUILDING'}):
            result = pipeline.run_for_date(TARGET, 'ALRAI')
        return result, gate, extract

    def test_a_court_page_is_closed_unread(self):
        result, gate, extract = self.run_alrai({COURT_PDF: page_gate.COURT})
        self.assertEqual(result, 'done')
        # the AI saw the ads page only
        self.assertEqual(extract.call_count, 1)
        self.assertEqual(extract.call_args.args[0], ADS_PDF)
        court = NewspaperEditionFile.objects.get(pdf_url=COURT_URL)
        self.assertEqual(court.status, NewspaperEditionFile.Status.DONE)
        self.assertEqual(court.error_message, page_gate.SKIP_NOTE)
        self.assertEqual(court.tenders_found, 0)
        self.assertTrue(court.file_hash)            # downloaded and hashed first
        # one tender, from the ads page
        self.assertEqual(Tender.objects.count(), 1)
        self.assertEqual(NewspaperTenderMeta.objects.get().edition_file.pdf_url, ADS_URL)
        self.assertEqual(NewspaperEdition.objects.get().status,
                         NewspaperEdition.Status.DONE)

    def test_every_page_is_read_when_no_header_says_court(self):
        _, gate, extract = self.run_alrai({})
        self.assertEqual(gate.call_count, 2)
        self.assertEqual(extract.call_count, 2)
        self.assertEqual(Tender.objects.count(), 2)

    @override_settings(NEWSPAPER_SKIP_COURT_PAGES=False)
    def test_the_setting_switches_the_gate_off(self):
        _, gate, extract = self.run_alrai({COURT_PDF: page_gate.COURT})
        self.assertEqual(gate.call_count, 0)
        self.assertEqual(extract.call_count, 2)

    def test_a_skipped_page_is_judged_again_on_reprocess(self):
        # Re-process resets the rows to PENDING; the page is downloaded,
        # hashed and gated again (its own DONE row is not a "duplicate").
        self.run_alrai({COURT_PDF: page_gate.COURT})
        NewspaperEditionFile.objects.update(status=NewspaperEditionFile.Status.PENDING)
        _, gate, extract = self.run_alrai({COURT_PDF: page_gate.COURT})
        self.assertEqual(gate.call_count, 2)
        self.assertEqual(extract.call_count, 1)
        self.assertEqual(Tender.objects.count(), 1)


class PurgeCommandTests(TestCase):
    def setUp(self):
        self.edition = NewspaperEdition.objects.create(
            source='ALRAI', edition_date=TARGET,
            status=NewspaperEdition.Status.DONE)
        self.court = NewspaperEditionFile.objects.create(
            edition=self.edition, pdf_url=COURT_URL,
            status=NewspaperEditionFile.Status.DONE, tenders_found=2)
        self.ads = NewspaperEditionFile.objects.create(
            edition=self.edition, pdf_url=ADS_URL,
            status=NewspaperEditionFile.Status.DONE, tenders_found=1)
        source = pipeline.get_newspaper_source('ALRAI')
        for i, (f, title) in enumerate([(self.court, 'دعوة عطاء'),
                                        (self.court, 'إخطار صادر عن دائرة التنفيذ'),
                                        (self.ads, 'إعادة طرح العطاء رقم 21/2026')]):
            t = Tender.objects.create(source=source, external_id=f'alrai-{i}',
                                      title=title, entity='x', country=source.country,
                                      source_url=f.pdf_url, publication_date=TARGET)
            NewspaperTenderMeta.objects.create(tender=t, edition=self.edition,
                                               edition_file=f, original_pdf_url=f.pdf_url)

    def run_cmd(self, *args, verdicts):
        out = StringIO()
        with mock.patch.object(page_gate, 'available', return_value=True), \
             mock.patch('newspaper_tenders.intake.download_pdf',
                        side_effect=lambda url, referer='': url.encode()), \
             mock.patch.object(page_gate, 'classify_page',
                               side_effect=lambda data: verdicts.get(data.decode(), '')):
            call_command('purge_court_page_tenders', *args, stdout=out)
        return out.getvalue()

    def test_dry_run_reports_and_deletes_nothing(self):
        out = self.run_cmd(verdicts={COURT_URL: page_gate.COURT})
        self.assertIn('2 fabricated row(s)', out)
        self.assertIn('دعوة عطاء', out)
        self.assertEqual(Tender.objects.count(), 3)

    def test_apply_deletes_the_court_page_rows_only(self):
        out = self.run_cmd('--apply', verdicts={COURT_URL: page_gate.COURT})
        self.assertIn('rows deleted: 2', out)
        self.assertEqual(list(Tender.objects.values_list('title', flat=True)),
                         ['إعادة طرح العطاء رقم 21/2026'])
        self.court.refresh_from_db()
        self.assertTrue(self.court.error_message.startswith(page_gate.SKIP_NOTE))
        self.assertEqual(self.court.tenders_found, 0)
        self.ads.refresh_from_db()
        self.assertEqual(self.ads.tenders_found, 1)

    def test_a_page_already_judged_by_the_gate_is_not_downloaded(self):
        self.court.error_message = page_gate.SKIP_NOTE
        self.court.save()
        with mock.patch('newspaper_tenders.intake.download_pdf') as dl:
            dl.side_effect = lambda url, referer='': url.encode()
            out = StringIO()
            with mock.patch.object(page_gate, 'available', return_value=True), \
                 mock.patch.object(page_gate, 'classify_page', return_value=''):
                call_command('purge_court_page_tenders', '--apply', stdout=out)
        self.assertEqual([c.args[0] for c in dl.call_args_list], [ADS_URL])
        self.assertEqual(Tender.objects.count(), 1)

    def test_an_undownloadable_page_is_left_alone(self):
        from newspaper_tenders import intake
        out = StringIO()
        with mock.patch.object(page_gate, 'available', return_value=True), \
             mock.patch('newspaper_tenders.intake.download_pdf',
                        side_effect=intake.DownloadError('HTTP 404')):
            call_command('purge_court_page_tenders', '--apply', stdout=out)
        self.assertIn('unreadable pages: 2', out.getvalue())
        self.assertEqual(Tender.objects.count(), 3)
