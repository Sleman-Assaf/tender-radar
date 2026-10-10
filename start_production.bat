@echo off
setlocal enabledelayedexpansion
title TenderRadar Production
color 0A

REM Path: TenderRadar/start_production.bat
REM Production startup — self-signed cert, port 443
REM Identical to start_https.bat — use either one

cd /d "%~dp0"
set "PYTHON="

REM ── Detect Python ────────────────────────────────────────────
python --version >nul 2>&1 && set "PYTHON=python" && goto :py_ok
python3 --version >nul 2>&1 && set "PYTHON=python3" && goto :py_ok
py --version >nul 2>&1 && set "PYTHON=py" && goto :py_ok
if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python313\python.exe" & goto :py_ok
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python312\python.exe" & goto :py_ok
if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python311\python.exe" & goto :py_ok
if exist "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python310\python.exe" & goto :py_ok
if exist "C:\Python313\python.exe" set "PYTHON=C:\Python313\python.exe" & goto :py_ok
if exist "C:\Python312\python.exe" set "PYTHON=C:\Python312\python.exe" & goto :py_ok
if exist "C:\Python311\python.exe" set "PYTHON=C:\Python311\python.exe" & goto :py_ok
if exist "C:\Python310\python.exe" set "PYTHON=C:\Python310\python.exe" & goto :py_ok
echo [ERROR] Python not found. Set PYTHON= path manually in this file.
pause & exit /b 1
:py_ok
echo [OK] Python: %PYTHON%

echo.
echo ============================================================
echo   TenderRadar Production  ^|  https://radar.ajigroup.com
echo ============================================================
echo.

REM ── Auto-generate self-signed cert if missing ─────────────────
if not exist "ssl" mkdir ssl
if not exist "ssl\cert.pem" (
    echo [INFO] Generating self-signed certificate...
    "%PYTHON%" gen_cert.py
    if errorlevel 1 ( echo [ERROR] Certificate generation failed & pause & exit /b 1 )
)
echo [OK] SSL certificate ready

REM ── Check / create production.env ────────────────────────────
if not exist "production.env" (
    echo [INFO] Creating production.env...
    (
        echo DJANGO_DEBUG=False
        echo DJANGO_HTTPS=True
        echo DJANGO_ALLOWED_HOSTS=radar.ajigroup.com,localhost,127.0.0.1
    ) > "production.env"
    for /f "delims=" %%K in ('"%PYTHON%" -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"') do (
        echo DJANGO_SECRET_KEY=%%K>> "production.env"
    )
    echo [OK] production.env created with new secret key
)

REM ── Load env vars ─────────────────────────────────────────────
for /f "usebackq tokens=1,* delims==" %%A in ("production.env") do (
    set "ln=%%A"
    if not "!ln:~0,1!"=="#" if not "%%A"=="" set "%%A=%%B"
)

REM ── Kill any existing servers ─────────────────────────────────
echo [INFO] Stopping existing servers on ports 80/443/8000/6626...
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":443 \|:80 \|:8000 \|:6626 " ^| findstr "LISTENING"') do (
    taskkill /f /pid %%P >nul 2>&1
)
timeout /t 1 /nobreak >nul

REM ── Django setup ──────────────────────────────────────────────
echo [INFO] Applying migrations...
"%PYTHON%" manage.py migrate --run-syncdb
if errorlevel 1 ( echo [ERROR] migrate failed & pause & exit /b 1 )
echo [OK] Migrations applied

echo [INFO] Collecting static files...
"%PYTHON%" manage.py collectstatic --noinput
if errorlevel 1 ( echo [ERROR] collectstatic failed & pause & exit /b 1 )
echo [OK] Static files collected

REM ── Start servers ─────────────────────────────────────────────
if not exist "logs" mkdir logs

echo.
echo [INFO] Starting HTTP redirect (port 80)...
start "TR-HTTP-Redirect" /min cmd /c ""%PYTHON%" serve_http_redirect.py >> logs\http.log 2>&1"
timeout /t 2 /nobreak >nul

echo [INFO] Starting HTTPS server (port 443)...
start "TR-HTTPS" /min cmd /c ""%PYTHON%" serve_https.py >> logs\https.log 2>&1"
timeout /t 3 /nobreak >nul

REM ── Status check ─────────────────────────────────────────────
netstat -ano | findstr ":443 " | findstr "LISTENING" >nul
if errorlevel 1 (
    echo.
    echo [WARN] Port 443 not listening yet. Check: type logs\https.log
) else (
    echo.
    echo ============================================================
    echo   RUNNING  ^|  https://radar.ajigroup.com
    echo   Admin    ^|  https://radar.ajigroup.com/admin/
    echo   Manage   ^|  https://radar.ajigroup.com/manage/
    echo.
    echo   Browser will warn about self-signed certificate.
    echo   Click 'Advanced' then 'Proceed to radar.ajigroup.com'
    echo ============================================================
)
echo.
echo To stop  : taskkill /f /im python.exe
echo HTTPS log: logs\https.log
echo HTTP  log: logs\http.log
echo.
