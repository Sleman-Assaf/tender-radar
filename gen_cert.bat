@echo off
REM Path: TenderRadar/gen_cert.bat
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

echo [INFO] Installing cryptography...
"%PYTHON%" -m pip install cryptography --quiet

echo [INFO] Generating certificate...
if not exist "ssl" mkdir ssl
del /f /q "ssl\cert.pem" >nul 2>&1
del /f /q "ssl\key.pem"  >nul 2>&1

"%PYTHON%" gen_cert.py
if errorlevel 1 ( echo [ERROR] Failed & pause & exit /b 1 )

echo.
if exist "ssl\cert.pem" ( echo [OK] ssl\cert.pem ) else ( echo [ERROR] ssl\cert.pem MISSING )
if exist "ssl\key.pem"  ( echo [OK] ssl\key.pem  ) else ( echo [ERROR] ssl\key.pem MISSING  )
echo.
echo Now run: test_https.bat
pause
