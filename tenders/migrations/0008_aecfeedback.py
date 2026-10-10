# tenders/migrations/0006_aecfeedback.py
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('tenders', '0007_remove_userprofile_aec_only'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='AECFeedback',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False)),
                ('vote', models.CharField(choices=[('UP', 'Accurate'), ('DOWN', 'Inaccurate')], max_length=4)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('tender', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='aec_feedbacks', to='tenders.tender')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='aec_feedbacks', to=settings.AUTH_USER_MODEL)),
            ],
            options={'verbose_name': 'AEC Feedback', 'verbose_name_plural': 'AEC Feedbacks', 'unique_together': {('tender', 'user')}},
        ),
    ]
