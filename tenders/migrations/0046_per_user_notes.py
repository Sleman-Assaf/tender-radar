"""
Split the shared notes box into per-user private notes.

`Tender.notes` was a single text field on the tender shared by every user: anyone
could read what someone else had written and overwrite it by pressing Save. This
creates TenderUserNote, moves the existing notes to the people who wrote them,
and then drops the shared column.

As with the bookmarks in 0045, the old schema recorded no author, so ownership is
recovered from TenderView — a note can only be written from the tender's detail
page, which records a view. Notes carry no timestamp of their own (the save used
`update_fields=['notes']`, which does not touch `updated_at`), so there is no
"before the note was written" to order by: a single viewer is taken as the author,
and where several people have opened the tender the earliest is used and reported
so it can be corrected. Nothing is silently discarded — an unattributable note is
printed in full so it can be recovered from the migration output.

Operations are ordered so the copy happens while both schemas exist.
"""
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


def _note_author(views):
    """
    The view whose user most plausibly wrote a note, or None.

    A note could only be saved from the detail page, which records a view, so the
    viewers are the candidates. Notes carry no timestamp of their own — the save
    used `update_fields=['notes']`, which does not touch `updated_at` — so there
    is nothing to order against except the views themselves: a sole viewer is the
    author, and otherwise the person who opened the tender first.
    """
    views = sorted(views, key=lambda v: v.viewed_at)
    return views[0] if views else None


def move_notes_to_users(apps, schema_editor):
    Tender         = apps.get_model('tenders', 'Tender')
    TenderView     = apps.get_model('tenders', 'TenderView')
    TenderUserNote = apps.get_model('tenders', 'TenderUserNote')

    moved = ambiguous = orphaned = 0
    for tender in Tender.objects.exclude(notes='').exclude(notes=None).iterator():
        body = (tender.notes or '').strip()
        if not body:
            continue

        views  = list(TenderView.objects.filter(tender_id=tender.pk)
                                        .order_by('viewed_at')
                                        .select_related('user'))
        author = _note_author(views)
        if author is None:
            orphaned += 1
            print(f'  [notes] tender {tender.pk}: never opened by anyone, no author '
                  f'to assign - skipped. Text was: {body!r}')
            continue

        if len(views) > 1:
            ambiguous += 1
            others = ', '.join(v.user.username for v in views[1:])
            print(f'  [notes] tender {tender.pk}: opened by several people - '
                  f'assigned to the first, {author.user.username} '
                  f'(others: {others})')

        TenderUserNote.objects.get_or_create(
            tender_id=tender.pk, user_id=author.user_id,
            defaults={'body': body, 'created_at': author.viewed_at},
        )
        moved += 1

    if moved or orphaned:
        print(f'  [notes] moved {moved} note(s) to their authors, '
              f'{ambiguous} needed a best guess, {orphaned} could not be attributed')


def restore_shared_notes(apps, schema_editor):
    """
    Reverse: fold the private notes back into the single shared box.

    Where two people wrote about the same tender only one text can survive, since
    the old schema had room for exactly one — the most recently edited wins. That
    lossiness is the reason for this migration.
    """
    Tender         = apps.get_model('tenders', 'Tender')
    TenderUserNote = apps.get_model('tenders', 'TenderUserNote')

    for note in TenderUserNote.objects.all().order_by('updated_at').iterator():
        Tender.objects.filter(pk=note.tender_id).update(notes=note.body)


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('tenders', '0045_per_user_bookmarks'),
    ]

    operations = [
        # ── 1. The new per-user table ────────────────────────────────────────
        migrations.CreateModel(
            name='TenderUserNote',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name='ID')),
                ('body', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('tender', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                             related_name='user_notes',
                                             to='tenders.tender')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                           related_name='tender_user_notes',
                                           to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Private Note',
                'verbose_name_plural': 'Private Notes — ملاحظات خاصة',
                'ordering': ['-updated_at'],
            },
        ),
        migrations.AddIndex(
            model_name='tenderusernote',
            index=models.Index(fields=['user', 'tender'],
                               name='tenders_ten_user_id_98dacb_idx'),
        ),
        migrations.AlterUniqueTogether(
            name='tenderusernote',
            unique_together={('tender', 'user')},
        ),

        # ── 2. Move the data across, while both schemas exist ────────────────
        migrations.RunPython(move_notes_to_users, restore_shared_notes),

        # ── 3. Only now drop the shared column ───────────────────────────────
        migrations.RemoveField(
            model_name='tender',
            name='notes',
        ),
    ]
