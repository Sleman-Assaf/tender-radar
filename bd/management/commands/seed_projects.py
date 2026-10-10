# bd/management/commands/seed_projects.py
# Path: TenderRadar/bd/management/commands/seed_projects.py
#
# Imports historical projects from the AJi Projects List workbook.
#
#   python manage.py seed_projects --file "Projects_List_new.xlsx"
#   python manage.py seed_projects --file "..." --dry-run
#
# Workbook layout (all 8 sheets identical):
#   Row 4-5: two-level headers · data from row 6
#   B Project Code · C Project Name · D/E City & Country (header claims
#   Country/City but the data is City/Country — auto-detected per row)
#   F Client · G Description · H Site Area · I BUA · J Partners · K Services
#   L Status · M Start · N End · O Project Value · P-T Contract values
#   (Design/SPV/PMO/Studies/Others) · U MM · V-Z client contact
#
# Idempotent: rows are matched on (name, client); re-running updates in place.

import re

from django.core.management.base import BaseCommand, CommandError

from bd.models import (HistoricalProject, ProjectSector, ProjectStatus,
                       SECTOR_CATEGORY_MAP)

SHEET_SECTOR = {
    'Commercial':                      ProjectSector.COMMERCIAL,
    'Educational':                     ProjectSector.EDUCATIONAL,
    'Hospitality':                     ProjectSector.HOSPITALITY,
    'Master Planning & Infrastructur': ProjectSector.INFRA,
    'Master Planning & Infrastructure': ProjectSector.INFRA,
    'Residential':                     ProjectSector.RESIDENTIAL,
    'Industrial':                      ProjectSector.INDUSTRIAL,
    'Studies':                         ProjectSector.STUDIES,
    'Wellness & Healthcare':           ProjectSector.HEALTHCARE,
}

# Countries appearing in AJi's operating footprint — used to detect which of
# the two location cells is the country.
KNOWN_COUNTRIES = {
    'jordan', 'saudi arabia', 'ksa', 'uae', 'united arab emirates', 'qatar',
    'kuwait', 'bahrain', 'oman', 'iraq', 'syria', 'lebanon', 'palestine',
    'egypt', 'libya', 'yemen', 'sudan', 'algeria', 'morocco', 'tunisia',
    'turkey', 'usa', 'united states', 'uk', 'united kingdom', 'norway',
}

PLACEHOLDERS = {'??', '?', '_', '\\_', '-', '—', 'n/a', 'na', 'tbd'}


def _clean(val) -> str:
    if val is None:
        return ''
    s = str(val).strip()
    return '' if s.lower() in PLACEHOLDERS else s


def _num(val):
    """Decimal from messy cells like '1,156,000' / 2777513.3 / ''. """
    s = _clean(val).replace(',', '').replace('USD', '').strip()
    if not s:
        return None
    try:
        return round(float(s), 2)
    except ValueError:
        return None


def _year(val):
    """Extract a 4-digit year from '2015', 'Jan, 2015', 'Oct ,2006', dates."""
    s = _clean(val)
    if not s:
        return None, ''
    if hasattr(val, 'year'):           # datetime cell
        return val.year, s
    m = re.search(r'(19|20)\d{2}', s)
    return (int(m.group(0)) if m else None), s


def _status(val) -> str:
    s = _clean(val).lower()
    if s.startswith('complet'):
        return ProjectStatus.COMPLETED
    if 'going' in s or 'ongoing' in s:
        return ProjectStatus.ONGOING
    if 'hold' in s:
        return ProjectStatus.ON_HOLD
    return ProjectStatus.UNKNOWN


def _city_country(d_val, e_val):
    """Header says D=Country/E=City but the data is City/Country. Detect per
    row using the known-country list; fall back to the observed data order."""
    d, e = _clean(d_val), _clean(e_val)
    if e.lower() in KNOWN_COUNTRIES:
        return d, _norm_country(e)     # observed order: D=city, E=country
    if d.lower() in KNOWN_COUNTRIES:
        return e, _norm_country(d)     # header order:   D=country, E=city
    return d, _norm_country(e)         # unknown — keep observed order


_COUNTRY_FIX = {'ksa': 'KSA', 'uae': 'UAE', 'usa': 'USA', 'uk': 'UK',
                'egipt': 'Egypt'}


def _norm_country(name: str) -> str:
    """Consistent casing: 'LIBYA' → 'Libya', 'ksa' → 'KSA', 'Egipt' → 'Egypt'."""
    if not name:
        return ''
    low = name.strip().lower()
    return _COUNTRY_FIX.get(low, name.strip().title())


class Command(BaseCommand):
    help = 'Import historical projects from the AJi Projects List workbook.'

    def add_arguments(self, parser):
        parser.add_argument('--file', required=True, help='Path to the .xlsx workbook')
        parser.add_argument('--dry-run', action='store_true',
                            help='Parse and report without writing to the database')

    def handle(self, *args, **opts):
        try:
            import openpyxl
        except ImportError:
            raise CommandError('openpyxl is not installed. Run: pip install openpyxl')

        path    = opts['file']
        dry_run = opts['dry_run']
        try:
            wb = openpyxl.load_workbook(path, data_only=True)
        except Exception as exc:
            raise CommandError(f'Could not open workbook: {exc}')

        created = updated = skipped = 0

        for sheet_name in wb.sheetnames:
            sector = SHEET_SECTOR.get(sheet_name.strip())
            if sector is None:
                self.stdout.write(self.style.WARNING(
                    f'  ! Unknown sheet "{sheet_name}" — imported as OTHER'))
                sector = ProjectSector.OTHER
            ws = wb[sheet_name]

            for r in range(6, ws.max_row + 1):
                name = _clean(ws.cell(r, 3).value)          # C
                if not name:
                    continue
                name = re.sub(r'\s+', ' ', name)[:300]

                city, country = _city_country(ws.cell(r, 4).value,   # D
                                              ws.cell(r, 5).value)   # E
                client = _clean(ws.cell(r, 6).value)[:300]           # F
                start_year, start_raw = _year(ws.cell(r, 13).value)  # M
                end_year,   end_raw   = _year(ws.cell(r, 14).value)  # N

                fields = dict(
                    code          = _clean(ws.cell(r, 2).value)[:60],    # B
                    sector        = sector,
                    category      = SECTOR_CATEGORY_MAP.get(sector, 'OTHER'),
                    city          = city[:120],
                    country       = country[:120],
                    description   = _clean(ws.cell(r, 7).value),        # G
                    site_area     = _clean(ws.cell(r, 8).value)[:80],    # H
                    bua           = _clean(ws.cell(r, 9).value)[:80],    # I
                    partners      = _clean(ws.cell(r, 10).value)[:400],  # J
                    services      = _clean(ws.cell(r, 11).value),        # K
                    status        = _status(ws.cell(r, 12).value),       # L
                    start_year    = start_year,
                    end_year      = end_year,
                    start_raw     = start_raw[:60],
                    end_raw       = end_raw[:60],
                    project_value = _num(ws.cell(r, 15).value),          # O
                    design_value  = _num(ws.cell(r, 16).value),          # P
                    spv_value     = _num(ws.cell(r, 17).value),          # Q
                    pmo_value     = _num(ws.cell(r, 18).value),          # R
                    studies_value = _num(ws.cell(r, 19).value),          # S
                    others_value  = _num(ws.cell(r, 20).value),          # T
                    staff_months  = _clean(ws.cell(r, 21).value)[:60],   # U
                    contact_person  = _clean(ws.cell(r, 22).value)[:200],  # V
                    contact_phone   = _clean(ws.cell(r, 23).value)[:200],  # W
                    contact_email   = _clean(ws.cell(r, 24).value)[:300],  # X
                    contact_address = _clean(ws.cell(r, 25).value),        # Y
                    contact_website = _clean(ws.cell(r, 26).value)[:300],  # Z
                )

                if dry_run:
                    exists = HistoricalProject.objects.filter(
                        name=name, client=client).exists()
                    updated += 1 if exists else 0
                    created += 0 if exists else 1
                    continue

                obj, was_created = HistoricalProject.objects.update_or_create(
                    name=name, client=client, defaults=fields)
                created += 1 if was_created else 0
                updated += 0 if was_created else 1

            self.stdout.write(f'  ✓ {sheet_name}')

        verb = 'Would create/update' if dry_run else 'Done —'
        self.stdout.write(self.style.SUCCESS(
            f'{verb} created={created} updated={updated} skipped={skipped} '
            f'(total in DB: {HistoricalProject.objects.count()})'))
