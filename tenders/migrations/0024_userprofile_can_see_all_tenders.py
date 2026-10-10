# tenders/migrations/0024_userprofile_can_see_all_tenders.py
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenders', '0023_userfeedback_attachment'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='can_see_all_tenders',
            field=models.BooleanField(
                default=False,
                verbose_name='Can See All Tenders',
                help_text='Admin-controlled. When enabled, user can switch between AEC and All Tenders.',
            ),
        ),
    ]
