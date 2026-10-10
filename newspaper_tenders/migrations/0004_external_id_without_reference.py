# Recompute newspaper tender external_ids WITHOUT the reference number.
#
# 0003 stamped ref-included ids to keep every tender of a multi-tender list
# ad. Management then decided (24 Aug 2026) a list ad is ONE tender row —
# the pipeline now merges list ads before saving and derives identity from
# title|entity|deadline only. This backfill re-stamps existing rows to that
# formula so re-processing an edition matches them instead of duplicating.

from django.db import migrations


def _recompute(apps, schema_editor):
    from newspaper_tenders.pipeline import make_external_id

    Tender = apps.get_model('tenders', 'Tender')
    Meta = apps.get_model('newspaper_tenders', 'NewspaperTenderMeta')

    for meta in Meta.objects.select_related('tender', 'tender__source'):
        t = meta.tender
        prefix = (t.external_id or 'alrai-').split('-', 1)[0] or 'alrai'
        new_id = make_external_id(t.title or '', t.entity or '',
                                  t.submission_deadline, prefix=prefix)
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
        ('newspaper_tenders', '0003_external_id_includes_reference'),
    ]

    operations = [
        migrations.RunPython(_recompute, migrations.RunPython.noop),
    ]
