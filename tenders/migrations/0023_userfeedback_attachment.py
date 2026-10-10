# tenders/migrations/0023_userfeedback_attachment.py
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenders', '0022_tendernote_team'),
    ]

    operations = [
        migrations.AddField(
            model_name='userfeedback',
            name='attachment',
            field=models.FileField(
                blank=True, null=True,
                upload_to='feedback_attachments/',
                verbose_name='Attachment',
            ),
        ),
    ]
