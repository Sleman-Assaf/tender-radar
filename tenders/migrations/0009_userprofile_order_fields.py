# tenders/migrations/0009_userprofile_order_fields.py
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenders', '0008_aecfeedback'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='country_order',
            field=models.TextField(
                blank=True, default='',
                verbose_name='Country Display Order',
                help_text='JSON list of country codes in preferred order.',
            ),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='source_order',
            field=models.TextField(
                blank=True, default='',
                verbose_name='Source Display Order',
                help_text='JSON list of source IDs in preferred order.',
            ),
        ),
    ]
