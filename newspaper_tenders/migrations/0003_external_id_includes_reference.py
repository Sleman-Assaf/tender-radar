# Recompute external_id for existing newspaper tenders.
#
# make_external_id now includes the reference number in the identity key
# (one newspaper ad can list dozens of tenders differing only by tender
# number — without the ref they all dedup into one saved tender). Existing
# rows were saved under the old title|entity|deadline key; without this
# backfill, re-processing an edition would see every old tender as "new"
# and create a duplicate.

from django.db import migrations


def _recompute(apps, schema_editor):
    from newspaper_tenders.pipeline import make_external_id

    Tender = apps.get_model('tenders', 'Tender')
    Meta = apps.get_model('newspaper_tenders', 'NewspaperTenderMeta')

    for meta in Meta.objects.select_related('tender', 'tender__source'):
        t = meta.tender
        prefix = (t.external_id or 'alrai-').split('-', 1)[0] or 'alrai'
        new_id = make_external_id(t.title or '', t.entity or '',
                                  t.submission_deadline,
                                  reference_no=t.reference_no or '',
                                  prefix=prefix)
        if new_id == t.external_id:
            continue
        clash = Tender.objects.filter(source=t.source, external_id=new_id) \
                              .exclude(pk=t.pk).exists()
        if clash:
            continue  # keep the old id rather than violate the unique index
        t.external_id = new_id
        t.save(update_fields=['external_id'])


class Migration(migrations.Migration):

    dependencies = [
        ('newspaper_tenders', '0002_newspapertendermeta_ad_image'),
        ('tenders', '0048_userprofile_notify_feedback_email'),
    ]

    operations = [
        migrations.RunPython(_recompute, migrations.RunPython.noop),
    ]
