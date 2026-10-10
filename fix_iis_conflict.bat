@echo off
title Fix IIS Port Conflict
color 0E

REM Path: TenderRadar/fix_iis_conflict.bat
REM Stops IIS from intercepting port 443 so serve_https.py can use it
REM Run as Administrator

net session >nul 2>&1
if errorlevel 1 ( echo Run as Administrator & pause & exit /b 1 )

echo.
echo ============================================================
echo   Fixing IIS / Port 443 conflict
echo ============================================================
echo.

echo [INFO] Checking what is listening on port 443...
netstat -ano | findstr ":443 " | findstr "LISTENING"
echo.

echo [INFO] Checking IIS status...
sc query W3SVC | findstr "STATE"
echo.

echo Choose action:
echo   [1] Stop IIS completely (recommended — TenderRadar replaces it)
echo   [2] Stop only the site using port 443 in IIS
echo   [3] Just show me what's on port 443
echo.
set /p "CHOICE=Enter choice (1/2/3): "

if "%CHOICE%"=="1" goto :stop_iis
if "%CHOICE%"=="2" goto :stop_site
if "%CHOICE%"=="3" goto :show_only
goto :stop_iis

:stop_iis
echo.
echo [INFO] Stopping IIS (W3SVC)...
net stop W3SVC /y >nul 2>&1
net stop WAS   /y >nul 2>&1
iisreset /stop >nul 2>&1
echo [INFO] Disabling IIS auto-start...
sc config W3SVC start= demand >nul 2>&1
sc config WAS   start= demand >nul 2>&1
echo [OK] IIS stopped and set to manual start
echo.
echo [INFO] Verifying port 443 is now free...
netstat -ano | findstr ":443 " | findstr "LISTENING"
if errorlevel 1 (
    echo [OK] Port 443 is free
) else (
    echo [WARN] Something still on 443 — see above
)
goto :restart_radar

:stop_site
echo.
echo [INFO] IIS sites using port 443:
%windir%\system32\inetsrv\appcmd list site /bindings | findstr "https"
echo.
set /p "SITENAME=Enter site name to stop (or press Enter to stop all): "
if "%SITENAME%"=="" (
    %windir%\system32\inetsrv\appcmd stop sites /name:* >nul 2>&1
    echo [OK] All IIS sites stopped
) else (
    %windir%\system32\inetsrv\appcmd stop site /site.name:"%SITENAME%" >nul 2>&1
    echo [OK] Site "%SITENAME%" stopped
)
goto :restart_radar

:show_only
echo.
echo [INFO] Processes on port 443:
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":443 " ^| findstr "LISTENING"') do (
    echo   PID %%P:
    tasklist /fi "pid eq %%P" /fo table /nh
)
echo.
echo [INFO] IIS status:
sc query W3SVC | findstr "STATE\|NAME"
echo.
pause
exit /b 0

:restart_radar
echo.
echo [INFO] Now starting TenderRadar on port 443...

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
echo [ERROR] Python not found
pause & exit /b 1
:py_ok

REM Kill old python servers
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":443 \|:80 \|:8000 \|:6626 " ^| findstr "LISTENING"') do (
    taskkill /f /pid %%P >nul 2>&1
)
timeout /t 1 /nobreak >nul

if not exist "logs" mkdir logs

start "TR-HTTP"  /min cmd /c ""%PYTHON%" serve_http_redirect.py >> logs\http.log 2>&1"
timeout /t 2 /nobreak >nul
start "TR-HTTPS" /min cmd /c ""%PYTHON%" serve_https.py >> logs\https.log 2>&1"
timeout /t 3 /nobreak >nul

netstat -ano | findstr ":443 " | findstr "LISTENING" >nul
if errorlevel 1 (
    echo [WARN] Port 443 still not listening. Check logs\https.log
) else (
    echo.
    echo ============================================================
    echo   SUCCESS — https://radar.ajigroup.com is running
    echo   (Click Advanced → Proceed for self-signed cert warning)
    echo ============================================================
)
echo.
pause
