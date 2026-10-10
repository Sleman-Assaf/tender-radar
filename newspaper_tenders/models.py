# newspaper_tenders/models.py
# Isolated storage layer for Newspaper Tenders — عطاءات الجريدة.
#
# Actual tender records live in the existing tenders.Tender table (so all
# existing tender functionality — dedup, bookmarks, notes, AEC feedback,
# digest, detail page — keeps working). These models hold everything that is
# newspaper-specific: edition state, per-PDF processing state, and per-tender
# provenance.

from django.db import models
from django.utils import timezone


NEWSPAPER_SOURCE_ALRAI = 'ALRAI'


class NewspaperEdition(models.Model):
    """One daily edition of the Al-Rai advertisements section."""

    class Status(models.TextChoices):
        DISCOVERED   = 'DISCOVERED',   'Discovered'
        # Article page exists but no PDF embeds are attached yet — Al-Rai
        # regularly publishes the article hours before the PDFs. Retried.
        PENDING_PDFS = 'PENDING_PDFS', 'Pending PDFs'
        PROCESSING   = 'PROCESSING',   'Processing'
        DONE         = 'DONE',         'Done'
        FAILED       = 'FAILED',       'Failed'

    source        = models.CharField(max_length=20, default=NEWSPAPER_SOURCE_ALRAI)
    edition_date  = models.DateField(db_index=True)
    article_url   = models.URLField(max_length=2000, blank=True)
    status        = models.CharField(max_length=20, choices=Status.choices,
                                     default=Status.DISCOVERED, db_index=True)
    discovered_at = models.DateTimeField(null=True, blank=True)
    processed_at  = models.DateTimeField(null=True, blank=True)
    pdf_count     = models.PositiveIntegerField(default=0)
    error_message = models.TextField(blank=True)
    created_at    = models.DateTimeField(auto_now_add=True)
    updated_at    = models.DateTimeField(auto_now=True)

    class Meta:
        ordering        = ['-edition_date']
        unique_together = [['source', 'edition_date']]   # idempotency anchor
        verbose_name        = 'Newspaper Edition'
        verbose_name_plural = 'Newspaper Editions — أعداد الجريدة'

    def __str__(self):
        return f'{self.source} {self.edition_date} [{self.status}]'

    @property
    def source_display(self) -> str:
        """Arabic newspaper name for the UI (falls back to the raw code)."""
        try:
            from .sources import get_source_config
            return get_source_config(self.source)['display']
        except Exception:
            return self.source

    @property
    def source_display_en(self) -> str:
        try:
            from .sources import get_source_config
            return get_source_config(self.source)['display_en']
        except Exception:
            return self.source

    def mark(self, status, error=''):
        self.status = status
        if error:
            self.error_message = error[:5000]
        if status == self.Status.DONE:
            self.processed_at = timezone.now()
        self.save()


class NewspaperEditionFile(models.Model):
    """One advertisement-page PDF inside an edition."""

    class Status(models.TextChoices):
        PENDING    = 'PENDING',    'Pending'
        DOWNLOADED = 'DOWNLOADED', 'Downloaded'
        DONE       = 'DONE',       'Done'
        FAILED     = 'FAILED',     'Failed'

    edition       = models.ForeignKey(NewspaperEdition, on_delete=models.CASCADE,
                                      related_name='files')
    pdf_url       = models.URLField(max_length=2000)
    # SHA256 of the downloaded bytes. Unique: the same physical PDF is never
    # processed twice, even when it reappears under a different URL or in a
    # later edition article.
    file_hash     = models.CharField(max_length=64, blank=True, db_index=True)
    file_size     = models.PositiveIntegerField(default=0)
    page_count    = models.PositiveIntegerField(default=0)
    status        = models.CharField(max_length=20, choices=Status.choices,
                                     default=Status.PENDING, db_index=True)
    error_message = models.TextField(blank=True)
    downloaded_at = models.DateTimeField(null=True, blank=True)
    processed_at  = models.DateTimeField(null=True, blank=True)
    tenders_found = models.PositiveIntegerField(default=0)
    created_at    = models.DateTimeField(auto_now_add=True)
    updated_at    = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['edition', 'id']
        constraints = [
            models.UniqueConstraint(
                fields=['file_hash'],
                condition=~models.Q(file_hash=''),
                name='uniq_newspaper_file_hash',
            ),
            # The same URL is listed once per edition (retries update the row).
            models.UniqueConstraint(
                fields=['edition', 'pdf_url'],
                name='uniq_newspaper_edition_pdf_url',
            ),
        ]
        verbose_name        = 'Newspaper Edition File'
        verbose_name_plural = 'Newspaper Edition Files'

    def __str__(self):
        return f'{self.edition.edition_date} {self.pdf_url.rsplit("/", 1)[-1]} [{self.status}]'

    def mark(self, status, error=''):
        self.status = status
        if error:
            self.error_message = error[:5000]
        if status == self.Status.DONE:
            self.processed_at = timezone.now()
        self.save()


class NewspaperTenderMeta(models.Model):
    """
    Newspaper provenance for one tenders.Tender row: which edition, which
    PDF, which page, plus the raw AI extraction for auditing.
    """
    tender          = models.OneToOneField('tenders.Tender',
                                           on_delete=models.CASCADE,
                                           related_name='newspaper_meta')
    edition         = models.ForeignKey(NewspaperEdition, on_delete=models.CASCADE,
                                        related_name='tender_meta')
    edition_file    = models.ForeignKey(NewspaperEditionFile,
                                        on_delete=models.SET_NULL, null=True,
                                        related_name='tender_meta')
    page_number     = models.PositiveIntegerField(default=1)
    ai_confidence   = models.FloatField(null=True, blank=True)
    original_pdf_url = models.URLField(max_length=2000, blank=True)
    # Photo of the specific printed announcement, cropped by the AI service
    # from the newspaper page. Empty when the model could not locate the ad
    # (no photo is shown then — never a wrong image).
    ad_image        = models.FileField(upload_to='newspaper_ads/',
                                       null=True, blank=True)
    # Full extraction record as returned by the AI service (raw_text included)
    raw_extracted_data = models.JSONField(default=dict, blank=True)
    created_at      = models.DateTimeField(auto_now_add=True)
    updated_at      = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name        = 'Newspaper Tender Provenance'
        verbose_name_plural = 'Newspaper Tender Provenance'

    def __str__(self):
        return f'Tender #{self.tender_id} ← {self.edition.edition_date} p.{self.page_number}'
