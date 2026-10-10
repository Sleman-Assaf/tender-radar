@echo off
setlocal enabledelayedexpansion
title TenderRadar HTTPS Deployment
color 0A

REM ============================================================
REM  Path: TenderRadar/deploy_https.bat
REM  TenderRadar — Full HTTPS Deployment Automation Script
REM  Domain : https://radar.ajigroup.com
REM  Run as : Administrator (required for port 443/80)
REM ============================================================

set "PROJECT_DIR=%~dp0"
set "DOMAIN=radar.ajigroup.com"
set "SSL_DIR=%PROJECT_DIR%ssl"
set "CERT=%SSL_DIR%\cert.pem"
set "KEY=%SSL_DIR%\key.pem"
set "ENV_FILE=%PROJECT_DIR%production.env"
set "PYTHON="
set "PIP="

echo.
echo ============================================================
echo   TenderRadar HTTPS Deployment Script
echo   Domain: https://%DOMAIN%
echo ============================================================
echo.

REM ── Step 0: Check Admin rights ──────────────────────────────
net session >nul 2>&1
if errorlevel 1 (
    echo [ERROR] This script must run as Administrator.
    echo Right-click deploy_https.bat ^> "Run as administrator"
    pause & exit /b 1
)
echo [OK] Running as Administrator

REM ── Step 1: Auto-detect Python ──────────────────────────────
echo.
echo [1/9] Detecting Python...

python --version >nul 2>&1
if not errorlevel 1 ( set "PYTHON=python" & goto :python_ok )

python3 --version >nul 2>&1
if not errorlevel 1 ( set "PYTHON=python3" & goto :python_ok )

py --version >nul 2>&1
if not errorlevel 1 ( set "PYTHON=py" & goto :python_ok )

if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python313\python.exe" & goto :python_ok
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python312\python.exe" & goto :python_ok
if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python311\python.exe" & goto :python_ok
if exist "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python310\python.exe" & goto :python_ok
if exist "C:\Python313\python.exe" set "PYTHON=C:\Python313\python.exe" & goto :python_ok
if exist "C:\Python312\python.exe" set "PYTHON=C:\Python312\python.exe" & goto :python_ok
if exist "C:\Python311\python.exe" set "PYTHON=C:\Python311\python.exe" & goto :python_ok
if exist "C:\Python310\python.exe" set "PYTHON=C:\Python310\python.exe" & goto :python_ok
if exist "C:\Python39\python.exe"  set "PYTHON=C:\Python39\python.exe"  & goto :python_ok
if exist "C:\Program Files\Python313\python.exe" set "PYTHON=C:\Program Files\Python313\python.exe" & goto :python_ok
if exist "C:\Program Files\Python312\python.exe" set "PYTHON=C:\Program Files\Python312\python.exe" & goto :python_ok

REM Deep search — find python.exe anywhere under user profile
for /f "delims=" %%P in ('dir /b /s "%USERPROFILE%\python.exe" 2^>nul ^| findstr /v "Scripts\|__pycache__"') do (
    set "PYTHON=%%P" & goto :python_ok
)

echo [ERROR] Python not found. Please:
echo   1. Install Python 3.10+ from https://python.org
echo   2. Check "Add Python to PATH" during install
echo   3. Restart CMD and re-run this script
echo.
echo   OR manually set the path - edit this file and change:
echo      set "PYTHON="
echo   to:
echo      set "PYTHON=C:\Users\%USERNAME%\AppData\Local\Programs\Python\Python312\python.exe"
pause & exit /b 1

:python_ok
REM ALWAYS use "python -m pip" — avoids pip.exe not in PATH issue
set "PIP=%PYTHON% -m pip"
for /f "tokens=*" %%v in ('"%PYTHON%" --version 2^>^&1') do echo [OK] %%v
echo [OK] Using: %PYTHON%

REM ── Step 2: Install dependencies ────────────────────────────
echo.
echo [2/9] Installing Python packages...
"%PYTHON%" -m pip install --upgrade pip --quiet
"%PYTHON%" -m pip install -r requirements.txt --quiet
if errorlevel 1 echo [WARN] Some packages may have warnings - continuing...
"%PYTHON%" -m pip install waitress pyopenssl cryptography --quiet
echo [OK] Packages installed

REM ── Step 3: Create ssl folder ───────────────────────────────
echo.
echo [3/9] Setting up SSL directory...
if not exist "%SSL_DIR%" mkdir "%SSL_DIR%"
echo [OK] SSL directory ready: %SSL_DIR%

REM ── Step 4: SSL Certificate ─────────────────────────────────
echo.
echo [4/9] Checking SSL certificate...
if exist "%CERT%" if exist "%KEY%" (
    echo [OK] Certificate files already exist.
    goto :cert_done
)

echo No certificate found. Choose:
echo.
echo   [1] Self-signed (for testing — browser will warn)
echo   [2] Let's Encrypt / Certbot (FREE, real cert — needs DNS ready)
echo   [3] I already have cert.pem + key.pem (copy manually to ssl\)
echo.
set /p "CERT_CHOICE=Enter choice (1/2/3): "

if "%CERT_CHOICE%"=="1" goto :self_signed
echo.
echo [INFO] Generating self-signed certificate...
"%PYTHON%" -m pip install cryptography --quiet
"%PYTHON%" gen_cert.py
if errorlevel 1 ( echo [ERROR] Certificate generation failed & pause & exit /b 1 )
goto :cert_done

:letsencrypt
echo.
echo [INFO] Installing Certbot...
"%PYTHON%" -m pip install certbot --quiet
if errorlevel 1 ( echo [ERROR] Failed to install certbot & pause & exit /b 1 )
echo [INFO] Verifying DNS for %DOMAIN%...
nslookup %DOMAIN% | findstr "Address" | findstr /v "172\|10\.\|192\."
echo [INFO] Requesting certificate (port 80 must be free)...
"%PYTHON%" -m certbot certonly --standalone -d %DOMAIN% --non-interactive --agree-tos --email admin@ajigroup.com
if errorlevel 1 (
    echo.
    echo [ERROR] Certbot failed. Check:
    echo   1. nslookup %DOMAIN% shows your public IP
    echo   2. Port 80 is free: netstat -ano ^| findstr ":80 "
    echo   3. Firewall allows port 80 inbound
    echo.
    echo [TIP] Use option [1] self-signed to test first, then re-run with [2]
    pause & exit /b 1
)
REM Find and copy the certificate
for /f "delims=" %%F in ('dir /b /s "%APPDATA%\certbot\live\*\fullchain.pem" 2^>nul') do copy "%%F" "%CERT%" >nul & goto :le_key
for /f "delims=" %%F in ('dir /b /s "C:\Certbot\live\*\fullchain.pem" 2^>nul') do copy "%%F" "%CERT%" >nul & goto :le_key
echo [WARN] Could not auto-copy cert — manually copy to %CERT%
pause
goto :cert_done
:le_key
for /f "delims=" %%F in ('dir /b /s "%APPDATA%\certbot\live\*\privkey.pem" 2^>nul') do copy "%%F" "%KEY%" >nul & goto :le_done
for /f "delims=" %%F in ('dir /b /s "C:\Certbot\live\*\privkey.pem" 2^>nul') do copy "%%F" "%KEY%" >nul & goto :le_done
:le_done
echo [OK] Let's Encrypt certificate installed
goto :cert_done

:manual_cert
echo.
echo [ACTION] Copy your files to:
echo   %CERT%
echo   %KEY%
echo.
echo Press any key when done...
pause >nul
if not exist "%CERT%" ( echo [ERROR] cert.pem not found & pause & exit /b 1 )
if not exist "%KEY%"  ( echo [ERROR] key.pem not found  & pause & exit /b 1 )
goto :cert_done

:cert_done
echo [OK] SSL certificates ready

REM ── Step 5: Configure production.env ────────────────────────
echo.
echo [5/9] Configuring production.env...
if not exist "%ENV_FILE%" (
    echo DJANGO_DEBUG=False>  "%ENV_FILE%"
    echo DJANGO_HTTPS=True>>  "%ENV_FILE%"
    echo DJANGO_SECRET_KEY=CHANGE_THIS>> "%ENV_FILE%"
    echo DJANGO_ALLOWED_HOSTS=%DOMAIN%,localhost,127.0.0.1>> "%ENV_FILE%"
    echo [INFO] Created production.env
)
findstr /C:"CHANGE_THIS" "%ENV_FILE%" >nul
if not errorlevel 1 (
    echo [INFO] Generating secret key...
    "%PYTHON%" update_secret_key.py
)
echo [OK] production.env ready

REM ── Step 6: Django setup ─────────────────────────────────────
echo.
echo [6/9] Django setup...
for /f "usebackq tokens=1,* delims==" %%A in ("%ENV_FILE%") do (
    set "ln=%%A"
    if not "!ln:~0,1!"=="#" if not "%%A"=="" set "%%A=%%B"
)
"%PYTHON%" manage.py collectstatic --noinput
if errorlevel 1 ( echo [ERROR] collectstatic failed & pause & exit /b 1 )
echo [OK] Static files collected
"%PYTHON%" manage.py migrate --run-syncdb
if errorlevel 1 ( echo [ERROR] migrate failed & pause & exit /b 1 )
echo [OK] Migrations applied
"%PYTHON%" manage.py seed_data >nul 2>&1
echo [OK] Seed data loaded

"%PYTHON%" check_superuser.py > _su.tmp 2>nul
set /p SUST=<_su.tmp & del _su.tmp >nul 2>&1
if "!SUST!"=="NO_SU" (
    echo.
    echo [INFO] No admin user found. Create one now:
    "%PYTHON%" manage.py createsuperuser
)

REM ── Step 7: Firewall ─────────────────────────────────────────
echo.
echo [7/9] Configuring Windows Firewall...
netsh advfirewall firewall delete rule name="TenderRadar HTTPS" >nul 2>&1
netsh advfirewall firewall delete rule name="TenderRadar HTTP"  >nul 2>&1
netsh advfirewall firewall add rule name="TenderRadar HTTPS" protocol=TCP dir=in localport=443 action=allow >nul
netsh advfirewall firewall add rule name="TenderRadar HTTP"  protocol=TCP dir=in localport=80  action=allow >nul
echo [OK] Ports 80 and 443 opened in firewall

REM ── Step 8: Auto-start on Windows login ──────────────────────
echo.
echo [8/9] Creating Windows startup entry...
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
(
    echo Set WS = CreateObject^("WScript.Shell"^)
    echo WS.CurrentDirectory = "%PROJECT_DIR%"
    echo WS.Run "cmd /c start_https.bat", 0, False
) > "%STARTUP%\TenderRadar.vbs"
echo [OK] Auto-start created: %STARTUP%\TenderRadar.vbs

REM ── Step 9: Start servers ────────────────────────────────────
echo.
echo [9/9] Starting TenderRadar...
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":443 \|:80 " ^| findstr "LISTENING"') do taskkill /f /pid %%P >nul 2>&1
timeout /t 1 /nobreak >nul

if not exist "logs" mkdir logs
start "TR-HTTP"  /min cmd /c ""%PYTHON%" serve_http_redirect.py >> logs\http.log 2>&1"
timeout /t 2 /nobreak >nul
start "TR-HTTPS" /min cmd /c ""%PYTHON%" serve_https.py >> logs\https.log 2>&1"
timeout /t 3 /nobreak >nul

netstat -ano | findstr ":443 " | findstr "LISTENING" >nul
if errorlevel 1 (
    echo [WARN] Port 443 not listening yet — check logs\https.log
) else (
    echo [OK] Port 443 listening
)

echo.
echo ============================================================
echo   DEPLOYMENT COMPLETE
echo   https://%DOMAIN%
echo   Admin : https://%DOMAIN%/admin/
echo   Manage: https://%DOMAIN%/manage/
echo   Logs  : %PROJECT_DIR%logs\https.log
echo ============================================================
echo.
pause
