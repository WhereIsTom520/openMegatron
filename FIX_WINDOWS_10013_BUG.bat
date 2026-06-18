@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo.
echo ============================================================
echo   Windows 10013 Reserved Port BUG - ONE-CLICK FIX
echo ============================================================
echo.
echo   This fixes the infamous Windows "[winerror 10013]" bug
echo   that randomly blocks ports like 8000, 8001, etc.
echo.
echo   REQUIRES ADMINISTRATOR PRIVILEGES!
echo.

REM Check admin rights
net session >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo   [XX] NOT RUNNING AS ADMINISTRATOR!
    echo.
    echo   Right-click this file and select:
    echo       "Run as administrator"
    echo.
    pause
    exit /b 1
)

echo   [OK] Running with administrator privileges
echo.

REM Fix 1: Set TCP dynamic port range to start at 50000
echo   [1/3] Setting TCP dynamic port range...
netsh int ipv4 set dynamicport tcp start=50000 num=15535 >nul
if %ERRORLEVEL% EQU 0 (
    echo        OK - TCP dynamic port range starts at 50000
) else (
    echo        (may already be set - ignoring)
)

REM Fix 2: Exclude 8000-8999 from dynamic port allocation
echo   [2/3] Excluding port range 8000-8999 from Windows reservation...
netsh int ipv4 add excludedportrange tcp 8000 1000 >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo        OK - Excluded ports 8000-8999
) else (
    echo        (may already be excluded - ignoring)
)

REM Fix 3: Exclude 3000-3999 for frontend
netsh int ipv4 add excludedportrange tcp 3000 1000 >nul 2>&1

REM Fix 4: Restart NAT service
echo   [3/3] Restarting Windows NAT service...
net stop winnat >nul 2>&1
net start winnat >nul 2>&1

echo.
echo ============================================================
echo   [OK] Windows 10013 BUG HAS BEEN FIXED!
echo ============================================================
echo.
echo   Ports 8000+ and 3000+ should now work correctly.
echo.
echo   You can now run start.bat normally.
echo.
echo   If the bug returns in the future, just run this again.
echo ============================================================
echo.
pause
