# tenders/migrations/0019_team_notes_notifications.py
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def create_team_leaders_group(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Group.objects.get_or_create(name='Team Leaders')


class Migration(migrations.Migration):

    dependencies = [
        ('tenders', '0018_fix_biddetail_country'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Team',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True)),
                ('name', models.CharField(max_length=120, unique=True, verbose_name='Team Name')),
                ('description', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('leader', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='led_teams',
                    to=settings.AUTH_USER_MODEL,
                    verbose_name='Team Leader',
                )),
            ],
            options={'ordering': ['name']},
        ),
        migrations.CreateModel(
            name='TeamMembership',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True)),
                ('added_at', models.DateTimeField(auto_now_add=True)),
                ('team', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='memberships',
                    to='tenders.team',
                )),
                ('user', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='team_memberships',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={'ordering': ['user__username'], 'unique_together': {('team', 'user')}},
        ),
        migrations.AddField(
            model_name='team',
            name='members',
            field=models.ManyToManyField(
                blank=True,
                related_name='teams',
                through='tenders.TeamMembership',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.CreateModel(
            name='TenderNote',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True)),
                ('body', models.TextField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('tender', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='team_notes',
                    to='tenders.tender',
                )),
                ('author', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='tender_notes',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={'ordering': ['created_at']},
        ),
        migrations.CreateModel(
            name='NoteNotification',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True)),
                ('is_read', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('note', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='notifications',
                    to='tenders.tendernote',
                )),
                ('recipient', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='note_notifications',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={'ordering': ['-created_at'], 'unique_together': {('note', 'recipient')}},
        ),
        migrations.RunPython(create_team_leaders_group, migrations.RunPython.noop),
    ]
