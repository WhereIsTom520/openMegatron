@echo off
chcp 65001 >nul 2>&1
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"

set "TQDM_DISABLE=1"
set "TRANSFORMERS_VERBOSITY=error"
set "TOKENIZERS_PARALLELISM=false"
set "PYTHONUTF8=1"

if /i "%~1"=="--help" goto HELP
if /i "%~1"=="-h" goto HELP
if /i "%~1"=="help" goto HELP
if /i "%~1"=="RUN_BACKEND" goto RUN_BACKEND
if /i "%~1"=="RUN_FRONTEND" goto RUN_FRONTEND
if /i "%~1"=="test" goto TEST_ALL
if not "%~1"=="" goto UNKNOWN_ARG

goto START_ALL

:HELP
echo Megatron launcher
echo.
echo   start.bat        Start backend, frontend, and open the app
echo   start.bat test   Run startup self-test
echo.
exit /b 0

:UNKNOWN_ARG
echo [ERROR] Unknown option: %~1
exit /b 1

:START_ALL
title Megatron Launcher
color 0A
if not exist ".runtime" mkdir ".runtime" >nul 2>&1
set "STARTUP_LOG=.runtime\startup.log"
> "%STARTUP_LOG%" echo Megatron startup log
echo ===================================================
echo Starting Megatron
echo ===================================================
echo.
call :CHECK_PYTHON
if errorlevel 1 goto FAIL
call :CHECK_VENV
if errorlevel 1 goto FAIL
call :CHECK_PY_DEPS
if errorlevel 1 goto FAIL
call :CHECK_NODE
if errorlevel 1 goto FAIL
call :CHECK_WEB_DEPS
if errorlevel 1 goto FAIL
call :CHECK_CONFIG
if errorlevel 1 goto FAIL
call :SETUP_API_RUNTIME
if errorlevel 1 goto FAIL
call :SETUP_FRONTEND_RUNTIME
if errorlevel 1 goto FAIL

echo Opening backend window...
start "Megatron Backend API" cmd /k call "%~f0" RUN_BACKEND

set "BACKEND_PORT=8000"
if exist ".runtime\backend_port.txt" set /p BACKEND_PORT=<".runtime\backend_port.txt"
echo Waiting for backend API on http://localhost:%BACKEND_PORT% ...
call :WAIT_FOR_URL "http://localhost:%BACKEND_PORT%/runtime_status" 90
if errorlevel 1 goto BACKEND_WARN

echo Opening frontend window...
start "Megatron Frontend" cmd /k call "%~f0" RUN_FRONTEND

set "FRONTEND_PORT=3000"
if exist ".runtime\frontend_port.txt" set /p FRONTEND_PORT=<".runtime\frontend_port.txt"
echo Waiting for frontend on http://localhost:%FRONTEND_PORT% ...
call :WAIT_FOR_URL "http://localhost:%FRONTEND_PORT%" 90
if errorlevel 1 goto FRONTEND_WARN

echo.
echo [OK] Megatron is ready.
echo App: http://localhost:%FRONTEND_PORT%
start "" "http://localhost:%FRONTEND_PORT%"
exit /b 0

:BACKEND_WARN
echo [WARN] Backend did not report ready in time.
echo        Keep the backend window open; it may still be loading models.
echo        Log: %STARTUP_LOG%
goto FAIL

:FRONTEND_WARN
echo [WARN] Frontend did not report ready in time.
echo        Keep the frontend window open; it may still be compiling.
echo        Try opening http://localhost:%FRONTEND_PORT% manually in a minute.
goto FAIL

:TEST_ALL
title Megatron Startup Test
color 0B
set "MEGATRON_SKIP_LLM_CHECK=1"
if not defined OPENAI_API_KEY set "OPENAI_API_KEY=megatron-self-test-key"
call :CHECK_PYTHON
if errorlevel 1 goto FAIL
call :CHECK_VENV
if errorlevel 1 goto FAIL
call :CHECK_PY_DEPS
if errorlevel 1 goto FAIL
call :CHECK_NODE
if errorlevel 1 goto FAIL
call :CHECK_WEB_DEPS
if errorlevel 1 goto FAIL
call :CHECK_CONFIG
if errorlevel 1 goto FAIL
call :SETUP_TEST_RUNTIME
if errorlevel 1 goto FAIL
if exist ".runtime\runtime_env.cmd" call ".runtime\runtime_env.cmd"
echo Running backend self-test...
venv\Scripts\python.exe "pysrc\agent.py" --self-test
if errorlevel 1 goto FAIL
echo Running frontend typecheck...
call npm run lint
if errorlevel 1 goto FAIL
echo Running frontend build...
call npm run build
if errorlevel 1 goto FAIL
echo.
echo [OK] Startup self-test passed.
exit /b 0

:RUN_BACKEND
title Megatron Backend API
color 0B
if exist ".runtime\runtime_env.cmd" call ".runtime\runtime_env.cmd"
if not defined MEGATRON_BACKEND_PORT set "MEGATRON_BACKEND_PORT=8000"
set "AGENT_NO_CONSOLE_CONFIRM=1"
echo Backend API port: %MEGATRON_BACKEND_PORT%
venv\Scripts\python.exe "pysrc\agent.py" --api --port %MEGATRON_BACKEND_PORT%
pause
exit /b %errorlevel%

:RUN_FRONTEND
title Megatron Frontend
color 0E
set "BACKEND_PORT=8000"
if exist ".runtime\backend_port.txt" set /p BACKEND_PORT=<".runtime\backend_port.txt"
set "FRONTEND_PORT=3000"
if exist ".runtime\frontend_port.txt" set /p FRONTEND_PORT=<".runtime\frontend_port.txt"
set "VITE_API_BASE=http://localhost:%BACKEND_PORT%"
set "FRONTEND_PORT=%FRONTEND_PORT%"
set "VITE_FRONTEND_PORT=%FRONTEND_PORT%"
echo Frontend API base: %VITE_API_BASE%
echo Frontend port: %FRONTEND_PORT%
call npm run dev -- --host 0.0.0.0 --port %FRONTEND_PORT%
pause
exit /b %errorlevel%

:CHECK_PYTHON
echo [1/7] Checking Python...
where python >nul 2>&1
if errorlevel 1 echo [ERROR] Python not found.
if errorlevel 1 exit /b 1
python --version
exit /b 0

:CHECK_VENV
echo [2/7] Checking Python virtual environment...
if exist "venv\Scripts\python.exe" goto VENV_OK
echo Creating venv...
python -m venv venv
if errorlevel 1 exit /b 1
:VENV_OK
venv\Scripts\python.exe -c "import sys" >nul 2>&1
if errorlevel 1 echo [ERROR] venv is broken.
if errorlevel 1 exit /b 1
exit /b 0

:CHECK_PY_DEPS
echo [3/7] Checking Python dependencies...
if not exist "pysrc\requirements.txt" echo [ERROR] Missing pysrc\requirements.txt.
if not exist "pysrc\requirements.txt" exit /b 1
for /f %%h in ('powershell -NoProfile -Command "(Get-FileHash -Algorithm SHA256 'pysrc\requirements.txt').Hash"') do set "REQ_HASH=%%h"
set "OLD_REQ_HASH="
if exist "venv\.requirements.sha256" set /p OLD_REQ_HASH=<"venv\.requirements.sha256"
if "!OLD_REQ_HASH!"=="!REQ_HASH!" goto PY_DEPS_EXTRA
echo Installing Python packages. First run can take a while...
venv\Scripts\python.exe -m pip install wheel
if errorlevel 1 exit /b 1
venv\Scripts\python.exe -m pip install -r "pysrc\requirements.txt"
if errorlevel 1 exit /b 1
> "venv\.requirements.sha256" echo !REQ_HASH!
:PY_DEPS_EXTRA
venv\Scripts\python.exe -c "import tomli_w" >nul 2>&1
if not errorlevel 1 goto PY_PLAYWRIGHT
venv\Scripts\python.exe -m pip install tomli-w
if errorlevel 1 exit /b 1
:PY_PLAYWRIGHT
venv\Scripts\python.exe -c "import playwright" >nul 2>&1
if not errorlevel 1 exit /b 0
venv\Scripts\python.exe -m pip install playwright
if errorlevel 1 exit /b 1
exit /b 0

:CHECK_NODE
echo [4/7] Checking Node.js...
where node >nul 2>&1
if errorlevel 1 echo [ERROR] Node.js not found.
if errorlevel 1 exit /b 1
where npm >nul 2>&1
if errorlevel 1 echo [ERROR] npm not found.
if errorlevel 1 exit /b 1
node --version
exit /b 0

:CHECK_WEB_DEPS
echo [5/7] Checking frontend dependencies...
if exist "node_modules" exit /b 0
if exist "package-lock.json" goto NPM_CI
call npm install --no-audit --no-fund
exit /b %errorlevel%
:NPM_CI
call npm ci --no-audit --no-fund
exit /b %errorlevel%

:CHECK_CONFIG
echo [6/7] Checking config...
if exist "pysrc\model.toml" exit /b 0
if not exist "pysrc\model.example.toml" echo [ERROR] Missing pysrc\model.toml.
if not exist "pysrc\model.example.toml" exit /b 1
copy /y "pysrc\model.example.toml" "pysrc\model.toml" >nul
exit /b 0

:SETUP_API_RUNTIME
echo [7/7] Starting local databases...
if not exist ".runtime" mkdir ".runtime" >nul 2>&1
del ".runtime\runtime_env.cmd" >nul 2>&1
del ".runtime\backend_port.txt" >nul 2>&1
venv\Scripts\python.exe "scripts\runtime_setup.py" --toml "pysrc\model.toml" --runtime-dir ".runtime" --mode API
exit /b %errorlevel%

:SETUP_FRONTEND_RUNTIME
echo Choosing frontend port...
if not exist ".runtime" mkdir ".runtime" >nul 2>&1
for /f %%p in ('powershell -NoProfile -ExecutionPolicy Bypass -Command "$p=3000; while($p -lt 65535){ $l=[Net.Sockets.TcpListener]::new([Net.IPAddress]::Any,$p); try{$l.Start(); $l.Stop(); Write-Output $p; break}catch{$p++} }"') do set "FRONTEND_PORT=%%p"
if not defined FRONTEND_PORT echo [ERROR] No available frontend port found.
if not defined FRONTEND_PORT exit /b 1
> ".runtime\frontend_port.txt" echo %FRONTEND_PORT%
if "%FRONTEND_PORT%"=="3000" (
  echo [OK] Frontend port 3000 is bindable.
) else (
  echo [WARN] Frontend port 3000 is unavailable; using %FRONTEND_PORT%.
)
exit /b 0

:SETUP_TEST_RUNTIME
echo [7/7] Starting local databases...
if not exist ".runtime" mkdir ".runtime" >nul 2>&1
del ".runtime\runtime_env.cmd" >nul 2>&1
venv\Scripts\python.exe "scripts\runtime_setup.py" --toml "pysrc\model.toml" --runtime-dir ".runtime" --mode TEST
exit /b %errorlevel%

:WAIT_FOR_FILE
set "WAIT_FILE=%~1"
set /a WAIT_MAX=%~2
set /a WAIT_COUNT=0
:WAIT_LOOP
if exist "%WAIT_FILE%" exit /b 0
if !WAIT_COUNT! geq !WAIT_MAX! exit /b 1
set /a WAIT_COUNT+=1
ping 127.0.0.1 -n 2 >nul
goto WAIT_LOOP

:WAIT_FOR_URL
set "WAIT_URL=%~1"
set /a WAIT_MAX=%~2
set /a WAIT_COUNT=0
:WAIT_URL_LOOP
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $r=Invoke-WebRequest -UseBasicParsing -Uri '%WAIT_URL%' -TimeoutSec 2; if ($r.StatusCode -ge 200 -and $r.StatusCode -lt 500) { exit 0 } } catch { exit 1 }"
if not errorlevel 1 exit /b 0
if !WAIT_COUNT! geq !WAIT_MAX! exit /b 1
set /a WAIT_COUNT+=1
ping 127.0.0.1 -n 2 >nul
goto WAIT_URL_LOOP

:FAIL
echo.
echo [ERROR] Startup failed.
echo Please keep this window open and send the lines above.
if exist ".runtime\startup.log" echo Startup log: .runtime\startup.log
pause
exit /b 1
