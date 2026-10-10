@echo off
title Fix http.sys Port Reservation
color 0E
REM Path: TenderRadar/fix_httpsys.bat
REM The "HTTP Error 404" page is served by Windows http.sys kernel driver
REM which reserves ports at the OS level, even without IIS installed.
REM This script removes the http.sys reservation for port 443.

net session >nul 2>&1
if errorlevel 1 ( echo Run as Administrator & pause & exit /b 1 )

echo.
echo ============================================================
echo   Fixing Windows http.sys port 443 reservation
echo ============================================================
echo.

echo [INFO] Current http.sys URL reservations on port 443:
netsh http show urlacl | findstr ":443"
echo.

echo [INFO] Current http.sys service listening on 443:
netstat -ano | findstr ":443"
echo.

echo [INFO] Removing all http.sys reservations for port 443...
for /f "tokens=*" %%U in ('netsh http show urlacl ^| findstr ":443"') do (
    echo   Removing: %%U
    netsh http delete urlacl url=%%U >nul 2>&1
)

echo [INFO] Checking SSL certificate bindings on port 443...
netsh http show sslcert | findstr ":443\|CertHash\|AppID"
echo.

echo [INFO] Removing SSL cert bindings on port 443 (if any)...
netsh http delete sslcert ipport=0.0.0.0:443 >nul 2>&1
netsh http delete sslcert ipport=[::]":443"  >nul 2>&1
echo [OK] Done

echo.
echo [INFO] Stopping http.sys related services...
net stop http /y >nul 2>&1
echo [INFO] http.sys stopped (will restart automatically if needed)

echo.
echo [INFO] Checking what process owns port 443 now...
netstat -ano | findstr ":443 " | findstr "LISTENING"
if errorlevel 1 ( echo [OK] Port 443 is now FREE ) else ( echo [WARN] Still occupied — see above )

echo.
echo ============================================================
echo   Now run: test_https.bat
echo ============================================================
echo.
pause
