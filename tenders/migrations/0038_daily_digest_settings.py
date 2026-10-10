# Path: tenders/migrations/0038_daily_digest_settings.py
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenders', '0037_graph_interactive_signin'),
    ]

    operations = [
        migrations.AddField(
            model_name='integrationsettings',
            name='digest_enabled',
            field=models.BooleanField(
                default=False, verbose_name='Daily digest enabled',
                help_text='Send each user a daily HTML e-mail with new tenders from their preferred countries.',
            ),
        ),
        migrations.AddField(
            model_name='integrationsettings',
            name='digest_hour',
            field=models.PositiveSmallIntegerField(
                default=9, verbose_name='Send hour (0–23)',
                help_text='Local server hour the daily digest is sent (default 9 AM).',
            ),
        ),
        migrations.AddField(
            model_name='integrationsettings',
            name='digest_test_email',
            field=models.EmailField(
                blank=True, default='Munaf_Dhuhairat@ajigroup.com',
                max_length=254, verbose_name='Test recipient',
            ),
        ),
        migrations.AddField(
            model_name='integrationsettings',
            name='digest_site_url',
            field=models.URLField(
                blank=True, default='', verbose_name='TenderRadar base URL',
                help_text='Used for "Open in TenderRadar" links in the e-mail, e.g. https://tenders.ajigroup.com — leave blank to link to the source portals only.',
            ),
        ),
        migrations.AddField(
            model_name='integrationsettings',
            name='digest_last_run_at',
            field=models.DateTimeField(
                blank=True, editable=False, null=True,
                verbose_name='Digest last run at',
            ),
        ),
    ]
