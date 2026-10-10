# tenders/migrations/0014_sitesettings_ai_service.py
"""
Adds new OpenAI batch fields to SiteSettings.
The ai_service, openai_api_key etc are added in 0015.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenders', '0013_countryconfig_is_organization'),
    ]

    operations = [
        migrations.AddField(
            model_name='sitesettings',
            name='openai_classify_batch',
            field=models.PositiveIntegerField(
                default=50,
                verbose_name='Classification Batch Size',
            ),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='openai_translate_batch',
            field=models.PositiveIntegerField(
                default=30,
                verbose_name='Translation Batch Size',
            ),
        ),
    ]
