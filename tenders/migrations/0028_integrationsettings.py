# Path: tenders/migrations/0028_integrationsettings.py
"""
No-op placeholder.

This migration originally created the IntegrationSettings table on a side
branch, which conflicted with the main history (0028_alter_userfeedback...).
It is kept as an empty migration so databases that already recorded it stay
consistent. The real table is created in 0033_integrationsettings.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('tenders', '0027_sitesettings_ai_classify_sitesettings_ai_service_and_more'),
    ]

    operations = []
