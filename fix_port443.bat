@echo off
setlocal enabledelayedexpansion
title Fix Port 443
color 0E

REM Path: TenderRadar/fix_port443.bat
REM Finds and kills whatever is using port 443, then starts TenderRadar
REM Run as Administrator

net session >nul 2>&1
if errorlevel 1 ( echo [ERROR] Run as Administrator & pause & exit /b 1 )

echo.
echo ============================================================
echo   Diagnosing port 443
echo ============================================================
echo.

echo [INFO] All processes listening on port 443:
echo.
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":443 " ^| findstr "LISTENING"') do (
    echo   PID: %%P
    tasklist /fi "pid eq %%P" /fo table /nh 2^>nul
    echo.
)

echo [INFO] All processes listening on port 80:
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":80 " ^| findstr "LISTENING"') do (
    echo   PID: %%P
    tasklist /fi "pid eq %%P" /fo table /nh 2^>nul
)

echo.
echo [INFO] Killing all processes on port 443 and 80...
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":443 \|:80 \|:8000 \|:6626 " ^| findstr "LISTENING"') do (
    echo   Killing PID %%P
    taskkill /f /pid %%P >nul 2>&1
)
timeout /t 2 /nobreak >nul

echo.
echo [INFO] Port 443 status after kill:
netstat -ano | findstr ":443 " | findstr "LISTENING"
if errorlevel 1 ( echo   [OK] Port 443 is free ) else ( echo   [WARN] Still occupied )

echo.
echo [INFO] Starting TenderRadar...

REM Detect Python
set "PYTHON="
python --version >nul 2>&1 && set "PYTHON=python" && goto :py_ok
python3 --version >nul 2>&1 && set "PYTHON=python3" && goto :py_ok
py --version >nul 2>&1 && set "PYTHON=py" && goto :py_ok
if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python313\python.exe" & goto :py_ok
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python312\python.exe" & goto :py_ok
if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python311\python.exe" & goto :py_ok
if exist "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python310\python.exe" & goto :py_ok
if exist "C:\Python312\python.exe" set "PYTHON=C:\Python312\python.exe" & goto :py_ok
echo [ERROR] Python not found & pause & exit /b 1
:py_ok
echo [OK] Python: %PYTHON%

if not exist "logs" mkdir logs
start "TR-HTTP"  /min cmd /c ""%PYTHON%" serve_http_redirect.py  >> logs\http.log  2>&1"
timeout /t 2 /nobreak >nul
start "TR-HTTPS" /min cmd /c ""%PYTHON%" serve_https.py          >> logs\https.log 2>&1"
timeout /t 4 /nobreak >nul

echo.
netstat -ano | findstr ":443 " | findstr "LISTENING" >nul
if errorlevel 1 (
    echo [WARN] Port 443 still not listening. Showing https.log:
    echo.
    type logs\https.log
) else (
    echo ============================================================
    echo   https://radar.ajigroup.com  is running
    echo   Click Advanced then Proceed for self-signed cert warning
    echo ============================================================
)
echo.
pause
