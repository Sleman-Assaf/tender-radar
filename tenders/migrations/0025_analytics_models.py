# tenders/migrations/0025_analytics_models.py

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('tenders', '0024_userprofile_can_see_all_tenders'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='UserLoginLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('logged_in_at', models.DateTimeField(auto_now_add=True)),
                ('ip_address', models.GenericIPAddressField(blank=True, null=True)),
                ('user_agent', models.TextField(blank=True, default='')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='login_logs', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'User Login Log',
                'verbose_name_plural': 'User Login Logs',
                'ordering': ['-logged_in_at'],
            },
        ),
        migrations.CreateModel(
            name='TenderPortalClick',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('clicked_at', models.DateTimeField(auto_now_add=True)),
                ('tender', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='portal_clicks', to='tenders.tender')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='portal_clicks', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Portal Click',
                'verbose_name_plural': 'Portal Clicks',
                'ordering': ['-clicked_at'],
            },
        ),
    ]
