# Path: TenderRadar/check_settings.py
# Run: python check_settings.py
# Shows current SiteSettings values directly from DB

import os, sys, django
os.environ['DJANGO_SETTINGS_MODULE'] = 'tender_radar.settings'
sys.path.insert(0, '.')
django.setup()

from tenders.models import SiteSettings
cfg = SiteSettings.get()
print(f'pk           : {cfg.pk}')
print(f'ai_service   : {cfg.ai_service!r}')
print(f'openai_key   : {"SET" if cfg.openai_api_key else "NOT SET"}')
print(f'openai_model : {cfg.openai_model!r}')
print(f'batch_classify : {cfg.openai_classify_batch}')
print(f'batch_translate: {cfg.openai_translate_batch}')
print(f'scraping_mode: {cfg.scraping_mode!r}')
