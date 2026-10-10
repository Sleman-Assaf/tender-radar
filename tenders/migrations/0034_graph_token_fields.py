# Path: tenders/migrations/0034_graph_token_fields.py
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenders', '0033_integrationsettings'),
    ]

    operations = [
        migrations.AddField(
            model_name='integrationsettings',
            name='graph_access_token',
            field=models.TextField(blank=True, default='', editable=False,
                                   verbose_name='Graph access token (cached)'),
        ),
        migrations.AddField(
            model_name='integrationsettings',
            name='graph_token_expires_at',
            field=models.DateTimeField(blank=True, editable=False, null=True,
                                       verbose_name='Graph token expires at'),
        ),
        migrations.AddField(
            model_name='integrationsettings',
            name='graph_token_acquired_at',
            field=models.DateTimeField(blank=True, editable=False, null=True,
                                       verbose_name='Graph token acquired at'),
        ),
    ]
