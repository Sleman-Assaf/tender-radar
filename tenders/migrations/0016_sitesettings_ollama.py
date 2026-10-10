# tenders/migrations/0016_sitesettings_ollama.py
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenders', '0015_sitesettings_ai_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='sitesettings',
            name='ollama_base_url',
            field=models.CharField(blank=True, default='http://localhost:11434',
                                   max_length=200, verbose_name='Ollama Base URL'),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='ollama_model',
            field=models.CharField(blank=True, default='qwen2.5:7b',
                                   max_length=100, verbose_name='Ollama Model'),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='ollama_classify_batch',
            field=models.PositiveIntegerField(default=20,
                                              verbose_name='Ollama Classification Batch Size'),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='ollama_translate_batch',
            field=models.PositiveIntegerField(default=10,
                                              verbose_name='Ollama Translation Batch Size'),
        ),
        migrations.AlterField(
            model_name='sitesettings',
            name='ai_service',
            field=models.CharField(
                choices=[
                    ('AJIBRAIN', 'AJiBrain (local Ollama — default)'),
                    ('OPENAI',   'OpenAI API (GPT-4o-mini — batch per scraper)'),
                    ('OLLAMA',   'Ollama (direct — local Claude/Llama/Qwen models)'),
                ],
                default='AJIBRAIN', max_length=10, verbose_name='AI Service',
            ),
        ),
    ]
