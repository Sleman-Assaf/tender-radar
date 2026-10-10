# tenders/migrations/0020_userfeedback.py
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('tenders', '0019_team_notes_notifications'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='UserFeedback',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True)),
                ('feedback_type', models.CharField(
                    choices=[('BUG','🐛 Bug / Issue'),('FEATURE','✨ Feature Request'),
                             ('CHANGE','🔧 Modification'),('OTHER','💬 Other')],
                    default='OTHER', max_length=10, verbose_name='Type')),
                ('title', models.CharField(max_length=200, verbose_name='Subject')),
                ('body', models.TextField(verbose_name='Details')),
                ('status', models.CharField(
                    choices=[('NEW','New'),('IN_REVIEW','In Review'),
                             ('PLANNED','Planned'),('DONE','Done'),('DECLINED','Declined')],
                    default='NEW', max_length=12)),
                ('priority', models.CharField(
                    choices=[('LOW','Low'),('MEDIUM','Medium'),('HIGH','High')],
                    default='MEDIUM', max_length=8, verbose_name='Priority')),
                ('admin_note', models.TextField(blank=True, verbose_name='Admin Response')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('author', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='feedbacks', to=settings.AUTH_USER_MODEL)),
                ('reviewed_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='reviewed_feedbacks', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-created_at']},
        ),
    ]
