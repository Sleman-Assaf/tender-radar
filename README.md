# Tender Radar

**Django platform for monitoring tender sources and related news.**

**بالعربية:** منصة لمتابعة مصادر العطاءات والأخبار، وإدارة المصادر ومعالجة المحتوى، مع تكاملات ذكاء اصطناعي اختيارية.

> **Source upload pending.** The workspace disconnected during transfer. This repository currently documents the project; the prepared source files still need to be committed. The commands below apply to the complete prepared source package, not this incomplete checkout.

## Main capabilities

- Tender and news source management.
- Newspaper and magazine tender monitoring.
- Dynamic scraper generation and validation.
- Optional AI processing through a compatible external HTTP service.
- Scheduled processing with optional Celery and Redis.

## Local startup: complete prepared package

Use Python 3.12 and a fresh environment. The prepared local settings use SQLite and disable in-process schedulers.

### Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-local.txt
$env:DJANGO_SETTINGS_MODULE = "tender_radar.settings_local"
python manage.py migrate
python manage.py seed_data
python manage.py createsuperuser
python manage.py check
python manage.py runserver 127.0.0.1:8000
```

### Linux/macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-local.txt
export DJANGO_SETTINGS_MODULE=tender_radar.settings_local
python manage.py migrate
python manage.py seed_data
python manage.py createsuperuser
python manage.py runserver 127.0.0.1:8000
```

Open **http://127.0.0.1:8000** and sign in with your account. This project uses its System Dashboard; a standard Django `/admin/` route is not mounted.

## Dependencies and configuration

The local dependency set covers Django 4.2, Celery, Redis integration, HTTP parsers and document processing. The original full requirements include heavier optional scraping/OCR dependencies.

`.env.example` is a reference and is not automatically loaded. Set configuration through shell/process environment variables. External AI processing requires a compatible service; model weights are not included. Running the web interface does not start background workers or live scraping. Redis and Celery require separate setup for scheduled processing.

## Source layout

| Folder | Purpose |
| --- | --- |
| `tender_radar/` | Settings and application routes |
| `tenders/` | Tender handling and source scrapers |
| `newspaper_tenders/` | Newspaper monitoring |
| `magazine_tenders/` | Magazine/news monitoring |
| `scraper_factory/` | Scraper generation, validation and health checks |
| `sources_dynamic/` | Dynamic source handling |
| `templates/`, `static/` | Interface and assets |
| `desktop/` | Desktop/deployment tooling |

## Preparation checks

The complete prepared package passed Python parsing, Django system checks, 147 migrations on a fresh SQLite database, source seeding and authenticated page-rendering checks for the homepage, newspaper tenders and news. Full application tests and external AI/scraping integrations were not verified.

Production databases, private configuration, credentials and imported company data are excluded. No open-source license is granted unless the rights holder adds one.
