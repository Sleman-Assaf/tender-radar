# bd/apps.py
# Path: TenderRadar/bd/apps.py

from django.apps import AppConfig


class BdConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'bd'
    verbose_name = 'Business Development'
