# magazine_tenders/models.py
"""
Two tables only. Everything else a magazine tender needs already exists on
`tenders.Tender`, so nothing here duplicates it — `MagazineRun` is the run log
(the magazine equivalent of `ScrapeLog`) and `MagazineTenderMeta` records where
one tender came from.
"""
from django.db import models


class MagazineRun(models.Model):
    """
    One execution of one magazine scraper.

    Kept separate from `tenders.ScrapeLog` for the same reason the whole app is
    separate: writing to ScrapeLog would make magazine runs appear in the
    scraper history pages, which is existing behaviour this feature must not
    change.
    """

    class Status(models.TextChoices):
        RUNNING = 'RUNNING', 'Running'
        SUCCESS = 'SUCCESS', 'Success'
        FAILED  = 'FAILED',  'Failed'

    class Trigger(models.TextChoices):
        MANUAL   = 'MANUAL',   'Manual'
        SCHEDULE = 'SCHEDULE', 'Schedule'
        CLI      = 'CLI',      'Command line'

    magazine      = models.CharField(max_length=30, db_index=True,
                                     help_text='Code from magazine_tenders.sources.MAGAZINES')
    started_at    = models.DateTimeField(auto_now_add=True)
    finished_at   = models.DateTimeField(null=True, blank=True)
    status        = models.CharField(max_length=10, choices=Status.choices,
                                     default=Status.RUNNING, db_index=True)
    trigger       = models.CharField(max_length=10, choices=Trigger.choices,
                                     default=Trigger.MANUAL)

    found         = models.PositiveIntegerField(default=0,
                                                help_text='TenderData items the scraper returned')
    saved_new     = models.PositiveIntegerField(default=0)
    saved_updated = models.PositiveIntegerField(default=0)
    skipped       = models.PositiveIntegerField(default=0,
                                                help_text='Rejected by the shared save policy, e.g. deadline already passed')
    aec           = models.PositiveIntegerField(default=0)
    non_aec       = models.PositiveIntegerField(default=0)
    error_message = models.TextField(blank=True)

    class Meta:
        ordering = ['-started_at']
        verbose_name = 'Magazine run'
        verbose_name_plural = 'Magazine runs'

    def __str__(self):
        return f'{self.magazine} · {self.started_at:%Y-%m-%d %H:%M} · {self.status}'

    @property
    def duration_seconds(self):
        """Elapsed seconds, or None while the run is still going."""
        if not self.finished_at:
            return None
        return (self.finished_at - self.started_at).total_seconds()

    @property
    def magazine_display(self):
        """Arabic display name from the registry; falls back to the raw code."""
        from . import sources
        cfg = sources.MAGAZINES.get(self.magazine) or {}
        return cfg.get('display') or self.magazine

    @property
    def magazine_display_en(self):
        from . import sources
        cfg = sources.MAGAZINES.get(self.magazine) or {}
        return cfg.get('display_en') or self.magazine


class MagazineTenderMeta(models.Model):
    """
    Provenance for one `tenders.Tender` row that came from a magazine website.

    Its presence is what marks a tender as a magazine tender: every view filters
    on `magazine_meta__isnull=False`, mirroring how `newspaper_meta` works. That
    keeps the marker in one place instead of spread across string comparisons on
    `source.scraper_class`.
    """

    RAW_SNIPPET_CAP = 20_000

    tender        = models.OneToOneField('tenders.Tender', on_delete=models.CASCADE,
                                         related_name='magazine_meta')
    magazine      = models.CharField(max_length=30, db_index=True)
    run           = models.ForeignKey(MagazineRun, on_delete=models.SET_NULL,
                                      null=True, blank=True, related_name='tender_meta')
    listing_url   = models.URLField(max_length=2000, blank=True,
                                    help_text='The listing page this item was found on')
    page_number   = models.PositiveIntegerField(default=1)
    raw_snippet   = models.TextField(blank=True,
                                     help_text='The HTML/JSON fragment that was parsed, for audit')
    # The article's featured image, as the publisher's own URL. Hotlinked, not
    # downloaded: nothing is stored locally and the browser fetches it from the
    # site. Empty for sources that have none — Gulf Construction's monthly
    # column shares one photo across 50 items (worse than none), and the
    # Ashghal list has no images at all. The page shows a designed block for
    # those rather than a broken image.
    image_url     = models.URLField(max_length=2000, blank=True,
                                    help_text="The publisher's featured image URL, hotlinked")
    # The same image at the publisher's largest sensible rendition, for the
    # article hero. The card rendition is ~768 px; the hero displays at up to
    # ~1,400 px, and showing the card image there meant upscaling it twice.
    image_url_large = models.URLField(max_length=2000, blank=True,
                                      help_text="The same image, large rendition, for the hero")
    # The quarter a FORTHCOMING project is expected in, as a sortable
    # 'YYYYQN' key - see magazine_tenders/quarters.py. Stored rather than
    # parsed on the fly so Future Projects can be ordered by it in the
    # database; sorting 145 rows in Python would break pagination. Empty for
    # ordinary news, which is not a pipeline and has nothing to expect.
    expected_quarter = models.CharField(max_length=6, blank=True, db_index=True,
                                        help_text="Sortable 'YYYYQN', e.g. 2026Q3")
    summary       = models.TextField(blank=True,
                                     help_text='Two or three sentences written by AJi AI when '
                                               'the article was stored, leading with the figures; '
                                               "empty falls back to the publisher's excerpt")
    summary_model = models.CharField(max_length=60, blank=True,
                                     help_text='The model AJi AI reported for the summary')
    first_seen_at = models.DateTimeField(auto_now_add=True)
    last_seen_at  = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Magazine tender provenance'
        verbose_name_plural = 'Magazine tender provenance'

    def __str__(self):
        return f'{self.magazine} · tender #{self.tender_id}'

    @property
    def magazine_display(self):
        from . import sources
        cfg = sources.MAGAZINES.get(self.magazine) or {}
        return cfg.get('display') or self.magazine

    @property
    def magazine_display_en(self):
        from . import sources
        cfg = sources.MAGAZINES.get(self.magazine) or {}
        return cfg.get('display_en') or self.magazine

    @property
    def is_forthcoming(self):
        """
        True when this row is a FORECAST rather than something published.

        Ashghal's المشاريع المتوقعة list and DevelopmentAid's forecast notices
        are announcements of work to come. A publication date on one of those
        dates the notice, not the project, and reads as though the project
        happened - so the page shows the expected quarter instead.
        """
        from . import sources
        return self.magazine in set(sources.forthcoming_codes())

    @property
    def quarter_display(self):
        """'Q3 2026' for the stored key, or '' when no quarter is known."""
        from .quarters import quarter_label
        return quarter_label(self.expected_quarter)

    @property
    def magazine_kind(self):
        """One line for the photo slot when there is no photo, or ''."""
        from . import sources
        cfg = sources.MAGAZINES.get(self.magazine) or {}
        return cfg.get('kind') or ''
