# Path: TenderRadar/check_superuser.py
import os, django
os.environ['DJANGO_SETTINGS_MODULE'] = 'tender_radar.settings'
django.setup()
from django.contrib.auth import get_user_model
U = get_user_model()
print('NO_SU' if not U.objects.filter(is_superuser=True).exists() else 'HAS_SU')
