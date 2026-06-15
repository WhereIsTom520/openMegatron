@echo off
chcp 65001 >nul 2>&1
setlocal EnableExtensions

cd /d "%~dp0"

if /i "%~1"=="RUN_BACKEND" (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" backend %2 %3 %4 %5 %6 %7 %8 %9
  exit /b %errorlevel%
)

if /i "%~1"=="RUN_FRONTEND" (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" frontend %2 %3 %4 %5 %6 %7 %8 %9
  exit /b %errorlevel%
)

if /i "%~1"=="--help" goto HELP
if /i "%~1"=="-h" goto HELP
if /i "%~1"=="help" goto HELP

set "ACTION=%~1"
if "%ACTION%"=="" set "ACTION=start"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %ACTION% %2 %3 %4 %5 %6 %7 %8 %9
set "RC=%errorlevel%"

if not "%RC%"=="0" (
  echo.
  echo OpenMegatron did not start cleanly.
  echo Logs are in: %~dp0.runtime
  echo.
  pause
)
exit /b %RC%

:HELP
echo OpenMegatron one-click launcher
echo.
echo   start.bat             Start backend + frontend, then open browser
echo   start.bat health      Check current service status
echo   start.bat stop        Stop started backend/frontend processes
echo   start.bat install     Create venv and install Python/Node packages
echo   start.bat test        Run project tests
echo   start.bat menu        Show menu
echo.
exit /b 0
