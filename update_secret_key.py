# Path: TenderRadar/update_secret_key.py
import sys, pathlib
env_file = pathlib.Path('production.env')
new_key  = sys.argv[1] if len(sys.argv) > 1 else ''
if not new_key:
    from django.core.management.utils import get_random_secret_key
    new_key = get_random_secret_key()
lines = env_file.read_text(encoding='utf-8').splitlines()
out = []
for line in lines:
    if line.startswith('DJANGO_SECRET_KEY='):
        out.append(f'DJANGO_SECRET_KEY={new_key}')
    else:
        out.append(line)
env_file.write_text('\n'.join(out) + '\n', encoding='utf-8')
print('[OK] Secret key updated in production.env')
