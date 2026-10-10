# tenders/migrations/0004_sitesettings.py

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('tenders', '0003_add_mirror_columns'),
    ]

    operations = [
        migrations.CreateModel(
            name='SiteSettings',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True,
                                        serialize=False, verbose_name='ID')),
                ('scraping_mode', models.CharField(
                    choices=[
                        ('FILTERED', 'Filtered — relevant tenders only (uses keyword list)'),
                        ('ALL',      'All — save every tender regardless of keywords'),
                    ],
                    default='FILTERED',
                    help_text=(
                        'FILTERED: only tenders matching the relevance keyword list are saved. '
                        'ALL: every scraped tender is saved and marked relevant — '
                        'ignores keyword matching entirely.'
                    ),
                    max_length=10,
                    verbose_name='Scraping Mode',
                )),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('updated_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='+',
                    to=settings.AUTH_USER_MODEL,
                    verbose_name='Last changed by',
                )),
            ],
            options={
                'verbose_name':        'Site Settings',
                'verbose_name_plural': 'Site Settings',
            },
        ),
    ]
