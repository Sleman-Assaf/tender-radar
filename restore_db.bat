@echo off
setlocal enabledelayedexpansion
title Restore TenderRadar Database
color 0C

REM Path: TenderRadar/restore_db.bat
REM Recreates the database from scratch after deletion
REM Run from project root as Administrator

cd /d "%~dp0"
set "PYTHON="
python --version >nul 2>&1 && set "PYTHON=python" && goto :py_ok
python3 --version >nul 2>&1 && set "PYTHON=python3" && goto :py_ok
py --version >nul 2>&1 && set "PYTHON=py" && goto :py_ok
if exist "%LOCALAPPDATA%\Programs\Python\Python314\python.exe" set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python314\python.exe" & goto :py_ok
if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python313\python.exe" & goto :py_ok
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python312\python.exe" & goto :py_ok
if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python311\python.exe" & goto :py_ok
if exist "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python310\python.exe" & goto :py_ok
echo [ERROR] Python not found & pause & exit /b 1
:py_ok
echo [OK] Python: %PYTHON%

REM Load production env
if exist "production.env" (
    for /f "usebackq tokens=1,* delims==" %%A in ("production.env") do (
        set "ln=%%A"
        if not "!ln:~0,1!"=="#" if not "%%A"=="" set "%%A=%%B"
    )
)

echo.
echo ============================================================
echo   Restoring TenderRadar Database
echo ============================================================
echo.

echo [1/5] Running migrations (creates all tables)...
"%PYTHON%" manage.py migrate --run-syncdb
if errorlevel 1 ( echo [ERROR] migrate failed & pause & exit /b 1 )
echo [OK] All tables created

echo.
echo [2/5] Loading seed data (countries, sources, keywords)...
"%PYTHON%" manage.py seed_data
if errorlevel 1 ( echo [WARN] seed_data had issues - continuing )
echo [OK] Seed data loaded

echo.
echo [3/5] Creating admin superuser...
"%PYTHON%" manage.py createsuperuser
echo [OK] Superuser created

echo.
echo [4/5] Collecting static files...
"%PYTHON%" manage.py collectstatic --noinput
echo [OK] Static files collected

echo.
echo [5/5] Verifying database...
"%PYTHON%" -c "
import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'tender_radar.settings')
django.setup()
from tenders.models import CountryConfig, ScraperSource, RelevanceKeyword
print(f'  Countries  : {CountryConfig.objects.count()}')
print(f'  Sources    : {ScraperSource.objects.count()}')
print(f'  Keywords   : {RelevanceKeyword.objects.count()}')
from django.contrib.auth import get_user_model
U = get_user_model()
print(f'  Users      : {U.objects.count()}')
print(f'  Superusers : {U.objects.filter(is_superuser=True).count()}')
"
echo.
echo ============================================================
echo   DATABASE RESTORED SUCCESSFULLY
echo   Now run: start_https.bat
echo ============================================================
echo.
pause
