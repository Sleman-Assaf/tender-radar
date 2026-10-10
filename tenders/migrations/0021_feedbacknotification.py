# tenders/migrations/0021_feedbacknotification.py
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('tenders', '0020_userfeedback'),
    ]

    operations = [
        migrations.CreateModel(
            name='FeedbackNotification',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True)),
                ('is_read', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now=True)),
                ('feedback', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='notification',
                    to='tenders.userfeedback',
                )),
            ],
        ),
    ]
