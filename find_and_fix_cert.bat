@echo off
setlocal enabledelayedexpansion
title Fix Certificate
color 0A

REM Path: TenderRadar/find_and_fix_cert.bat
REM Finds existing cert.pem anywhere on the machine and copies to ssl\

cd /d "%~dp0"

echo.
echo [INFO] Searching for cert.pem on this machine...
echo.

set "FOUND_CERT="
set "FOUND_KEY="

REM Search common locations
for /f "delims=" %%F in ('dir /b /s "C:\Users\Administrator\cert.pem" 2^>nul') do set "FOUND_CERT=%%F"
for /f "delims=" %%F in ('dir /b /s "C:\Users\Administrator\key.pem"  2^>nul') do set "FOUND_KEY=%%F"

if not defined FOUND_CERT (
    for /f "delims=" %%F in ('dir /b /s "C:\cert.pem" 2^>nul') do set "FOUND_CERT=%%F"
    for /f "delims=" %%F in ('dir /b /s "C:\key.pem"  2^>nul') do set "FOUND_KEY=%%F"
)

if defined FOUND_CERT (
    echo [FOUND] %FOUND_CERT%
    echo [FOUND] %FOUND_KEY%
    if not exist "ssl" mkdir ssl
    copy "%FOUND_CERT%" "ssl\cert.pem" >nul
    copy "%FOUND_KEY%"  "ssl\key.pem"  >nul
    echo [OK] Copied to ssl\
    goto :gen_done
)

echo [INFO] Not found — generating now...

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

if not exist "ssl" mkdir ssl

"%PYTHON%" -m pip install cryptography --quiet

"%PYTHON%" -c "
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend
import datetime, pathlib, sys

ssl_dir = pathlib.Path('ssl')
ssl_dir.mkdir(exist_ok=True)

key = rsa.generate_private_key(65537, 2048, default_backend())
subject = issuer = x509.Name([
    x509.NameAttribute(NameOID.COUNTRY_NAME, 'JO'),
    x509.NameAttribute(NameOID.ORGANIZATION_NAME, 'AJi Group'),
    x509.NameAttribute(NameOID.COMMON_NAME, 'radar.ajigroup.com'),
])
cert = (x509.CertificateBuilder()
    .subject_name(subject).issuer_name(issuer)
    .public_key(key.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(datetime.datetime.utcnow())
    .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=3650))
    .add_extension(x509.SubjectAlternativeName([
        x509.DNSName('radar.ajigroup.com'),
        x509.DNSName('localhost'),
    ]), critical=False)
    .sign(key, hashes.SHA256(), default_backend()))

(ssl_dir / 'cert.pem').write_bytes(cert.public_bytes(serialization.Encoding.PEM))
(ssl_dir / 'key.pem').write_bytes(key.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.TraditionalOpenSSL,
    serialization.NoEncryption()))
print('[OK] Certificate generated: ssl/cert.pem + ssl/key.pem')
print('[OK] Valid for 10 years')
"
if errorlevel 1 ( echo [ERROR] Generation failed & pause & exit /b 1 )

:gen_done
echo.
echo [INFO] Verifying:
if exist "ssl\cert.pem" ( echo [OK] ssl\cert.pem exists ) else ( echo [ERROR] ssl\cert.pem still missing )
if exist "ssl\key.pem"  ( echo [OK] ssl\key.pem exists  ) else ( echo [ERROR] ssl\key.pem still missing  )

echo.
echo [INFO] Now run test_https.bat to start the server.
echo.
pause
