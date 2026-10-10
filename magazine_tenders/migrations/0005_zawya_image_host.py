# Hand-written data migration, 2026-09-15.
#
# Zawya photos were stored as https://www.zawya.com/prod/images/original/... -
# a path that exists only on media.zawya.com, so every Zawya card showed its
# grey placeholder. Re-point the saved rows: the card gets the site's 950px
# gm_preview rendition, the article hero keeps the original.

import re

from django.db import migrations

ANY_ZAWYA = re.compile(r'^https?://(?:www\.|media\.)?zawya\.com/prod/images/(?:original|gm_preview)/(.+)$')
MEDIA = 'https://media.zawya.com/prod/images/'
SITE_ORIGINAL = 'https://www.zawya.com/prod/images/original/'


def _rows(Meta):
    return (Meta.objects.filter(image_url__contains='zawya.com/prod/images/')
            | Meta.objects.filter(image_url_large__contains='zawya.com/prod/images/'))


def forwards(apps, schema_editor):
    Meta = apps.get_model('magazine_tenders', 'MagazineTenderMeta')
    for meta in _rows(Meta):
        m = ANY_ZAWYA.match(meta.image_url or '') or ANY_ZAWYA.match(meta.image_url_large or '')
        if not m:
            continue
        name = m.group(1)
        meta.image_url = f'{MEDIA}gm_preview/{name}'
        meta.image_url_large = f'{MEDIA}original/{name}'
        meta.save(update_fields=['image_url', 'image_url_large'])


def backwards(apps, schema_editor):
    Meta = apps.get_model('magazine_tenders', 'MagazineTenderMeta')
    for meta in _rows(Meta):
        m = ANY_ZAWYA.match(meta.image_url or '') or ANY_ZAWYA.match(meta.image_url_large or '')
        if not m:
            continue
        meta.image_url = meta.image_url_large = SITE_ORIGINAL + m.group(1)
        meta.save(update_fields=['image_url', 'image_url_large'])


class Migration(migrations.Migration):

    dependencies = [
        ('magazine_tenders', '0004_magazinetendermeta_expected_quarter'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
