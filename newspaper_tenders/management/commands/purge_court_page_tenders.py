# newspaper_tenders/management/commands/purge_court_page_tenders.py
"""
Remove the tenders the AI invented from Al-Rai court-notice pages.

Before the page gate (page_gate.py) every page went to the AI, and the court
pages ("إعلانات قضائية") came back as lists of fabricated tenders — 47 rows
on 5 October 2026 alone. This command re-reads the printed header of every
Al-Rai page that produced tenders in the last N days and deletes the rows of
the pages that turn out to be court pages, photos included.

    # Preview — downloads and reads the headers, changes nothing:
    python manage.py purge_court_page_tenders

    # Apply — delete the rows, note the verdict on the file row:
    python manage.py purge_court_page_tenders --apply

    python manage.py purge_court_page_tenders --days 60 --apply

Only pages whose header POSITIVELY reads as court notices are touched; a page
that cannot be downloaded or read is reported and left alone.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from newspaper_tenders import intake, page_gate, sources
from newspaper_tenders.models import NewspaperEditionFile, NewspaperTenderMeta


class Command(BaseCommand):
    help = 'Delete tenders fabricated from Al-Rai court-notice pages (إعلانات قضائية).'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='Actually delete the rows (default: dry run).')
        parser.add_argument('--days', type=int, default=30,
                            help='Look back this many edition days (default 30).')
        parser.add_argument('--source', default='ALRAI',
                            help='Newspaper code (default ALRAI).')

    def handle(self, *args, **opts):
        if not page_gate.available():
            self.stderr.write(self.style.ERROR(
                'PyMuPDF and EasyOCR are required — pip install PyMuPDF easyocr'))
            return
        apply = opts['apply']
        code = opts['source'].strip().upper()
        cfg = sources.get_source_config(code)
        if not cfg.get('page_header_gate'):
            self.stderr.write(self.style.ERROR(
                f'{code} has no printed page header to read — nothing to do'))
            return

        since = timezone.now().date() - timedelta(days=opts['days'])
        files = (NewspaperEditionFile.objects
                 .filter(edition__source=code, edition__edition_date__gte=since,
                         tender_meta__isnull=False)
                 .distinct()
                 .select_related('edition')
                 .order_by('edition__edition_date', 'id'))
        self.stdout.write(f'{files.count()} {cfg["display_en"]} page(s) since {since} '
                          f'produced tenders — reading their headers '
                          f'({"APPLY — will delete" if apply else "dry run"})…')

        court_pages = deleted = unreadable = 0
        for f in files:
            label = f'{f.edition.edition_date} {f.pdf_url.rsplit("/", 1)[-1]}'
            if f.error_message.startswith(page_gate.SKIP_NOTE):
                kind = page_gate.COURT            # judged by an earlier run
            else:
                try:
                    data = intake.download_pdf(f.pdf_url, referer=cfg['base_url'] + '/')
                except intake.DownloadError as exc:
                    unreadable += 1
                    self.stdout.write(f'  ? {label}: cannot download ({exc}) — left alone')
                    continue
                kind = page_gate.classify_page(data)
            if kind != page_gate.COURT:
                continue

            court_pages += 1
            metas = (NewspaperTenderMeta.objects.filter(edition_file=f)
                     .select_related('tender'))
            titles = [m.tender.title[:60] for m in metas]
            self.stdout.write(f'  ✗ {label}: court-notices page, '
                              f'{len(titles)} fabricated row(s)')
            for t in titles:
                self.stdout.write(f'      - {t}')
            if not apply:
                continue
            for m in metas:
                if m.ad_image:
                    try:
                        m.ad_image.delete(save=False)
                    except Exception as exc:                   # pragma: no cover
                        self.stdout.write(f'      (photo not removed: {exc})')
                m.tender.delete()                 # cascades the meta row
                deleted += 1
            f.tenders_found = 0
            f.error_message = (f'{page_gate.SKIP_NOTE} — {len(titles)} fabricated '
                               f'row(s) removed on {timezone.now():%Y-%m-%d}')
            f.save(update_fields=['tenders_found', 'error_message', 'updated_at'])

        self.stdout.write(self.style.SUCCESS(
            f'Done. Court pages: {court_pages} | rows '
            f'{"deleted" if apply else "that would be deleted"}: '
            f'{deleted if apply else "see above"} | unreadable pages: {unreadable}'))
