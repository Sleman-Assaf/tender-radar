# tenders/migrations/0006_userprofile.py

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('tenders', '0005_tender_is_aec'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='UserProfile',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('industry', models.CharField(
                    choices=[
                        ('AEC', 'AEC — Architecture, Engineering & Construction'),
                        ('GEN', 'ALL — All Industries (no filter)'),
                    ],
                    default='GEN',
                    max_length=10,
                    verbose_name='Industry Interest',
                    help_text='Controls which tenders appear on your dashboard.',
                )),
('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='profile',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('countries', models.ManyToManyField(
                    blank=True,
                    help_text='Leave blank to see tenders from all countries.',
                    related_name='user_profiles',
                    to='tenders.countryconfig',
                    verbose_name='Focus Countries',
                )),
            ],
            options={
                'verbose_name': 'User Profile',
                'verbose_name_plural': 'User Profiles — ملفات المستخدمين',
            },
        ),
    ]
