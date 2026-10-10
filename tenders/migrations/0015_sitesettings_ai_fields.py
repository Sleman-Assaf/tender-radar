# tenders/migrations/0015_sitesettings_ai_fields.py
from django.db import migrations, models

class Migration(migrations.Migration):

    dependencies = [
        ('tenders', '0014_sitesettings_ai_service'),
    ]

    operations = [
        migrations.AddField(
            model_name='sitesettings',
            name='ai_service',
            field=models.CharField(
                choices=[('AJIBRAIN','AJiBrain (local Ollama — default)'),('OPENAI','OpenAI API (GPT-4o-mini — batch per scraper)')],
                default='AJIBRAIN', max_length=10, verbose_name='AI Service',
            ),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='ai_classify',
            field=models.BooleanField(default=False, verbose_name='AI Classification'),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='ai_translate',
            field=models.BooleanField(default=False, verbose_name='AI Translation'),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='openai_api_key',
            field=models.CharField(blank=True, default='', max_length=200, verbose_name='OpenAI API Key'),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='openai_model',
            field=models.CharField(blank=True, default='gpt-4o-mini', max_length=100, verbose_name='OpenAI Model'),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='ajibrain_url',
            field=models.CharField(blank=True, default='http://localhost:8001', max_length=200, verbose_name='AJiBrain URL'),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='ajibrain_model',
            field=models.CharField(blank=True, default='qwen2.5:14b', max_length=100, verbose_name='AJiBrain Model'),
        ),
    ]
