# newspaper_tenders/tests/test_pipeline.py
# End-to-end pipeline tests with mocked network + AI (no Ollama, no Al-Rai).

from datetime import date, timedelta
from unittest import mock

from django.test import TestCase

from tenders.models import CountryConfig, Tender

from newspaper_tenders import ai_client, alrai, intake, photo_verify, pipeline
from newspaper_tenders.models import (NewspaperEdition, NewspaperEditionFile,
                                      NewspaperTenderMeta)

TARGET = date(2026, 8, 19)
FUTURE = (TARGET + timedelta(days=20)).isoformat()

PDF1 = b'%PDF-1.4 page one bytes'
PDF2 = b'%PDF-1.4 page two bytes'

URL1 = 'https://alrai.com/alraijordan/uploads/images/2026/08/19/763671.pdf'
URL2 = 'https://alrai.com/alraijordan/uploads/images/2026/08/19/763672.pdf'


def _article():
    return alrai.EditionArticle('10972360',
                                'https://alrai.com/article/10972360/x/y', TARGET)


def _discovery(urls):
    return alrai.DiscoveryResult(article=_article(), pdf_urls=urls)


def ai_tender(**over):
    # What the AI extraction service returns after the rewire: NO is_aec, NO
    # category — classification is TenderRadar's standard enrichment job.
    base = {
        'title': 'دعوة عطاء رقم 12/2026 - أعمال إشراف هندسي على مبنى المحكمة',
        'title_ar': 'دعوة عطاء رقم 12/2026 - أعمال إشراف هندسي على مبنى المحكمة',
        'entity': 'وزارة الأشغال العامة والإسكان',
        'entity_ar': 'وزارة الأشغال العامة والإسكان',
        'description': 'إشراف هندسي', 'description_ar': 'إشراف هندسي',
        'reference_no': 'عطاء رقم 12/2026',
        'submission_deadline': FUTURE, 'opening_date': None,
        'confidence': 0.93, 'page_number': 1,
        'raw_text': 'النص الكامل',
    }
    base.update(over)
    return base


# What the mocked standard enrichment (classify_and_translate_tender) returns
# by default in these tests — same dict shape as the real client.
DEFAULT_ENRICH = {'is_aec_checked': True, 'is_aec': True, 'category': 'BUILDING'}


class PipelineTests(TestCase):
    def run_pipeline(self, discovery, downloads, ai_results,
                     enrich_updates=None, reread_results=None):
        """
        downloads:  {url: bytes | Exception}
        ai_results: {url: list | Exception}
        enrich_updates: dict returned by the mocked standard enrichment
                        (tenders.ajibrain_client.classify_and_translate_tender)
        reread_results: list | Exception returned by the mocked zoom re-read
                        (default [] = "nothing readable, keep page pass")
        """
        def fake_download(url, referer=''):
            v = downloads[url]
            if isinstance(v, Exception):
                raise v
            return v

        def fake_extract(pdf_bytes, *, edition_date, source_url, pdf_url,
                         page_number=1):
            v = ai_results[pdf_url]
            if isinstance(v, Exception):
                raise v
            return v

        def fake_reread(image_b64, *, edition_date, page_number=1):
            if isinstance(reread_results, Exception):
                raise reread_results
            return reread_results if reread_results is not None else []

        with mock.patch.object(pipeline.alrai, 'discover', return_value=discovery), \
             mock.patch.object(pipeline.intake, 'download_pdf', side_effect=fake_download), \
             mock.patch.object(pipeline.ai_client, 'extract_tenders', side_effect=fake_extract), \
             mock.patch.object(pipeline.ai_client, 'extract_from_image', side_effect=fake_reread), \
             mock.patch('tenders.ajibrain_client.classify_and_translate_tender',
                        return_value=dict(enrich_updates or DEFAULT_ENRICH)) as m_enrich:
            result = pipeline.run_for_date(TARGET)
        self.last_enrich_mock = m_enrich
        return result

    # ── Discovery outcomes ──────────────────────────────────────────────────
    def test_no_edition_is_success(self):
        with mock.patch.object(pipeline.alrai, 'discover', return_value=None):
            self.assertEqual(pipeline.run_for_date(TARGET), 'no-edition')
        ed = NewspaperEdition.objects.get(edition_date=TARGET)
        self.assertEqual(ed.status, NewspaperEdition.Status.DISCOVERED)

    def test_article_without_pdfs_is_pending(self):
        result = self.run_pipeline(_discovery([]), {}, {})
        self.assertEqual(result, 'pending-pdfs')
        ed = NewspaperEdition.objects.get(edition_date=TARGET)
        self.assertEqual(ed.status, NewspaperEdition.Status.PENDING_PDFS)

    def test_discovery_failure_marks_failed(self):
        with mock.patch.object(pipeline.alrai, 'discover',
                               side_effect=RuntimeError('cloudflare')):
            self.assertEqual(pipeline.run_for_date(TARGET), 'discovery-failed')
        ed = NewspaperEdition.objects.get(edition_date=TARGET)
        self.assertEqual(ed.status, NewspaperEdition.Status.FAILED)

    # ── Happy path ──────────────────────────────────────────────────────────
    def test_full_success_saves_tenders_and_meta(self):
        result = self.run_pipeline(
            _discovery([URL1, URL2]),
            {URL1: PDF1, URL2: PDF2},
            {URL1: [ai_tender(),
                    ai_tender(title='توريد مركبات', title_ar='توريد مركبات')],
             URL2: []},
        )
        self.assertEqual(result, 'done')

        ed = NewspaperEdition.objects.get(edition_date=TARGET)
        self.assertEqual(ed.status, NewspaperEdition.Status.DONE)
        self.assertEqual(ed.pdf_count, 2)
        self.assertEqual(ed.files.filter(
            status=NewspaperEditionFile.Status.DONE).count(), 2)

        tenders = Tender.objects.filter(source__scraper_class='NEWSPAPER_ALRAI')
        self.assertEqual(tenders.count(), 2)

        # The STANDARD enrichment service classified both — once per tender,
        # exactly like scraped tenders.
        self.assertEqual(self.last_enrich_mock.call_count, 2)
        for t in tenders:
            self.assertTrue(t.is_aec_checked)
            self.assertTrue(t.is_aec)                     # from mocked enrichment
            self.assertEqual(t.category, 'BUILDING')      # from mocked enrichment
            self.assertEqual(t.country_id, 'JO')
            self.assertEqual(t.title_ar, t.title)         # Arabic copy behaviour
            self.assertTrue(t.external_id.startswith('alrai-'))

        aec = tenders.exclude(title='توريد مركبات').get()
        meta = NewspaperTenderMeta.objects.get(tender=aec)
        self.assertEqual(meta.edition, ed)
        self.assertEqual(meta.original_pdf_url, URL1)
        self.assertEqual(meta.ai_confidence, 0.93)
        self.assertEqual(meta.raw_extracted_data['raw_text'], 'النص الكامل')

    def test_list_ad_merges_into_one_row_with_all_refs_in_description(self):
        # One newspaper ad can list dozens of tenders sharing title/entity/
        # deadline and differing only by tender number (NEPCO announcement,
        # 24 Aug 2026). Management decision: ONE row per ad — the individual
        # tender numbers preserved in the description, not as separate rows.
        rows = [ai_tender(reference_no=f'2026/{n}',
                          description_ar=f'وصف العطاء {n}')
                for n in range(19, 25)]
        self.run_pipeline(_discovery([URL1]), {URL1: PDF1}, {URL1: rows})
        tenders = Tender.objects.filter(source__scraper_class='NEWSPAPER_ALRAI')
        self.assertEqual(tenders.count(), 1)
        t = tenders.get()
        for n in range(19, 25):
            self.assertIn(f'2026/{n}', t.description)
        self.assertIn('2026/19', t.reference_no)
        self.assertIn('2026/24', t.reference_no)
        self.assertIn('6', t.reference_no)  # count of merged tenders

    def test_zoom_reread_corrects_misread_title(self):
        # Page pass misreads a decorative headline; the full-resolution
        # re-read of the ad's own crop overrides it (24 Aug 2026 live case:
        # استئجار مبنى بديل read as استشاري مبدئي لبدائل).
        import base64
        jpeg = base64.b64encode(b'\xff\xd8\xff\xe0fake').decode()
        misread = ai_tender(title='طلب استشاري مبدئي لبدائل مركز التنمية',
                            title_ar='طلب استشاري مبدئي لبدائل مركز التنمية',
                            ad_image_b64=jpeg)
        corrected = ai_tender(title='طلب استئجار مبنى بديل لمركز التنمية',
                              title_ar='طلب استئجار مبنى بديل لمركز التنمية')
        self.run_pipeline(_discovery([URL1]), {URL1: PDF1},
                          {URL1: [misread]}, reread_results=[corrected])
        t = Tender.objects.get(source__scraper_class='NEWSPAPER_ALRAI')
        self.assertIn('استئجار مبنى بديل', t.title)
        # photo stays from the page pass
        self.assertTrue(t.newspaper_meta.ad_image)

    def test_zoom_reread_failure_keeps_page_pass_record(self):
        import base64
        from newspaper_tenders.ai_client import AIServiceError
        jpeg = base64.b64encode(b'\xff\xd8\xff\xe0fake').decode()
        self.run_pipeline(_discovery([URL1]), {URL1: PDF1},
                          {URL1: [ai_tender(ad_image_b64=jpeg)]},
                          reread_results=AIServiceError('down'))
        self.assertEqual(Tender.objects.filter(
            source__scraper_class='NEWSPAPER_ALRAI').count(), 1)

    def test_zoom_reread_multiple_items_ignored(self):
        # A crop containing a list ad returns many items — the merged
        # page-pass record must stand untouched.
        import base64
        jpeg = base64.b64encode(b'\xff\xd8\xff\xe0fake').decode()
        original = ai_tender(ad_image_b64=jpeg)
        self.run_pipeline(_discovery([URL1]), {URL1: PDF1},
                          {URL1: [original]},
                          reread_results=[ai_tender(title='أ'), ai_tender(title='ب')])
        t = Tender.objects.get(source__scraper_class='NEWSPAPER_ALRAI')
        self.assertEqual(t.title, original['title'])

    def test_distinct_ads_on_one_page_stay_separate(self):
        # Different ads (different titles) on the same page must NOT merge.
        self.run_pipeline(
            _discovery([URL1]), {URL1: PDF1},
            {URL1: [ai_tender(),
                    ai_tender(title='توريد مركبات', title_ar='توريد مركبات')]})
        self.assertEqual(Tender.objects.filter(
            source__scraper_class='NEWSPAPER_ALRAI').count(), 2)

    def test_ad_photo_saved_from_ai_response(self):
        # The AI's cropped ad photo must survive the final-gate whitelist and
        # land as a stored file on the tender's newspaper meta.
        import base64
        jpeg = b'\xff\xd8\xff\xe0fakejpegbytes'
        self.run_pipeline(
            _discovery([URL1]), {URL1: PDF1},
            {URL1: [ai_tender(ad_image_b64=base64.b64encode(jpeg).decode())]})
        meta = NewspaperTenderMeta.objects.get()
        self.assertTrue(meta.ad_image)                       # file saved
        meta.ad_image.open('rb')
        self.assertEqual(meta.ad_image.read(), jpeg)
        meta.ad_image.close()
        # …and the bulky base64 is NOT duplicated into the audit JSON
        self.assertNotIn('ad_image_b64', meta.raw_extracted_data)

    def test_reprocess_replaces_photo_but_never_removes_it(self):
        import base64
        old_jpeg = b'\xff\xd8\xff\xe0old-photo'
        new_jpeg = b'\xff\xd8\xff\xe0new-sharper-photo'
        b64 = lambda b: base64.b64encode(b).decode()

        # First run stores the old photo
        self.run_pipeline(_discovery([URL1]), {URL1: PDF1},
                          {URL1: [ai_tender(ad_image_b64=b64(old_jpeg))]})
        meta = NewspaperTenderMeta.objects.get()

        # Re-process (file reset) returns a NEW photo → replaced
        meta.edition_file.status = NewspaperEditionFile.Status.PENDING
        meta.edition_file.save()
        self.run_pipeline(_discovery([URL1]), {URL1: PDF1},
                          {URL1: [ai_tender(ad_image_b64=b64(new_jpeg))]})
        meta.refresh_from_db()
        meta.ad_image.open('rb')
        self.assertEqual(meta.ad_image.read(), new_jpeg)
        meta.ad_image.close()

        # Re-process where the AI finds NO photo → the old one is KEPT
        meta.edition_file.status = NewspaperEditionFile.Status.PENDING
        meta.edition_file.save()
        self.run_pipeline(_discovery([URL1]), {URL1: PDF1},
                          {URL1: [ai_tender(ad_image_b64='')]})
        meta.refresh_from_db()
        self.assertTrue(meta.ad_image)
        meta.ad_image.open('rb')
        self.assertEqual(meta.ad_image.read(), new_jpeg)
        meta.ad_image.close()

    def test_non_aec_enrichment_result_respected(self):
        # A non-AEC classification is respected (saved but hidden) — as long as
        # the tender HAS a usable photo (a photoless non-AEC row is dropped; see
        # the dedicated tests below). Give it a good crop so it survives.
        import base64
        jpeg = b'\xff\xd8\xff\xe0good-full-crop'
        with mock.patch('newspaper_tenders.photo_verify.available', return_value=True), \
             mock.patch('newspaper_tenders.photo_verify.photo_belongs_to_tender',
                        return_value=True), \
             mock.patch('newspaper_tenders.photo_verify.crop_is_degenerate',
                        return_value=False):
            self.run_pipeline(_discovery([URL1]), {URL1: PDF1},
                              {URL1: [ai_tender(ad_image_b64=base64.b64encode(jpeg).decode())]},
                              enrich_updates={'is_aec_checked': True,
                                              'is_aec': False,
                                              'category': 'OTHER'})
        t = Tender.objects.get()
        self.assertFalse(t.is_aec)                        # audited, hidden
        self.assertTrue(t.is_aec_checked)

    def test_nonaec_with_dropped_half_photo_is_deleted_entirely(self):
        # A Non-AEC tender whose only crop is a degenerate half-strip has no
        # photo worth showing → the WHOLE row (text included) is dropped and it
        # is not counted as saved.
        import base64
        jpeg = b'\xff\xd8\xff\xe0half-strip-crop'
        with mock.patch('newspaper_tenders.photo_verify.available', return_value=True), \
             mock.patch('newspaper_tenders.photo_verify.photo_belongs_to_tender',
                        return_value=True), \
             mock.patch('newspaper_tenders.photo_verify.crop_is_degenerate',
                        return_value=True):
            self.run_pipeline(
                _discovery([URL1]), {URL1: PDF1},
                {URL1: [ai_tender(ad_image_b64=base64.b64encode(jpeg).decode())]},
                enrich_updates={'is_aec_checked': True, 'is_aec': False,
                                'category': 'OTHER'})
        self.assertEqual(Tender.objects.count(), 0)                   # text gone
        self.assertEqual(NewspaperTenderMeta.objects.count(), 0)      # meta gone

    def test_aec_with_dropped_half_photo_is_kept_text_only(self):
        # An AEC tender is ALWAYS kept even when its half-crop is dropped —
        # only the picture is removed, the text row survives.
        import base64
        jpeg = b'\xff\xd8\xff\xe0half-strip-crop'
        with mock.patch('newspaper_tenders.photo_verify.available', return_value=True), \
             mock.patch('newspaper_tenders.photo_verify.photo_belongs_to_tender',
                        return_value=True), \
             mock.patch('newspaper_tenders.photo_verify.crop_is_degenerate',
                        return_value=True):
            self.run_pipeline(
                _discovery([URL1]), {URL1: PDF1},
                {URL1: [ai_tender(ad_image_b64=base64.b64encode(jpeg).decode())]},
                enrich_updates={'is_aec_checked': True, 'is_aec': True,
                                'category': 'BUILDING'})
        t = Tender.objects.get()                    # AEC tender survives
        self.assertTrue(t.is_aec)
        meta = NewspaperTenderMeta.objects.get()
        self.assertFalse(meta.ad_image)             # but with no photo

    def test_nonaec_with_good_photo_is_kept(self):
        # Control: a Non-AEC tender with a NORMAL (non-degenerate) crop is kept
        # with its photo — the drop rule only fires on a degenerate half-strip.
        import base64
        jpeg = b'\xff\xd8\xff\xe0good-full-crop'
        with mock.patch('newspaper_tenders.photo_verify.available', return_value=True), \
             mock.patch('newspaper_tenders.photo_verify.photo_belongs_to_tender',
                        return_value=True), \
             mock.patch('newspaper_tenders.photo_verify.crop_is_degenerate',
                        return_value=False):
            self.run_pipeline(
                _discovery([URL1]), {URL1: PDF1},
                {URL1: [ai_tender(ad_image_b64=base64.b64encode(jpeg).decode())]},
                enrich_updates={'is_aec_checked': True, 'is_aec': False,
                                'category': 'OTHER'})
        t = Tender.objects.get()
        self.assertFalse(t.is_aec)
        meta = NewspaperTenderMeta.objects.get()
        self.assertTrue(meta.ad_image)              # photo kept

    def test_nonaec_with_no_crop_at_all_is_deleted(self):
        # A Non-AEC tender the AI returned with NO crop at all (never had a
        # photo) is also dropped whole — not only the degenerate-half-crop case.
        self.run_pipeline(
            _discovery([URL1]), {URL1: PDF1},
            {URL1: [ai_tender(ad_image_b64='')]},          # no photo extracted
            enrich_updates={'is_aec_checked': True, 'is_aec': False,
                            'category': 'OTHER'})
        self.assertEqual(Tender.objects.count(), 0)
        self.assertEqual(NewspaperTenderMeta.objects.count(), 0)

    def test_aec_with_no_crop_at_all_is_kept(self):
        # An AEC tender is ALWAYS kept, even with no photo at all.
        self.run_pipeline(
            _discovery([URL1]), {URL1: PDF1},
            {URL1: [ai_tender(ad_image_b64='')]},
            enrich_updates={'is_aec_checked': True, 'is_aec': True,
                            'category': 'BUILDING'})
        t = Tender.objects.get()
        self.assertTrue(t.is_aec)
        self.assertFalse(t.newspaper_meta.ad_image)     # kept, text-only

    def test_unclassified_tender_with_dropped_photo_is_never_deleted(self):
        # Safety guard: if classification has NOT run yet (is_aec_checked=False —
        # e.g. the OPENAI/OLLAMA services defer it to a later batch), a
        # degenerate-crop tender must be KEPT (text-only), NOT deleted. is_aec
        # defaults to False on an unclassified row, so without the is_aec_checked
        # guard a genuine AEC tender would be destroyed before the batch runs.
        import base64
        jpeg = b'\xff\xd8\xff\xe0half-strip-crop'
        with mock.patch('newspaper_tenders.photo_verify.available', return_value=True), \
             mock.patch('newspaper_tenders.photo_verify.photo_belongs_to_tender',
                        return_value=True), \
             mock.patch('newspaper_tenders.photo_verify.crop_is_degenerate',
                        return_value=True):
            self.run_pipeline(
                _discovery([URL1]), {URL1: PDF1},
                {URL1: [ai_tender(ad_image_b64=base64.b64encode(jpeg).decode())]},
                enrich_updates={'is_aec_checked': False, 'is_aec': False})
        t = Tender.objects.get()                    # kept — classification pending
        self.assertFalse(t.is_aec_checked)
        meta = NewspaperTenderMeta.objects.get()
        self.assertFalse(meta.ad_image)             # only the half-photo dropped

    def test_same_nonaec_dropped_tender_in_two_files_counts_once(self):
        # The whole-row delete defeats the DB `already` check, so the same
        # non-AEC degenerate tender appearing in TWO files of one edition must
        # not be re-created/re-dropped — the second copy is a duplicate.
        import base64
        jpeg = b'\xff\xd8\xff\xe0half-strip-crop'
        counters_seen = {}
        real_summary = pipeline.RunCounters.summary

        def capture(self, *a, **k):
            counters_seen['dropped'] = self.dropped_non_aec_no_photo
            counters_seen['photos_dropped'] = self.photos_dropped
            counters_seen['duplicates'] = self.duplicates
            return real_summary(self, *a, **k)

        with mock.patch('newspaper_tenders.photo_verify.available', return_value=True), \
             mock.patch('newspaper_tenders.photo_verify.photo_belongs_to_tender',
                        return_value=True), \
             mock.patch('newspaper_tenders.photo_verify.crop_is_degenerate',
                        return_value=True), \
             mock.patch.object(pipeline.RunCounters, 'summary', capture):
            # Same tender (same title/entity/deadline → same external_id) in two
            # DISTINCT files of the one edition.
            self.run_pipeline(
                _discovery([URL1, URL2]), {URL1: PDF1, URL2: PDF2},
                {URL1: [ai_tender(ad_image_b64=base64.b64encode(jpeg).decode())],
                 URL2: [ai_tender(ad_image_b64=base64.b64encode(jpeg).decode())]},
                enrich_updates={'is_aec_checked': True, 'is_aec': False,
                                'category': 'OTHER'})
        self.assertEqual(Tender.objects.count(), 0)          # dropped, DB clean
        self.assertEqual(counters_seen['dropped'], 1)        # counted ONCE
        self.assertEqual(counters_seen['photos_dropped'], 1)
        self.assertEqual(counters_seen['duplicates'], 1)     # 2nd copy = dupe

    def test_zero_tenders_edition_is_done(self):
        result = self.run_pipeline(_discovery([URL1]), {URL1: PDF1}, {URL1: []})
        self.assertEqual(result, 'done')
        self.assertEqual(Tender.objects.count(), 0)

    # ── Idempotency / dedup ─────────────────────────────────────────────────
    def test_second_run_creates_no_duplicates(self):
        args = (_discovery([URL1]), {URL1: PDF1}, {URL1: [ai_tender()]})
        self.run_pipeline(*args)
        self.assertEqual(Tender.objects.count(), 1)
        self.run_pipeline(*args)   # scheduler fired twice
        self.assertEqual(Tender.objects.count(), 1)
        self.assertEqual(NewspaperEdition.objects.count(), 1)

    def test_same_pdf_bytes_under_new_url_skipped(self):
        self.run_pipeline(_discovery([URL1]), {URL1: PDF1}, {URL1: [ai_tender()]})
        # Next run discovers an extra URL whose bytes are identical to URL1
        self.run_pipeline(_discovery([URL1, URL2]),
                          {URL1: PDF1, URL2: PDF1},
                          {URL1: [ai_tender()], URL2: [ai_tender()]})
        self.assertEqual(Tender.objects.count(), 1)
        dupe_row = NewspaperEditionFile.objects.get(pdf_url=URL2)
        self.assertEqual(dupe_row.status, NewspaperEditionFile.Status.DONE)
        self.assertIn('Duplicate', dupe_row.error_message)

    def test_same_tender_on_two_pages_deduplicated(self):
        result = self.run_pipeline(
            _discovery([URL1, URL2]),
            {URL1: PDF1, URL2: PDF2},
            {URL1: [ai_tender()], URL2: [ai_tender(page_number=2)]},
        )
        self.assertEqual(result, 'done')
        self.assertEqual(Tender.objects.count(), 1)

    # ── Partial failure + retry ─────────────────────────────────────────────
    def test_partial_failure_then_retry_only_failed(self):
        result = self.run_pipeline(
            _discovery([URL1, URL2]),
            {URL1: PDF1, URL2: intake.DownloadError('HTTP 500')},
            {URL1: [ai_tender()], URL2: []},
        )
        self.assertEqual(result, 'partial')
        ed = NewspaperEdition.objects.get(edition_date=TARGET)
        self.assertEqual(ed.status, NewspaperEdition.Status.PROCESSING)
        self.assertEqual(Tender.objects.count(), 1)

        # Retry run: URL2 now downloads; URL1 must NOT be reprocessed
        with mock.patch.object(pipeline.ai_client, 'extract_tenders',
                               return_value=[]) as m_extract, \
             mock.patch.object(pipeline.intake, 'download_pdf',
                               return_value=PDF2) as m_download, \
             mock.patch.object(pipeline.alrai, 'discover',
                               return_value=_discovery([URL1, URL2])):
            result = pipeline.run_for_date(TARGET)
        self.assertEqual(result, 'done')
        self.assertEqual(m_download.call_count, 1)          # only URL2
        self.assertEqual(m_download.call_args.args[0], URL2)

    def test_ai_retryable_failure_keeps_file_retryable(self):
        result = self.run_pipeline(
            _discovery([URL1]), {URL1: PDF1},
            {URL1: ai_client.AIServiceError('timeout', retryable=True)},
        )
        self.assertEqual(result, 'partial')
        row = NewspaperEditionFile.objects.get(pdf_url=URL1)
        self.assertEqual(row.status, NewspaperEditionFile.Status.DOWNLOADED)

    def test_corrupted_pdf_marked_failed_run_continues(self):
        result = self.run_pipeline(
            _discovery([URL1, URL2]),
            {URL1: b'<html>not a pdf</html>', URL2: PDF2},
            {URL2: [ai_tender()]},
        )
        self.assertEqual(result, 'partial')
        self.assertEqual(NewspaperEditionFile.objects.get(pdf_url=URL1).status,
                         NewspaperEditionFile.Status.FAILED)
        self.assertEqual(Tender.objects.count(), 1)

    # ── Final deterministic gate ────────────────────────────────────────────
    def test_invalid_ai_records_rejected(self):
        result = self.run_pipeline(
            _discovery([URL1]), {URL1: PDF1},
            {URL1: [
                ai_tender(title=''),                      # no title → rejected
                'not even a dict',                        # → rejected
                ai_tender(title='سليم', category='WEIRD_CAT'),  # bad category → OTHER, kept
            ]},
        )
        self.assertEqual(result, 'done')
        # Only the record with a title survives; the provisional category was
        # sanitised to OTHER before the standard enrichment set the real one.
        self.assertEqual(Tender.objects.count(), 1)

    def test_non_tender_auction_is_dropped_but_tender_is_saved(self):
        # An auction ad and a real tender arrive in one AI batch; the
        # deterministic gate drops the auction, the tender is saved.
        result = self.run_pipeline(
            _discovery([URL1]), {URL1: PDF1},
            {URL1: [
                ai_tender(title='دعوة عطاء رقم 7/2026 - أعمال بناء',
                          title_ar='دعوة عطاء رقم 7/2026 - أعمال بناء'),
                ai_tender(title='إعلان مزاد علني لبيع مركبات مستعملة',
                          title_ar='إعلان مزاد علني لبيع مركبات مستعملة'),
            ]},
        )
        self.assertEqual(result, 'done')
        self.assertEqual(Tender.objects.count(), 1)
        self.assertEqual(Tender.objects.get().title,
                         'دعوة عطاء رقم 7/2026 - أعمال بناء')

    def test_fabricated_entity_tender_is_rejected(self):
        # A template company named after a parenthesised foreign city is a
        # hallucination — the whole row is dropped, nothing is saved.
        self.run_pipeline(_discovery([URL1]), {URL1: PDF1},
                          {URL1: [ai_tender(entity='شركة (دبي) للمقاولات العامة',
                                            entity_ar='شركة (دبي) للمقاولات العامة')]})
        self.assertEqual(Tender.objects.count(), 0)

    def test_court_notice_crop_rejects_the_whole_tender(self):
        # The AJiAI extraction fabricates tenders from court-notice pages; such
        # a row's crop OCRs as a court notice. That means the row is not a real
        # tender, so the WHOLE tender is rejected (not just the photo dropped).
        import base64
        jpeg = base64.b64encode(b'\xff\xd8\xff\xe0fake').decode()
        with mock.patch('newspaper_tenders.photo_verify.available', return_value=True), \
             mock.patch('newspaper_tenders.photo_verify.photo_belongs_to_tender',
                        return_value=False):
            self.run_pipeline(_discovery([URL1]), {URL1: PDF1},
                              {URL1: [ai_tender(ad_image_b64=jpeg)]})
        self.assertEqual(Tender.objects.count(), 0,
                         'a tender whose crop is a court notice must be rejected')

    def test_matching_photo_is_kept_at_save(self):
        import base64
        jpeg = base64.b64encode(b'\xff\xd8\xff\xe0fake').decode()
        with mock.patch('newspaper_tenders.photo_verify.available', return_value=True), \
             mock.patch('newspaper_tenders.photo_verify.photo_belongs_to_tender',
                        return_value=True):
            self.run_pipeline(_discovery([URL1]), {URL1: PDF1},
                              {URL1: [ai_tender(ad_image_b64=jpeg)]})
        t = Tender.objects.get(source__scraper_class='NEWSPAPER_ALRAI')
        self.assertTrue(t.newspaper_meta.ad_image, 'a matching photo must be kept')

    def test_cross_source_duplicate_is_skipped(self):
        # The same tender already exists from another newspaper (Ad-Dustour);
        # the incoming Al-Rai copy is skipped — no second row is created.
        from newspaper_tenders.models import NewspaperEdition, NewspaperTenderMeta
        dustour = pipeline.get_newspaper_source('DUSTOUR')
        jo = CountryConfig.objects.get(code='JO')
        t0 = Tender.objects.create(
            source=dustour, external_id='pre-existing', source_url='http://x/',
            title='دعوة عطاء رقم 12/2026 - أعمال إشراف هندسي على مبنى المحكمة',
            entity='وزارة الأشغال العامة والإسكان', reference_no='عطاء رقم 12/2026',
            submission_deadline=date.fromisoformat(FUTURE), country=jo)
        ed = NewspaperEdition.objects.create(
            source='DUSTOUR', edition_date=date.fromisoformat(FUTURE),
            article_url='http://x/')
        NewspaperTenderMeta.objects.create(tender=t0, edition=ed)

        self.run_pipeline(_discovery([URL1]), {URL1: PDF1}, {URL1: [ai_tender()]})

        self.assertEqual(Tender.objects.count(), 1, 'Al-Rai duplicate must be skipped')
        self.assertEqual(Tender.objects.get().source, dustour)

    def test_past_deadline_follows_existing_policy(self):
        past = (TARGET - timedelta(days=5)).isoformat()
        with mock.patch('django.utils.timezone.now') as m_now:
            import datetime as dt
            from django.utils.timezone import make_aware
            m_now.return_value = make_aware(dt.datetime(2026, 8, 19, 6, 0))
            result = self.run_pipeline(
                _discovery([URL1]), {URL1: PDF1},
                {URL1: [ai_tender(submission_deadline=past)]},
            )
        self.assertEqual(result, 'done')
        self.assertEqual(Tender.objects.count(), 0)   # same skip policy as all tenders

    def test_bad_iso_dates_dropped_tender_kept(self):
        result = self.run_pipeline(
            _discovery([URL1]), {URL1: PDF1},
            {URL1: [ai_tender(submission_deadline='31/09/2026', opening_date='x')]},
        )
        self.assertEqual(result, 'done')
        t = Tender.objects.get()
        self.assertIsNone(t.submission_deadline)
        self.assertIsNone(t.opening_date)

    # ── Source row invariants ───────────────────────────────────────────────
    def test_newspaper_source_row_is_inactive_and_hidden_class(self):
        src = pipeline.get_newspaper_source()
        self.assertFalse(src.is_active)                  # run_all skips it
        self.assertEqual(src.scraper_class, 'NEWSPAPER_ALRAI')
        # Deliberately NOT registered as a scraper — the newspaper is not one.
        from tenders.scrapers import get_scraper
        with self.assertRaises(ValueError):
            get_scraper('NEWSPAPER_ALRAI')

    def test_enrichment_runs_exactly_like_scrapers(self):
        # REWIRED: newspaper saves go through the DEFAULT save path — the
        # standard enrichment service is called once per created tender,
        # identically to a scraped tender. Re-runs (dedup) don't re-classify.
        self.run_pipeline(_discovery([URL1]), {URL1: PDF1},
                          {URL1: [ai_tender()]})
        self.assertEqual(self.last_enrich_mock.call_count, 1)

        # Second run: tender already exists → no new classification call.
        self.run_pipeline(_discovery([URL1]), {URL1: PDF1},
                          {URL1: [ai_tender()]})
        self.assertEqual(self.last_enrich_mock.call_count, 0)


# ── Non-tender (auction / sale) rejection gate ────────────────────────────────

class FabricatedEntityTests(TestCase):
    """
    The AJiAI extraction invents placeholder tenders (template companies named
    after a parenthesised foreign city, or a few reused invented names). These
    must be rejected; real Jordanian issuers must never be.
    """

    def test_parenthesised_foreign_city_is_fabricated(self):
        for e in ('شركة (أبوظبي) للتطوير العقاري', 'شركة (دبي) للمقاولات العامة',
                  'شركة (الرياض) للتقنية', 'شركة (الكويت) للمقاولات',
                  'شركة (مسقط) للمقاولات'):
            with self.subTest(entity=e):
                self.assertTrue(pipeline.is_fabricated_entity(e))

    def test_blacklisted_name_is_fabricated(self):
        self.assertTrue(pipeline.is_fabricated_entity('شركة البتراء للخدمات الطبية'))

    def test_real_issuers_are_kept(self):
        for e in ('وزارة الأشغال العامة والإسكان', 'بلدية إربد الكبرى',
                  'دائرة العطاءات الحكومية', ''):
            with self.subTest(entity=e):
                self.assertFalse(pipeline.is_fabricated_entity(e))


class CrossSourceDedupTests(TestCase):
    """
    cross_source_duplicate(): the same government tender runs in several
    newspapers; a copy already saved from another paper is a duplicate — but
    two different bodies reusing one annual number must NOT be merged.
    """

    @classmethod
    def setUpTestData(cls):
        cls.alrai   = pipeline.get_newspaper_source('ALRAI')
        cls.dustour = pipeline.get_newspaper_source('DUSTOUR')

    def _existing(self, **kw):
        """An existing Ad-Dustour newspaper tender to match against."""
        from newspaper_tenders.models import NewspaperEdition, NewspaperTenderMeta
        d = dict(entity='وزارة التربية والتعليم', ref='', title='إعلان طرح عطاءات',
                 deadline=date(2026, 9, 1))
        d.update(kw)
        jo = CountryConfig.objects.get(code='JO')
        t = Tender.objects.create(
            source=self.dustour, external_id=f'dup-{Tender.objects.count()}',
            source_url='http://x/', title=d['title'], entity=d['entity'],
            reference_no=d['ref'], submission_deadline=d['deadline'], country=jo)
        ed = NewspaperEdition.objects.create(
            source='DUSTOUR', edition_date=d['deadline'] or date(2026, 9, 1),
            article_url='http://x/')
        NewspaperTenderMeta.objects.create(tender=t, edition=ed)
        return t

    def _cleaned(self, **kw):
        base = dict(entity='وزارة التربية والتعليم', reference_no='',
                    title='إعلان طرح عطاءات', submission_deadline=date(2026, 9, 2))
        base.update(kw)
        return base

    def test_same_entity_same_reference_is_duplicate(self):
        self._existing(ref='عطاء رقم 12/2026', entity='وزارة الأشغال', title='دعوة عطاء')
        self.assertIsNotNone(pipeline.cross_source_duplicate(
            self._cleaned(reference_no='رقم 12/2026', entity='وزارة الأشغال',
                          title='دعوة عطاء'), self.alrai))

    def test_same_reference_different_entity_is_not_duplicate(self):
        # The ٢٥/٢٠٢٦ trap — same number, two different bodies → keep both.
        self._existing(ref='25/2026', entity='شركة البتراء للخدمات الطبية',
                       title='دعوة عطاء')
        self.assertIsNone(pipeline.cross_source_duplicate(
            self._cleaned(reference_no='25/2026',
                          entity='المجلس الأعلى لحقوق الأشخاص',
                          title='دعوة عطاء'), self.alrai))

    def test_refless_announcement_within_window_is_duplicate(self):
        self._existing(deadline=date(2026, 9, 1))
        self.assertIsNotNone(pipeline.cross_source_duplicate(
            self._cleaned(submission_deadline=date(2026, 9, 2)), self.alrai))

    def test_refless_far_deadline_is_not_duplicate(self):
        self._existing(deadline=date(2026, 9, 1))
        self.assertIsNone(pipeline.cross_source_duplicate(
            self._cleaned(submission_deadline=date(2026, 9, 20)), self.alrai))

    def test_same_source_is_not_a_cross_source_duplicate(self):
        self._existing()
        self.assertIsNone(pipeline.cross_source_duplicate(
            self._cleaned(), self.dustour))          # same source → excluded

    def test_different_reference_same_entity_kept_apart(self):
        self._existing(ref='12/2026', entity='وزارة الأشغال', title='دعوة عطاء')
        self.assertIsNone(pipeline.cross_source_duplicate(
            self._cleaned(reference_no='13/2026', entity='وزارة الأشغال',
                          title='دعوة عطاء'), self.alrai))

    def test_same_entity_same_digit_reference_but_different_title_kept_apart(self):
        # Real case: same body, sub-tender numbers that reduce to the same
        # digits, but DIFFERENT tenders — 12/فرعية "postage stamps" vs
        # 12/ل/فرعية "laser printers". Must never be merged.
        self._existing(entity='المؤسسة العامة للضمان الاجتماعي',
                       ref='عطاء رقم (ع.12/فرعية/2026)',
                       title='شراء طوابع بريدية ملونة عدد (35)',
                       deadline=date(2026, 9, 2))
        self.assertIsNone(pipeline.cross_source_duplicate(
            self._cleaned(entity='المؤسسة العامة للضمان الاجتماعي',
                          reference_no='ع.أ رقم 12/ل/فرعية (2026)',
                          title='شراء طابعات ليزرية اسود عدد (35)',
                          submission_deadline=date(2026, 9, 2)), self.alrai))


class PhotoVerifyTests(TestCase):
    """
    The photo-verification decision (OCR text → keep/drop). Pure and fast —
    no OCR runs here; real OCR is exercised by the management command.
    """

    # A real OCR read of one of the wrong (court-notice) crops, 24 Aug 2026.
    COURT_OCR = ('مذكرة تبليغ حكم / بالنشر صادرة عن محكمة صلح حقوق شمال عمان '
                 'رقم الدعوى ٥٦٩٧ ٢٠٢٦')

    def test_court_notice_crop_is_flagged_wrong(self):
        self.assertTrue(photo_verify.crop_reads_as_non_tender(self.COURT_OCR))

    def test_auction_crop_is_flagged_wrong(self):
        self.assertTrue(photo_verify.crop_reads_as_non_tender(
            'إعلان مزاد علني لبيع أثاث ومركبات مستعملة'))

    def test_tender_ad_crop_is_kept(self):
        self.assertFalse(photo_verify.crop_reads_as_non_tender(
            'دعوة عطاء رقم ٨٨/٢٠٢٦ شركة البتراء للخدمات الطبية توريد مواد استهلاكية'))

    def test_courthouse_construction_tender_is_kept(self):
        # Mentions محكمة/صلح but is a real tender (عطاء present) — must be kept.
        self.assertFalse(photo_verify.crop_reads_as_non_tender(
            'عطاء إنشاء مبنى محكمة صلح حقوق إربد'))

    def test_empty_or_unreadable_ocr_is_kept(self):
        self.assertFalse(photo_verify.crop_reads_as_non_tender(''))
        self.assertFalse(photo_verify.crop_reads_as_non_tender('؟؟؟ ٱ ٢ ٣'))


class ListAdCropTests(TestCase):
    """
    A merged list ad must not show a WRONG photo. The AI's per-sub-item crops
    for a bundled ad are unreliable (it once attached a neighbouring court
    notice to the Sales-Tax 35-tender list), so a crop is kept only when every
    sub-item agrees on the same image; otherwise the merged row has no photo.
    """

    def _item(self, ref, img=None):
        return {
            'title': 'دعوة عطاء', 'title_ar': 'دعوة عطاء',
            'entity': 'دائرة الضريبة', 'entity_ar': 'دائرة الضريبة',
            'description': f'وصف {ref}', 'description_ar': f'وصف {ref}',
            'reference_no': ref, 'submission_deadline': None,
            'opening_date': None, 'category': 'OTHER', 'confidence': None,
            'page_number': 1, 'ad_image_b64': img,
        }

    def test_conflicting_crops_yield_no_photo(self):
        merged = pipeline.merge_list_ads([
            self._item('2026/1', img='CROP_TENDER'),
            self._item('2026/2', img='CROP_COURT_NOTICE'),   # wrong neighbour
            self._item('2026/3', img=None),
        ])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]['ad_image_b64'], '',
                         'conflicting crops must produce NO photo, not a guess')

    def test_unanimous_crop_is_kept(self):
        merged = pipeline.merge_list_ads([
            self._item('2026/1', img='WHOLE_AD_CROP'),
            self._item('2026/2', img='WHOLE_AD_CROP'),
            self._item('2026/3', img=None),
        ])
        self.assertEqual(merged[0]['ad_image_b64'], 'WHOLE_AD_CROP')

    def test_single_tender_keeps_its_own_crop(self):
        merged = pipeline.merge_list_ads([self._item('2026/9', img='ITS_OWN_CROP')])
        self.assertEqual(merged[0]['ad_image_b64'], 'ITS_OWN_CROP')


class NonTenderReasonTests(TestCase):
    """
    Unit tests for the deterministic non-tender gate. It drops auction /
    asset-sale ads the AI returned as tenders, and must NEVER drop a real
    procurement tender — including a genuine tender to RENT on the
    government's behalf.
    """

    def test_auctions_and_asset_sales_are_flagged(self):
        for title in (
            'إعلان مزاد بالظرف المختوم (المرحلة الرابعة)',
            'إعلان طرح المزادة رقم (T2026/31 P)',          # المزادة (definite)
            'إعلان بيع مزايدة العينية لمدة ثلاثون يوما',
            'إعلان بيع معدات اتصالات مختلفة غير صالحة - كسكرب',
            'أثاث ومعدات مكتبية للبيع بداعي إغلاق الشركة',  # للبيع (proclitic)
            'دائرة تنفيذ عمان - طرح عقار للبيع بالمظاريف',   # court asset sale
            'إعـلان مـزاد عـلني لبيــع أثـاث مستعمل',        # tatweel-obfuscated
            'مزاودة علنية على سيارات وأثاث مستعمل',          # colloquial مزاودة
        ):
            with self.subTest(title=title):
                self.assertTrue(pipeline.non_tender_reason(title),
                                f'should be rejected: {title}')

    def test_real_tenders_are_never_flagged(self):
        for title in (
            'دعوة عطاء رقم 12/2026 - أعمال إشراف هندسي',
            'دائرة ضريبة الدخل والمبيعات',                  # المبيعات must NOT trigger بيع
            'عطاء استئجار آليات للبلدية',                   # tender to RENT, has عطاء
            'طلب استئجار مبنى بديل لمركز التنمية',          # tender to RENT, NO عطاء — still kept
            'إيجار حافلات لنقل الطلبة للعام الدراسي',        # rent-IN tender, no marker
            'توريد وتركيب أجهزة نقاط بيع إلكترونية',        # POS supply tender (بيع noun)
            'توريد أجهزة بيع التذاكر الآلية لمحطات الحافلات',# ticket-vending supply tender
            'مزاد عكسي إلكتروني لتوريد أجهزة حاسوب مكتبية',  # reverse auction = buying
            'المزايدة العكسية لشراء أثاث مكتبي للدوائر',     # reverse auction = buying
            'عطاء بيع مركبات وآليات حكومية مستعملة',         # asset-disposal issued AS a عطاء
            'إعلان طرح العطاء رقم (٢٠٢٦/٩٠) لأعمال الترميم',
        ):
            with self.subTest(title=title):
                self.assertEqual(pipeline.non_tender_reason(title), '',
                                 f'should be kept: {title}')


