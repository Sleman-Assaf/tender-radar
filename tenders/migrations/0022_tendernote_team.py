# tenders/migrations/0022_tendernote_team.py
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('tenders', '0021_feedbacknotification'),
    ]

    operations = [
        migrations.AddField(
            model_name='tendernote',
            name='team',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='notes',
                to='tenders.team',
            ),
        ),
    ]
