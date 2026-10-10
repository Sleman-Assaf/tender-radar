# tenders/migrations/0012_afdb_source_url.py
from django.db import migrations


def update_afdb_url(apps, schema_editor):
    ScraperSource = apps.get_model('tenders', 'ScraperSource')
    TenderingURL  = apps.get_model('tenders', 'TenderingURL')
    # Update base_url on the AfDB source
    ScraperSource.objects.filter(
        scraper_class='AfDBScraper'
    ).update(base_url='https://www.afdb.org')
    # Replace old mapafrica tendering URLs with new procurement URL
    TenderingURL.objects.filter(
        url__icontains='mapafrica.afdb.org'
    ).update(url='https://www.afdb.org/en/projects-and-operations/procurement')
    TenderingURL.objects.filter(
        source__scraper_class='AfDBScraper'
    ).update(url='https://www.afdb.org/en/projects-and-operations/procurement')


class Migration(migrations.Migration):
    dependencies = [
        ('tenders', '0011_tender_bookmarked_at'),
    ]
    operations = [
        migrations.RunPython(update_afdb_url, migrations.RunPython.noop),
    ]
