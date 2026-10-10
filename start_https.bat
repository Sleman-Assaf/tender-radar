@echo off
setlocal enabledelayedexpansion
title TenderRadar HTTPS
color 0A

REM Path: TenderRadar/start_https.bat
REM Daily startup script — self-signed certificate on port 443

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
echo [ERROR] Python not found. Set PYTHON path manually in this file.
pause & exit /b 1
:py_ok
echo [OK] Python: %PYTHON%

REM ── Auto-generate self-signed cert if missing ─────────────────
if not exist "ssl" mkdir ssl
if not exist "ssl\cert.pem" (
    echo [INFO] Generating self-signed certificate...
    "%PYTHON%" gen_cert.py
    if errorlevel 1 ( echo [ERROR] Certificate generation failed & pause & exit /b 1 )
)
echo [OK] SSL certificate: ssl\cert.pem

REM ── Check production.env ──────────────────────────────────────
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
    echo [OK] production.env created
)

REM ── Load env vars ─────────────────────────────────────────────
for /f "usebackq tokens=1,* delims==" %%A in ("production.env") do (
    set "ln=%%A"
    if not "!ln:~0,1!"=="#" if not "%%A"=="" set "%%A=%%B"
)

REM ── Kill any old servers on these ports ───────────────────────
echo [INFO] Stopping any existing servers...
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":443 \|:80 \|:8000 \|:6626 " ^| findstr "LISTENING"') do (
    taskkill /f /pid %%P >nul 2>&1
)
timeout /t 1 /nobreak >nul

REM ── Run migrations and collectstatic ─────────────────────────
echo [INFO] Applying migrations...
"%PYTHON%" manage.py migrate --run-syncdb
if errorlevel 1 ( echo [ERROR] migrate failed & pause & exit /b 1 )

echo [INFO] Collecting static files...
"%PYTHON%" manage.py collectstatic --noinput
if errorlevel 1 ( echo [WARN] collectstatic had warnings - continuing )
echo [OK] Static files collected

echo [INFO] Collecting static files...
"%PYTHON%" manage.py collectstatic --noinput
if errorlevel 1 ( echo [ERROR] collectstatic failed & pause & exit /b 1 )

REM ── Start servers ─────────────────────────────────────────────
if not exist "logs" mkdir logs

echo.
echo [INFO] Starting HTTP redirect on port 80...
start "TR-HTTP-Redirect" /min cmd /c ""%PYTHON%" serve_http_redirect.py >> logs\http.log 2>&1"
timeout /t 2 /nobreak >nul

echo [INFO] Starting HTTPS server on port 443...
start "TR-HTTPS" /min cmd /c ""%PYTHON%" serve_https.py >> logs\https.log 2>&1"
timeout /t 3 /nobreak >nul

REM ── Verify ────────────────────────────────────────────────────
netstat -ano | findstr ":443 " | findstr "LISTENING" >nul
if errorlevel 1 (
    echo.
    echo [WARN] Port 443 not yet listening. Check log:
    echo        type logs\https.log
    echo.
) else (
    echo.
    echo ============================================================
    echo   TenderRadar is running
    echo   https://radar.ajigroup.com
    echo   Note: Browser will warn about self-signed cert.
    echo   Click 'Advanced' then 'Proceed' to continue.
    echo ============================================================
    echo.
)

echo To stop  : taskkill /f /im python.exe
echo HTTPS log: logs\https.log
echo HTTP  log: logs\http.log
echo.
