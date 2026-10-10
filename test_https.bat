@echo off
title Test HTTPS Server
color 0A

REM Path: TenderRadar/test_https.bat
REM Run serve_https.py directly in foreground to see exact errors

cd /d "%~dp0"
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
echo [OK] Working dir: %CD%
echo.
echo [INFO] Loading production.env...
for /f "usebackq tokens=1,* delims==" %%A in ("production.env") do (
    set "ln=%%A"
    if not "!ln:~0,1!"=="#" if not "%%A"=="" set "%%A=%%B"
)

echo [INFO] Checking cert files...
if exist "ssl\cert.pem" ( echo [OK] ssl\cert.pem found ) else ( echo [ERROR] ssl\cert.pem MISSING )
if exist "ssl\key.pem"  ( echo [OK] ssl\key.pem found  ) else ( echo [ERROR] ssl\key.pem MISSING  )

echo.
echo [INFO] Running serve_https.py in foreground (Ctrl+C to stop)...
echo        Any errors will show below:
echo ============================================================
echo.

"%PYTHON%" serve_https.py

echo.
echo ============================================================
echo [INFO] Server exited. See errors above.
pause
