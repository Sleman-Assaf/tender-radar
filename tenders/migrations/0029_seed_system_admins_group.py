# Path: tenders/migrations/0029_seed_system_admins_group.py
"""
No-op placeholder — the 'System Admins' group is now seeded in
0033_integrationsettings (kept here so already-applied databases stay valid).
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('tenders', '0028_integrationsettings'),
    ]

    operations = []
