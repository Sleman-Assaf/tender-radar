# Path: tenders/migrations/0030_userprofile_mobile_number.py
"""
No-op placeholder — UserProfile.phone (0031_userprofile_phone) is used for
contact numbers, so no separate mobile_number field is added.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('tenders', '0029_seed_system_admins_group'),
    ]

    operations = []
