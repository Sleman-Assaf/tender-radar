# bd/models.py
# Path: TenderRadar/bd/models.py
#
# Business Development module — Phase 1.
#   * Engineer / CV pool  — uploaded CVs parsed (via AJiAI) into the structure
#     of the standard AJi CV template, searchable, exportable as formatted DOCX.
#   * Historical Projects pool — AJi project history (seeded from the Projects
#     List workbook) with tendering documents attached; the raw material for
#     AI proposal composition in Phase 2.

import json

from django.db import models


# ─────────────────────────────────────────────────────────────────────────────
# CV Pool
# ─────────────────────────────────────────────────────────────────────────────

class Discipline(models.TextChoices):
    ARCHITECTURE = 'ARCH',   'Architecture'
    STRUCTURAL   = 'STRUCT', 'Structural'
    CIVIL        = 'CIVIL',  'Civil'
    MECHANICAL   = 'MECH',   'Mechanical'
    ELECTRICAL   = 'ELEC',   'Electrical'
    MEP          = 'MEP',    'MEP'
    PM           = 'PM',     'Project Management'
    BIM          = 'BIM',    'BIM'
    QS           = 'QS',     'Quantity Surveying / Cost'
    ENVIRONMENT  = 'ENV',    'Environmental'
    PLANNING     = 'PLAN',   'Urban Planning'
    INTERIOR     = 'INT',    'Interior Design'
    SURVEY       = 'SURV',   'Surveying / Geotechnical'
    IT           = 'IT',     'IT / Digital'
    OTHER        = 'OTHER',  'Other'


class EngineerStatus(models.TextChoices):
    ACTIVE    = 'ACTIVE',   'Active Staff'
    AVAILABLE = 'AVAIL',    'Available (bench)'
    EXTERNAL  = 'EXTERNAL', 'External Associate'
    ARCHIVED  = 'ARCHIVED', 'Archived'


class Engineer(models.Model):
    """
    One person in the CV pool. Structured fields mirror the AJi CV template
    so a formatted CV can be generated at any time. JSON list fields hold the
    repeating sections (education, employment, projects, ...).
    """
    # ── Header block (template top) ─────────────────────────────────────────
    full_name        = models.CharField(max_length=200)
    current_position = models.CharField(max_length=200, blank=True, default='')
    profession       = models.CharField(max_length=200, blank=True, default='')
    nationality      = models.CharField(max_length=100, blank=True, default='')
    date_of_birth    = models.CharField(max_length=40,  blank=True, default='',
                                        help_text='Year or full date, as text.')
    years_with_firm  = models.CharField(max_length=40,  blank=True, default='')
    memberships      = models.TextField(blank=True, default='',
                                        help_text='One professional membership per line.')
    countries_experience = models.CharField(
        max_length=400, blank=True, default='',
        help_text='Comma-separated, e.g. "Jordan, Qatar, KSA".')
    synopsis         = models.TextField(blank=True, default='')
    photo            = models.FileField(upload_to='bd/photos/%Y/%m/', null=True, blank=True,
                                        help_text='Profile photo extracted from the uploaded CV '
                                                  '(or uploaded manually).')

    # ── Repeating sections (JSON) ───────────────────────────────────────────
    # education:      [{"institution","country","years","degree"}]
    # employment:     [{"employer","position","period","responsibilities":[...]}]
    # experience:     [{"project","country","year","position","client","description"}]
    #                 (flat bullet CVs are stored as {"description": "..."} entries)
    # certifications: [{"period","name","location"}]
    # languages:      [{"language","reading","speaking","writing"}]
    education_json      = models.TextField(blank=True, default='[]')
    employment_json     = models.TextField(blank=True, default='[]')
    experience_json     = models.TextField(blank=True, default='[]')
    certifications_json = models.TextField(blank=True, default='[]')
    languages_json      = models.TextField(blank=True, default='[]')

    # ── Pool metadata / search ──────────────────────────────────────────────
    discipline       = models.CharField(max_length=10, choices=Discipline.choices,
                                        default=Discipline.OTHER)
    years_experience = models.PositiveIntegerField(null=True, blank=True)
    status           = models.CharField(max_length=10, choices=EngineerStatus.choices,
                                        default=EngineerStatus.ACTIVE)
    cv_text          = models.TextField(blank=True, default='',
                                        help_text='Raw text extracted from the uploaded CV — '
                                                  'used for keyword search and AI matching.')
    ai_parsed        = models.BooleanField(default=False,
                                           help_text='True when the fields were pre-filled by AJiAI.')
    needs_review     = models.BooleanField(default=False,
                                           help_text='Set for CVs created automatically by bulk '
                                                     'import — a BD should verify the parsed fields.')
    notes            = models.TextField(blank=True, default='')

    created_by = models.ForeignKey('auth.User', null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name='cvs_created')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering            = ['full_name']
        verbose_name        = 'Engineer CV'
        verbose_name_plural = 'Engineer CVs'

    def __str__(self):
        return f'{self.full_name} — {self.current_position or self.profession}'

    # ── JSON helpers ─────────────────────────────────────────────────────────
    @staticmethod
    def _loads(raw):
        try:
            data = json.loads(raw or '[]')
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, ValueError):
            return []

    @property
    def education(self):      return self._loads(self.education_json)
    @property
    def employment(self):     return self._loads(self.employment_json)
    @property
    def experience(self):     return self._loads(self.experience_json)
    @property
    def certifications(self): return self._loads(self.certifications_json)
    @property
    def languages(self):      return self._loads(self.languages_json)

    @property
    def memberships_list(self):
        return [m.strip() for m in (self.memberships or '').splitlines() if m.strip()]


class CVDocument(models.Model):
    """A file attached to an engineer — the original upload or a generated
    template-formatted version."""

    class Kind(models.TextChoices):
        ORIGINAL  = 'ORIGINAL',  'Original Upload'
        FORMATTED = 'FORMATTED', 'Formatted (AJi Template)'
        OTHER     = 'OTHER',     'Other'

    engineer    = models.ForeignKey(Engineer, on_delete=models.CASCADE,
                                    related_name='documents')
    kind        = models.CharField(max_length=10, choices=Kind.choices,
                                   default=Kind.ORIGINAL)
    file        = models.FileField(upload_to='bd/cvs/%Y/%m/')
    original_name = models.CharField(max_length=255, blank=True, default='')
    uploaded_by = models.ForeignKey('auth.User', null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name='cv_docs_uploaded')
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-uploaded_at']

    def __str__(self):
        return f'{self.engineer.full_name} — {self.get_kind_display()}'


# ─────────────────────────────────────────────────────────────────────────────
# Historical Projects Pool
# ─────────────────────────────────────────────────────────────────────────────

class ProjectSector(models.TextChoices):
    """Mirrors the sheets of the AJi Projects List workbook."""
    COMMERCIAL   = 'COMMERCIAL',  'Commercial'
    EDUCATIONAL  = 'EDUCATIONAL', 'Educational'
    HOSPITALITY  = 'HOSPITALITY', 'Hospitality'
    INFRA        = 'INFRA',       'Master Planning & Infrastructure'
    RESIDENTIAL  = 'RESIDENTIAL', 'Residential'
    INDUSTRIAL   = 'INDUSTRIAL',  'Industrial'
    STUDIES      = 'STUDIES',     'Studies'
    HEALTHCARE   = 'HEALTHCARE',  'Wellness & Healthcare'
    OTHER        = 'OTHER',       'Other'


# Sector → tender category (BUILDING / INFRA / HEALTH) for AI matching
SECTOR_CATEGORY_MAP = {
    'COMMERCIAL':  'BUILDING',
    'EDUCATIONAL': 'BUILDING',
    'HOSPITALITY': 'BUILDING',
    'RESIDENTIAL': 'BUILDING',
    'INDUSTRIAL':  'BUILDING',
    'INFRA':       'INFRA',
    'HEALTHCARE':  'HEALTH',
    'STUDIES':     'OTHER',
    'OTHER':       'OTHER',
}


class ProjectStatus(models.TextChoices):
    COMPLETED = 'COMPLETED', 'Completed'
    ONGOING   = 'ONGOING',   'On-going'
    ON_HOLD   = 'ON_HOLD',   'On hold'
    UNKNOWN   = 'UNKNOWN',   '—'


class HistoricalProject(models.Model):
    """One AJi past project — the reference pool the AI matches against new
    tenders and composes proposals from."""

    code        = models.CharField(max_length=60, blank=True, default='')
    name        = models.CharField(max_length=300)
    sector      = models.CharField(max_length=15, choices=ProjectSector.choices,
                                   default=ProjectSector.OTHER)
    category    = models.CharField(max_length=10, default='BUILDING',
                                   help_text='Tender category for AI matching: '
                                             'BUILDING / INFRA / HEALTH / OTHER.')
    city        = models.CharField(max_length=120, blank=True, default='')
    country     = models.CharField(max_length=120, blank=True, default='')
    client      = models.CharField(max_length=300, blank=True, default='')
    description = models.TextField(blank=True, default='')
    site_area   = models.CharField(max_length=80, blank=True, default='')
    bua         = models.CharField(max_length=80, blank=True, default='',
                                   verbose_name='BUA')
    partners    = models.CharField(max_length=400, blank=True, default='')
    services    = models.TextField(blank=True, default='')
    status      = models.CharField(max_length=10, choices=ProjectStatus.choices,
                                   default=ProjectStatus.UNKNOWN)

    start_year  = models.PositiveIntegerField(null=True, blank=True)
    end_year    = models.PositiveIntegerField(null=True, blank=True)
    start_raw   = models.CharField(max_length=60, blank=True, default='')
    end_raw     = models.CharField(max_length=60, blank=True, default='')

    # ── Values (USD) ─────────────────────────────────────────────────────────
    project_value = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    design_value  = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    spv_value     = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True,
                                        verbose_name='Supervision Value')
    pmo_value     = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    studies_value = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    others_value  = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    staff_months  = models.CharField(max_length=60, blank=True, default='',
                                     verbose_name='Deployment (MM)')

    # ── Client contact ───────────────────────────────────────────────────────
    contact_person  = models.CharField(max_length=200, blank=True, default='')
    contact_phone   = models.CharField(max_length=200, blank=True, default='')
    contact_email   = models.CharField(max_length=300, blank=True, default='')
    contact_address = models.TextField(blank=True, default='')
    contact_website = models.CharField(max_length=300, blank=True, default='')

    # ── Search / AI matching ─────────────────────────────────────────────────
    project_text = models.TextField(blank=True, default='',
                                    help_text='Concatenated searchable text — name, client, '
                                              'description, services. Rebuilt on save.')

    # Won tenders scraped from now on can be linked to the project they became
    tender = models.ForeignKey('tenders.Tender', null=True, blank=True,
                               on_delete=models.SET_NULL, related_name='historical_projects',
                               help_text='The TenderRadar tender this project originated from '
                                         '(new tenders only).')

    created_by = models.ForeignKey('auth.User', null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name='projects_created')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering            = ['name']
        verbose_name        = 'Historical Project'
        verbose_name_plural = 'Historical Projects'
        constraints = [
            models.UniqueConstraint(fields=['name', 'client'],
                                    name='uniq_project_name_client'),
        ]

    def __str__(self):
        return self.name

    def rebuild_text(self):
        parts = [self.name, self.code, self.client, self.city, self.country,
                 self.description, self.services, self.partners,
                 self.get_sector_display()]
        self.project_text = '\n'.join(p for p in parts if p)

    def save(self, *args, **kwargs):
        self.rebuild_text()
        super().save(*args, **kwargs)

    @property
    def contract_value_total(self):
        vals = [self.design_value, self.spv_value, self.pmo_value,
                self.studies_value, self.others_value]
        vals = [v for v in vals if v is not None]
        return sum(vals) if vals else None

    @property
    def period_display(self):
        s = self.start_raw or (str(self.start_year) if self.start_year else '')
        e = self.end_raw or (str(self.end_year) if self.end_year else '')
        if s and e:
            return f'{s} – {e}'
        return s or e or '—'


class ProjectDocument(models.Model):
    """A tendering / delivery document attached to a historical project.
    Past proposals are the raw material for AI proposal composition."""

    class DocType(models.TextChoices):
        PROPOSAL     = 'PROPOSAL',     'Proposal (Technical/Financial)'
        RFP          = 'RFP',          'RFP / EOI / ToR'
        CONTRACT     = 'CONTRACT',     'Contract / Agreement'
        COMPLETION   = 'COMPLETION',   'Completion Certificate'
        PRESENTATION = 'PRESENTATION', 'Presentation'
        OTHER        = 'OTHER',        'Other'

    project     = models.ForeignKey(HistoricalProject, on_delete=models.CASCADE,
                                    related_name='documents')
    doc_type    = models.CharField(max_length=15, choices=DocType.choices,
                                   default=DocType.OTHER)
    title       = models.CharField(max_length=255, blank=True, default='')
    file        = models.FileField(upload_to='bd/projects/%Y/%m/')
    original_name  = models.CharField(max_length=255, blank=True, default='')
    extracted_text = models.TextField(blank=True, default='',
                                      help_text='Text extracted on upload — used for AI '
                                                'retrieval when composing proposals.')
    uploaded_by = models.ForeignKey('auth.User', null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name='project_docs_uploaded')
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-uploaded_at']

    def __str__(self):
        return f'{self.project.name} — {self.get_doc_type_display()}'


# ─────────────────────────────────────────────────────────────────────────────
# Bulk CV Import Queue
# ─────────────────────────────────────────────────────────────────────────────

class CVBatch(models.Model):
    """One bulk upload of CVs. Items are analysed by AJiAI one at a time
    (Ollama is a single queue) until the batch finishes."""

    class Status(models.TextChoices):
        QUEUED     = 'QUEUED',     'Queued'
        PROCESSING = 'PROCESSING', 'Processing'
        DONE       = 'DONE',       'Done'
        FAILED     = 'FAILED',     'Failed'
        CANCELLED  = 'CANCELLED',  'Cancelled'

    status     = models.CharField(max_length=12, choices=Status.choices,
                                  default=Status.QUEUED)
    created_by = models.ForeignKey('auth.User', null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name='cv_batches')
    created_at  = models.DateTimeField(auto_now_add=True)
    started_at  = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering            = ['-created_at']
        verbose_name        = 'CV Import Batch'
        verbose_name_plural = 'CV Import Batches'

    def __str__(self):
        return f'Batch #{self.pk} — {self.items.count()} CVs ({self.get_status_display()})'

    @property
    def counts(self) -> dict:
        items = list(self.items.values_list('status', flat=True))
        return {
            'total':      len(items),
            'pending':    items.count(CVBatchItem.Status.PENDING),
            'processing': items.count(CVBatchItem.Status.PROCESSING),
            'done':       items.count(CVBatchItem.Status.DONE),
            'failed':     items.count(CVBatchItem.Status.FAILED),
            'cancelled':  items.count(CVBatchItem.Status.CANCELLED),
        }

    @property
    def progress_pct(self) -> int:
        c = self.counts
        if not c['total']:
            return 0
        return int(round(100 * (c['done'] + c['failed'] + c['cancelled']) / c['total']))


class CVBatchItem(models.Model):
    """One CV file inside a batch."""

    class Status(models.TextChoices):
        PENDING    = 'PENDING',    'Pending'
        PROCESSING = 'PROCESSING', 'Analysing'
        DONE       = 'DONE',       'Done'
        FAILED     = 'FAILED',     'Failed'
        CANCELLED  = 'CANCELLED',  'Cancelled'

    batch         = models.ForeignKey(CVBatch, on_delete=models.CASCADE,
                                      related_name='items')
    file          = models.FileField(upload_to='bd/cvs/batches/%Y/%m/')
    original_name = models.CharField(max_length=255, blank=True, default='')
    status        = models.CharField(max_length=12, choices=Status.choices,
                                     default=Status.PENDING)
    error         = models.CharField(max_length=500, blank=True, default='')
    engineer      = models.ForeignKey(Engineer, null=True, blank=True,
                                      on_delete=models.SET_NULL,
                                      related_name='batch_items')
    was_update    = models.BooleanField(default=False,
                                        help_text='True when an existing engineer with the same '
                                                  'name was updated instead of creating a new one.')
    processed_at  = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['pk']

    def __str__(self):
        return f'{self.original_name} ({self.get_status_display()})'
