# tenders/migrations/0048_userprofile_notify_feedback_email.py
# Path: TenderRadar/tenders/migrations/0048_userprofile_notify_feedback_email.py

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenders', '0047_three_categories'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='notify_feedback_email',
            field=models.BooleanField(
                default=False,
                help_text='Receive an e-mail notification whenever any user '
                          'submits feedback.',
                verbose_name='Feedback E-mail Alerts',
            ),
        ),
    ]
