# tenders/migrations/0005_tender_is_aec.py

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenders', '0004_sitesettings'),
    ]

    operations = [
        migrations.AddField(
            model_name='tender',
            name='is_aec',
            field=models.BooleanField(
                default=False,
                verbose_name='AEC Related',
                help_text='True if this tender is related to Architecture, Engineering & Construction industry',
                db_index=True,
            ),
        ),
        migrations.AddField(
            model_name='tender',
            name='is_aec_checked',
            field=models.BooleanField(
                default=False,
                help_text='True once the AJiBrain AEC classification API has been called for this tender',
            ),
        ),
    ]
