@echo off
setlocal enabledelayedexpansion
title TenderRadar HTTP
color 0B

REM Path: TenderRadar/start_http.bat
REM Plain-HTTP startup (no TLS, no certificate) on port 8005, bound to 0.0.0.0
REM so the site is reachable from other machines on the network.
REM HTTPS equivalent: start_production.bat / start_https.bat

cd /d "%~dp0"
set "PYTHON="

REM ── Detect Python (project venv first) ───────────────────────
if exist ".venv\Scripts\python.exe" set "PYTHON=%~dp0.venv\Scripts\python.exe" & goto :py_ok
if exist "venv\Scripts\python.exe"  set "PYTHON=%~dp0venv\Scripts\python.exe"  & goto :py_ok
python --version >nul 2>&1 && set "PYTHON=python" && goto :py_ok
py --version >nul 2>&1 && set "PYTHON=py" && goto :py_ok
echo [ERROR] Python not found. Set PYTHON= path manually in this file.
pause & exit /b 1
:py_ok
echo [OK] Python: %PYTHON%

REM ── Port (override: set PORT=80 before running) ───────────────
if "%PORT%"=="" set "PORT=8005"

echo.
echo ============================================================
echo   TenderRadar  ^|  HTTP  ^|  port %PORT%
echo ============================================================
echo.

if not exist "production.env" (
    echo [ERROR] production.env not found.
    echo         Copy production.env.template to production.env first.
    pause & exit /b 1
)

REM ── Load env vars from production.env ────────────────────────
for /f "usebackq tokens=1,* delims==" %%A in ("production.env") do (
    set "ln=%%A"
    if not "!ln:~0,1!"=="#" if not "%%A"=="" set "%%A=%%B"
)
REM This server never speaks TLS — force the HTTPS-only settings off.
set "DJANGO_HTTPS=False"

REM ── Stop anything already holding OUR port ───────────────────
REM Only %PORT%, deliberately. This used to blanket-kill 80/443/8000, which
REM (a) missed the port actually about to be bound once the default moved to
REM 8005, and (b) force-killed unrelated listeners — anything owned by another
REM account needs administrator rights, which is why this step could demand it.
echo [INFO] Stopping anything already listening on port %PORT%...
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":%PORT% " ^| findstr "LISTENING"') do (
    taskkill /f /pid %%P >nul 2>&1
)
timeout /t 1 /nobreak >nul

REM ── Django setup ─────────────────────────────────────────────
echo [INFO] Applying migrations...
"%PYTHON%" manage.py migrate --noinput
if errorlevel 1 ( echo [ERROR] migrate failed & pause & exit /b 1 )
echo [OK] Migrations applied

echo [INFO] Collecting static files...
"%PYTHON%" manage.py collectstatic --noinput
if errorlevel 1 ( echo [ERROR] collectstatic failed & pause & exit /b 1 )
echo [OK] Static files collected

if not exist "logs" mkdir logs

REM ── Run in the foreground (Ctrl+C to stop) ───────────────────
echo.
echo [INFO] Starting Waitress on 0.0.0.0:%PORT% ...
echo.
"%PYTHON%" serve_http.py

echo.
echo [INFO] Server stopped.
pause
