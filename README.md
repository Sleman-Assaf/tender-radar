# Tender Radar

**A Django platform for tender collection, document extraction and AI-assisted opportunity review.**

Tender Radar brings procurement opportunities from portal scrapers, newspaper PDFs and magazine/news sources into a searchable dashboard. Teams can review tenders, bookmark opportunities, add notes and manage the sources that supply them.

**بالعربية:** نظام لجمع العطاءات من المواقع والصحف، تنظيمها والبحث فيها، واستخدام الذكاء الاصطناعي للمساعدة في التصنيف واستخراج المعلومات.

## Features

- Search and country/category/date filters, tender details, bookmarks and team notes.
- Python scrapers for government portals and international procurement sources.
- Newspaper PDF processing, configurable AI extraction and optional OCR verification.
- News and magazine collection, duplicate handling and AI summaries.
- Source administration, run history, metrics and role-based permissions.
- Business development tools for project records, engineer profiles and CV generation.

External portals can change their pages or authentication. An included scraper does not guarantee that its portal is currently reachable.

## Technology

| Area | Stack |
| --- | --- |
| Application | Django 4.2, server-rendered templates, JavaScript |
| Collection | requests, BeautifulSoup, lxml, curl-cffi; optional Playwright |
| Documents | PyMuPDF, pypdf, python-docx, openpyxl |
| AI | Configurable HTTP AI services and application provider settings |
| Scheduling | APScheduler in the web process; Celery/Redis integrations |
| Storage/serving | SQLite locally; Waitress and WhiteNoise deployment support |

## Windows setup

Use **Python 3.12** for the prepared local setup. Open this project folder in VS Code and use its PowerShell terminal:

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

Open **http://127.0.0.1:8000** and sign in with the account you created. Administration uses the application's System Dashboard; `/admin/` is not mounted in this version.

If PowerShell blocks activation, use `.\.venv\Scripts\python.exe` instead of `python` in subsequent commands. Repeat the `DJANGO_SETTINGS_MODULE` assignment in each new terminal.

## Linux/macOS setup

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

The local settings use SQLite and disable automatic external scans. Redis, a GPU and an AI server are not needed to open the web application. AI extraction/enrichment requires its separately configured services.

## AI and optional integrations

`.env.example` documents variables; it is **not loaded automatically**. Set them through your shell or process manager:

```powershell
$env:AJIAI_API_BASE = "http://127.0.0.1:8001"
$env:AJIAI_API_KEY = "<your-own-key-if-required>"
```

`AJIAI_API_BASE` expects the compatible AI HTTP service, including the configured `/api/general-chat/` endpoint. A raw Ollama URL is not automatically a replacement for this API. Other provider/model options are managed through application settings. Model weights and private service credentials are not bundled.

- **Browser scrapers:** install `playwright`, then run `python -m playwright install chromium`.
- **OCR verification:** install `easyocr`; it may download models and large PyTorch dependencies.
- **Full integrations/MySQL:** use `requirements.txt`. Linux may need database development libraries for `mysqlclient`. Configure `DB_*` and use the main settings module for a non-SQLite deployment.
- **Celery tasks:** start Redis separately, configure its broker URL and run `python -m celery -A tender_radar worker --loglevel=info --pool=solo`. Use an appropriate Linux deployment for production workers.

The main web process also has APScheduler jobs. Do not run duplicate schedulers for the same workload. Newspaper and magazine automatic scheduling is intentionally off in the local configuration.

## Manual collection

After initial source seeding, inspect the command and optionally test a source:

```powershell
python manage.py run_scrapers --help
python manage.py run_scrapers --source JordanGTDScraper --dry-run --no-ai
```

Even a dry run fetches the external portal. Use it only when you intend to contact that source.

## Structure

| Folder | Purpose |
| --- | --- |
| `tender_radar/` | Settings, routes and Celery configuration |
| `tenders/` | Dashboard, tender models, scrapers and permissions |
| `newspaper_tenders/`, `magazine_tenders/` | Newspaper and news processing |
| `bd/` | Business development and CV tools |
| `sources_dynamic/` | Retained legacy source-engine schema |
| `scraper_factory/` | Retained factory code; not installed as an active app in this snapshot |
| `templates/`, `static/` | Interface and assets |

## Verification and tests

Preparation checks use Python 3.12: Django system checks, a new SQLite database, migrations, initial source seeding and page-rendering smoke checks. External portals, OCR model execution and AI integrations require separate testing.

Run the existing test suite with:

```powershell
python manage.py test --settings=tender_radar.settings_test
```

## Contents and configuration

Production databases, company import workbooks, credentials, private keys, uploads, generated static output and local environments are excluded. Supply your own accounts and configuration. No open-source license is granted unless the rights holder adds one.

## Test fixture privacy

Captured pages have transient browser-verification scripts removed. The blocked Al Arabiya business-page fixture is a minimal static representation without session tokens or client network information. Article link and image markup was preserved in the other sanitized fixtures.

TenderWell authentication requires your own `TENDERWELL_REFRESH_TOKEN` and `TENDERWELL_CLIENT_FINGERPRINT` environment values. No refresh token or browser fingerprint is included. With no token configured, the scraper skips authentication and may have limited access to public data.
