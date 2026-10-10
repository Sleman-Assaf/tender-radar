# Path: tenders/migrations/0032_merge_integration_branch.py
"""
Merges the (now empty) integration side-branch back into the main history so
there is a single leaf node again.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('tenders', '0030_userprofile_mobile_number'),
        ('tenders', '0031_userprofile_phone'),
    ]

    operations = []
