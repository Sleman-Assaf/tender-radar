#!/usr/bin/env python
# manage.py
import os
import sys


def main():
    # Force UTF-8 output on Windows to avoid cp1252 UnicodeEncodeError in logs
    if sys.platform == 'win32':
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'tender_radar.settings')
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError("Couldn't import Django.") from exc
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
