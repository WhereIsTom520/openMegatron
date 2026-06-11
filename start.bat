@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion

cd /d "%~dp0"

if /i "%~1"=="--help" goto TOP_HELP
if /i "%~1"=="-h" goto TOP_HELP
if "%~1"=="/?" goto TOP_HELP
goto TOP_HELP_DONE

:TOP_HELP
echo Megatron launcher usage:
echo.
echo   start.bat              Open the interactive menu
echo   start.bat --help       Show this help
echo.
echo Menu options:
echo   1  Start all services: backend API + frontend
echo   2  Start Python backend only: CLI mode
echo   3  Open cleanup manager
echo   4  Install base environment: Python, Docker, Node.js
echo   0  Exit
exit /b 0

:TOP_HELP_DONE

set "TQDM_DISABLE=1"
set "TRANSFORMERS_VERBOSITY=error"
set "TOKENIZERS_PARALLELISM=false"

call :INIT_LANGUAGE
set "TXT_WINDOW_BACKEND_API=Megatron Backend (API)"
set "TXT_WINDOW_FRONTEND=Megatron Frontend"
set "TXT_WINDOW_BACKEND_CLI=Megatron Backend (CLI)"
set "TXT_WINDOW_CLEANUP=Megatron Cleanup"

if "%~1"=="RUN_BACKEND" goto RUN_BACKEND
if "%~1"=="RUN_FRONTEND" goto RUN_FRONTEND
if "%~1"=="RUN_CLEANUP" goto RUN_CLEANUP
if "%~1"=="RUN_INSTALL_ENV" goto RUN_INSTALL_ENV
if "%~1"=="" goto ARGUMENTS_OK
echo [ERROR] Unknown option: %~1
exit /b 1

:ARGUMENTS_OK

set "MEGATRON_DOCKER_WAIT_MAX=%MEGATRON_DOCKER_WAIT_MAX%"
if not defined MEGATRON_DOCKER_WAIT_MAX set "MEGATRON_DOCKER_WAIT_MAX=80"
set "MEGATRON_DB_WAIT_MAX=%MEGATRON_DB_WAIT_MAX%"
if not defined MEGATRON_DB_WAIT_MAX set "MEGATRON_DB_WAIT_MAX=90"
set "MEGATRON_NEO4J_WAIT_MAX=%MEGATRON_NEO4J_WAIT_MAX%"
if not defined MEGATRON_NEO4J_WAIT_MAX set "MEGATRON_NEO4J_WAIT_MAX=90"

title Megatron Framework Launcher
color 0A

:MENU
cls
set "choice="
if defined MEGATRON_TEST_CHOICE (
    set "choice=%MEGATRON_TEST_CHOICE%"
) else (
    call :SHOW_I18N show-main
    set /p choice=
)

if "%choice%"=="" goto MENU
if "%choice%"=="1" (
    if not exist ".runtime" mkdir ".runtime" >nul 2>&1
    del ".runtime\backend_port.txt" >nul 2>&1
    echo.
    echo ============================================
    echo   openMegatron - Starting all services
    echo ============================================
    echo.
    echo [1/3] Starting backend API...
    start "!TXT_WINDOW_BACKEND_API!" cmd /k call "%~f0" RUN_BACKEND API
    echo         Waiting for backend to become ready...
    call :WAIT_FOR_FILE ".runtime\backend_port.txt" 90
    if errorlevel 1 (
        call :PRINT_I18N warn-backend-port
        echo [WARN] Backend port file not found, continuing anyway...
    )
    set /p BACKEND_PORT=<".runtime\backend_port.txt" 2>nul
    if not defined BACKEND_PORT set "BACKEND_PORT=8000"
    echo         Backend port: !BACKEND_PORT!
    echo         Health-checking API...
    call :HEALTH_CHECK_URL "http://127.0.0.1:!BACKEND_PORT!/runtime_status" 15 HC_RESULT
    if errorlevel 1 (
        echo [WARN] Backend API not responding yet on port !BACKEND_PORT!
        echo        It may still be initializing. Check the backend window for details.
    ) else (
        echo [OK]  Backend API is healthy ^(port !BACKEND_PORT!^)
    )
    echo.
    echo [2/3] Starting frontend...
    start "!TXT_WINDOW_FRONTEND!" cmd /k call "%~f0" RUN_FRONTEND
    echo         Waiting for frontend to become ready...
    set /p FRONTEND_PORT=<".runtime\frontend_port.txt" 2>nul
    if not defined FRONTEND_PORT set "FRONTEND_PORT=3000"
    call :HEALTH_CHECK_URL "http://127.0.0.1:!FRONTEND_PORT!" 30 HC_RESULT
    if errorlevel 1 (
        echo [WARN] Frontend not responding yet on port !FRONTEND_PORT!
        echo        It may still be installing dependencies. Check the frontend window.
    ) else (
        echo [OK]  Frontend is running ^(port !FRONTEND_PORT!^)
    )
    echo.
    echo [3/3] Final health check...
    call :HEALTH_CHECK_URL "http://127.0.0.1:!BACKEND_PORT!/runtime_status" 5 HC_RESULT
    if errorlevel 1 (
        echo [WARN] Final API health check failed.
    ) else (
        echo [OK]  All services responding.
    )
    echo.
    echo ============================================
    echo   openMegatron is running!
    echo.
    echo   Frontend:  http://localhost:!FRONTEND_PORT!
    echo   Backend:   http://localhost:!BACKEND_PORT!
    echo   API Docs:  http://localhost:!BACKEND_PORT!/docs
    echo ============================================
    echo.
    echo Close the backend and frontend windows to stop.
    echo.
    echo Press any key to return to the launcher menu.
    pause >nul
    goto MENU
)
if "%choice%"=="2" (
    start "!TXT_WINDOW_BACKEND_CLI!" cmd /k call "%~f0" RUN_BACKEND CLI
    goto MENU
)
if "%choice%"=="3" (
    start "!TXT_WINDOW_CLEANUP!" cmd /c call "%~f0" RUN_CLEANUP
    goto MENU
)
if "%choice%"=="4" (
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList 'RUN_INSTALL_ENV' -Verb RunAs"
    goto MENU
)
if "%choice%"=="0" exit /b 0
goto MENU

goto :EOF

:SHOW_HELP
if not defined UNKNOWN_ARG goto QUICK_HELP
echo [ERROR] Unknown option: %UNKNOWN_ARG%
echo.
goto QUICK_HELP

:QUICK_HELP
echo Megatron launcher usage:
echo.
echo   start.bat              Open the interactive menu
echo   start.bat --help       Show this help
echo.
echo Menu options:
echo   1  Start all services: backend API + frontend
echo   2  Start Python backend only: CLI mode
echo   3  Open cleanup manager
echo   4  Install base environment: Python, Docker, Node.js
echo   0  Exit
if defined UNKNOWN_ARG exit /b 1
exit /b 0

:DISABLED_ZH_TEXT
echo Megatron 启动器用法:
echo.
echo   start.bat              打开交互菜单
echo   start.bat --help       显示帮助
echo.
echo 菜单选项:
echo   1  启动全部（后端 API + 前端）
echo   2  仅启动 Python 后端（CLI 模式）
echo   3  清理管理器
echo   4  安装基础环境（Python、Docker、Node.js）
echo   0  退出
goto HELP_DONE

:HELP_EN
echo Megatron launcher usage:
echo.
echo   start.bat              Open the interactive menu
echo   start.bat --help       Show this help
echo.
echo Menu options:
echo   1  Start all services (backend API + frontend)
echo   2  Start Python backend only (CLI mode)
echo   3  Open cleanup manager
echo   4  Install base environment (Python, Docker, Node.js)
echo   0  Exit

:HELP_DONE
if defined UNKNOWN_ARG exit /b 1
exit /b 0

:INIT_LANGUAGE
if defined MEGATRON_LANG goto INIT_LANGUAGE_NORMALIZE
if exist "megatron.lang" (
    for /f "usebackq delims=" %%l in ("megatron.lang") do if not defined MEGATRON_LANG set "MEGATRON_LANG=%%l"
)
if defined MEGATRON_LANG goto INIT_LANGUAGE_NORMALIZE
for /f %%l in ('powershell -NoProfile -Command "$lang=(Get-Culture).TwoLetterISOLanguageName; if ($lang -eq 'zh') { 'zh' } else { 'en' }" 2^>nul') do set "MEGATRON_LANG=%%l"

:INIT_LANGUAGE_NORMALIZE
if /i "%MEGATRON_LANG%"=="zh" exit /b 0
set "MEGATRON_LANG=en"
exit /b 0

:PRINT_I18N
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launcher_i18n.ps1" "%~1" "%MEGATRON_LANG%"
exit /b 0

:SHOW_I18N
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launcher_i18n.ps1" "%~1" "%MEGATRON_LANG%"
exit /b 0

:CHECK_PORT
set "PORT=%~1"
set "PORT_NAME=%~2"
set "PS_SCRIPT=%TEMP%\check_port_%RANDOM%.ps1"
> "%PS_SCRIPT%" echo $port = %PORT%
>> "%PS_SCRIPT%" echo $portName = '%PORT_NAME%'
>> "%PS_SCRIPT%" echo $inUse = netstat -ano ^| Select-String ":$port " ^| Select-String "LISTENING"
>> "%PS_SCRIPT%" echo if ($inUse) {
>> "%PS_SCRIPT%" echo     Write-Host "[WARN] Port $port ($portName) is already in use!"
>> "%PS_SCRIPT%" echo     $inUse
>> "%PS_SCRIPT%" echo     $kill = Read-Host "Do you want to kill the occupying process? (y/n)"
>> "%PS_SCRIPT%" echo     if ($kill -eq 'y') {
>> "%PS_SCRIPT%" echo         foreach ($line in $inUse) {
>> "%PS_SCRIPT%" echo             $line -match '\d+$' ^| Out-Null
>> "%PS_SCRIPT%" echo             $processId = $matches[0]
>> "%PS_SCRIPT%" echo             Stop-Process -Force -Id $processId
>> "%PS_SCRIPT%" echo             Write-Host "Process $processId killed."
>> "%PS_SCRIPT%" echo         }
>> "%PS_SCRIPT%" echo         Start-Sleep -Seconds 2
>> "%PS_SCRIPT%" echo         $still = netstat -ano ^| Select-String ":$port " ^| Select-String "LISTENING"
>> "%PS_SCRIPT%" echo         if ($still) {
>> "%PS_SCRIPT%" echo             Write-Host "[ERROR] Port $port still occupied."
>> "%PS_SCRIPT%" echo             exit 1
>> "%PS_SCRIPT%" echo         } else {
>> "%PS_SCRIPT%" echo             Write-Host "[OK] Port $port is now free."
>> "%PS_SCRIPT%" echo             exit 0
>> "%PS_SCRIPT%" echo         }
>> "%PS_SCRIPT%" echo     } else {
>> "%PS_SCRIPT%" echo         exit 1
>> "%PS_SCRIPT%" echo     }
>> "%PS_SCRIPT%" echo } else {
>> "%PS_SCRIPT%" echo     Write-Host "[OK] Port $port is free."
>> "%PS_SCRIPT%" echo     exit 0
>> "%PS_SCRIPT%" echo }
powershell -NoProfile -ExecutionPolicy Bypass -File "%PS_SCRIPT%"
set "CHECK_RESULT=!errorlevel!"
del "%PS_SCRIPT%" 2>nul
exit /b !CHECK_RESULT!

:FIND_FREE_PORT
set "START_PORT=%~1"
set "OUT_VAR=%~2"
set "PORT_NAME=%~3"
set "%OUT_VAR%="
for /f %%p in ('powershell -NoProfile -Command "$p=[int]%START_PORT%; while($p -lt 65535){ try { $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Any, $p); $listener.Start(); $listener.Stop(); Write-Output $p; break } catch { $p++ } }"') do set "%OUT_VAR%=%%p"
if not defined %OUT_VAR% (
    echo [ERROR] Could not find a free port for %PORT_NAME% starting at %START_PORT%.
    exit /b 1
)
if not "!%OUT_VAR%!"=="%START_PORT%" (
    echo [WARN] %PORT_NAME% port %START_PORT% is busy; using !%OUT_VAR%! instead.
) else (
    echo [OK] %PORT_NAME% port !%OUT_VAR%! is free.
)
exit /b 0

:STOP_STALE_BACKENDS
set "PROJECT_ROOT=%CD%"
set "PS_SCRIPT=%TEMP%\stop_megatron_backends_%RANDOM%.ps1"
> "%PS_SCRIPT%" echo $root = [IO.Path]::GetFullPath('%PROJECT_ROOT%')
>> "%PS_SCRIPT%" echo $ids = [System.Collections.Generic.HashSet[int]]::new()
>> "%PS_SCRIPT%" echo $pidFile = Join-Path $root '.runtime\backend_pid.txt'
>> "%PS_SCRIPT%" echo if (Test-Path $pidFile) {
>> "%PS_SCRIPT%" echo   $pidText = Get-Content $pidFile -ErrorAction SilentlyContinue ^| Select-Object -First 1
>> "%PS_SCRIPT%" echo   $pidValue = 0
>> "%PS_SCRIPT%" echo   if ([int]::TryParse($pidText, [ref]$pidValue)) {
>> "%PS_SCRIPT%" echo     $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$pidValue" -ErrorAction SilentlyContinue
>> "%PS_SCRIPT%" echo     if ($proc -and $proc.CommandLine -and $proc.CommandLine -match 'agent\.py' -and $proc.CommandLine -match '--api') {
>> "%PS_SCRIPT%" echo       [void]$ids.Add([int]$proc.ProcessId)
>> "%PS_SCRIPT%" echo       $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$($proc.ParentProcessId)" -ErrorAction SilentlyContinue
>> "%PS_SCRIPT%" echo       if ($parent -and $parent.CommandLine -and $parent.CommandLine -match 'agent\.py' -and $parent.CommandLine -match '--api') { [void]$ids.Add([int]$parent.ProcessId) }
>> "%PS_SCRIPT%" echo     }
>> "%PS_SCRIPT%" echo   }
>> "%PS_SCRIPT%" echo }
>> "%PS_SCRIPT%" echo $rootEsc = [regex]::Escape($root)
>> "%PS_SCRIPT%" echo $procs = Get-CimInstance Win32_Process ^| Where-Object { $_.CommandLine -and $_.CommandLine -match 'agent\.py' -and $_.CommandLine -match '--api' -and $_.CommandLine -match $rootEsc }
>> "%PS_SCRIPT%" echo foreach ($proc in $procs) { [void]$ids.Add([int]$proc.ProcessId) }
>> "%PS_SCRIPT%" echo foreach ($id in $ids) {
>> "%PS_SCRIPT%" echo   try {
>> "%PS_SCRIPT%" echo     Stop-Process -Id $id -Force -ErrorAction Stop
>> "%PS_SCRIPT%" echo     Write-Host "[INFO] Stopped stale Megatron backend PID $id."
>> "%PS_SCRIPT%" echo   } catch {
>> "%PS_SCRIPT%" echo     Write-Host "[WARN] Could not stop stale backend PID $id`: $($_.Exception.Message)"
>> "%PS_SCRIPT%" echo   }
>> "%PS_SCRIPT%" echo }
>> "%PS_SCRIPT%" echo if ($ids.Count -gt 0) { Start-Sleep -Seconds 1 }
powershell -NoProfile -ExecutionPolicy Bypass -File "%PS_SCRIPT%"
del "%PS_SCRIPT%" 2>nul
exit /b 0

:STOP_STALE_FRONTENDS
set "PROJECT_ROOT=%CD%"
set "PS_SCRIPT=%TEMP%\stop_megatron_frontends_%RANDOM%.ps1"
> "%PS_SCRIPT%" echo $root = [IO.Path]::GetFullPath('%PROJECT_ROOT%')
>> "%PS_SCRIPT%" echo $rootEsc = [regex]::Escape($root)
>> "%PS_SCRIPT%" echo $procs = @(Get-CimInstance Win32_Process ^| Where-Object { $_.CommandLine -and $_.CommandLine -match 'vite' -and $_.CommandLine -match $rootEsc })
>> "%PS_SCRIPT%" echo foreach ($proc in $procs) {
>> "%PS_SCRIPT%" echo   try {
>> "%PS_SCRIPT%" echo     Stop-Process -Id $proc.ProcessId -Force -ErrorAction Stop
>> "%PS_SCRIPT%" echo     Write-Host "[INFO] Stopped stale Megatron frontend PID $($proc.ProcessId)."
>> "%PS_SCRIPT%" echo   } catch {
>> "%PS_SCRIPT%" echo     Write-Host "[WARN] Could not stop stale frontend PID $($proc.ProcessId): $($_.Exception.Message)"
>> "%PS_SCRIPT%" echo   }
>> "%PS_SCRIPT%" echo }
>> "%PS_SCRIPT%" echo if ($procs.Count -gt 0) { Start-Sleep -Seconds 1 }
powershell -NoProfile -ExecutionPolicy Bypass -File "%PS_SCRIPT%"
del "%PS_SCRIPT%" 2>nul
exit /b 0

:WAIT_FOR_FILE
set "WAIT_FILE=%~1"
set /a "WAIT_MAX=%~2"
if "!WAIT_MAX!"=="0" set "WAIT_MAX=30"
set /a "WAIT_COUNT=0"
:WAIT_FOR_FILE_LOOP
if exist "!WAIT_FILE!" exit /b 0
if !WAIT_COUNT! geq !WAIT_MAX! exit /b 1
call :SLEEP_SECONDS 1
set /a "WAIT_COUNT+=1"
goto WAIT_FOR_FILE_LOOP

:HEALTH_CHECK_URL
REM Usage: call :HEALTH_CHECK_URL "url" max_retries result_var
REM Returns 0 (success) or 1 (failure), stores HTTP code in result_var
set "HC_URL=%~1"
set /a "HC_MAX=%~2"
if "!HC_MAX!"=="0" set /a "HC_MAX=30"
set /a "HC_COUNT=0"
:HC_LOOP
curl.exe -s -o nul -w "%%{http_code}" "!HC_URL!" > ".runtime\hc_tmp.txt" 2>nul
set /p HC_CODE=<".runtime\hc_tmp.txt" 2>nul
del ".runtime\hc_tmp.txt" >nul 2>&1
if defined HC_CODE (
    if "!HC_CODE!"=="200" (
        if not "%~3"=="" set "%~3=!HC_CODE!"
        exit /b 0
    )
    if "!HC_CODE!"=="302" (
        if not "%~3"=="" set "%~3=!HC_CODE!"
        exit /b 0
    )
)
if !HC_COUNT! geq !HC_MAX! (
    if not "%~3"=="" set "%~3=!HC_CODE!"
    exit /b 1
)
set /a "HC_COUNT+=1"
call :SLEEP_SECONDS 1
goto HC_LOOP

:RUN_INSTALL_ENV
title Environment Installer
color 0B
cls
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [ERROR] Environment installation requires administrator privileges.
    echo Please choose option 4 from the main launcher so it can request elevation.
    pause
    exit /b 1
)
set PY_VER=3.11.9
set PY_EXE=python-%PY_VER%-amd64.exe
set WORKDIR=%TEMP%\agent_runtime_install
set PY_INSTALLER=%WORKDIR%\%PY_EXE%
set DOCKER_INSTALLER=%WORKDIR%\DockerDesktopInstaller.exe
set PY_URLS=https://registry.npmmirror.com/-/binary/python/%PY_VER%/%PY_EXE% https://repo.huaweicloud.com/python/%PY_VER%/%PY_EXE% https://www.python.org/ftp/python/%PY_VER%/%PY_EXE%
set ALT_DOCKER_URLS=https://ghproxy.com/https://desktop.docker.com/win/main/amd64/Docker%%20Desktop%%20Installer.exe https://hub.fastgit.org/docker/desktop/releases/latest/download/Docker%%20Desktop%%20Installer.exe
set OFFICIAL_DOCKER_URL=https://desktop.docker.com/win/main/amd64/Docker%%20Desktop%%20Installer.exe

if not exist "%WORKDIR%" mkdir "%WORKDIR%"

echo [1/9] Downloading Python %PY_VER%...
call :DOWNLOAD_FILE "%PY_INSTALLER%" %PY_URLS%
if not exist "%PY_INSTALLER%" (
    echo [ERROR] Python download failed.
    pause
    exit /b
)

echo [2/9] Installing Python silently...
start /wait "" "%PY_INSTALLER%" /quiet InstallAllUsers=0 PrependPath=1 Include_pip=1 Include_launcher=1 Include_test=0

echo [3/9] Configuring pip mirror...
py -3.11 -m pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple >nul 2>&1
py -3.11 -m pip config set global.trusted-host pypi.tuna.tsinghua.edu.cn >nul 2>&1
py -3.11 -m pip install -U pip setuptools wheel uv pipx >nul 2>&1

echo [4/9] Enabling WSL2 and virtualization...
dism.exe /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart >nul 2>&1
dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart >nul 2>&1
wsl --set-default-version 2 >nul 2>&1

echo [5/9] Downloading Docker Desktop...
call :DOWNLOAD_FILE "%DOCKER_INSTALLER%" %ALT_DOCKER_URLS% %OFFICIAL_DOCKER_URL%
if not exist "%DOCKER_INSTALLER%" (
    echo [ERROR] Docker download failed.
    pause
    exit /b
)

echo [6/9] Installing Docker Desktop...
start /wait "" "%DOCKER_INSTALLER%" install --user --accept-license --backend=wsl-2

echo [7/9] Configuring Docker mirror...
set DOCKER_CFG=%USERPROFILE%\.docker
if not exist "%DOCKER_CFG%" mkdir "%DOCKER_CFG%"
echo {"registry-mirrors":["https://mirror.tuna.tsinghua.edu.cn"]} > "%DOCKER_CFG%\daemon.json"

echo [8/9] Installing Node.js (LTS)...
call :INSTALL_NODE

echo [9/9] Checking environment...
wsl --status > "%WORKDIR%\wsl_status.txt" 2>&1
docker info >nul 2>&1
echo [*] Base environment setup complete. It is recommended to restart your PC now.
pause
goto :eof

:DOWNLOAD_FILE
set "OUT=%~1"
shift
:DOWNLOAD_LOOP
if "%~1"=="" exit /b 0
set "URL=%~1"
echo Trying %URL%...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; $maxRetries=3; for($i=0;$i -lt $maxRetries;$i++){ try { $wc=New-Object System.Net.WebClient; $wc.Headers.Add('User-Agent','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'); $wc.DownloadFile('%URL%','%OUT%'); if ((Get-Item '%OUT%').Length -gt 10*1024*1024) { exit 0 } else { exit 1 } } catch { Start-Sleep -Seconds 2 } }; exit 1"
if !errorlevel! equ 0 (
    echo Download succeeded.
    exit /b 0
)
shift
goto DOWNLOAD_LOOP
exit /b 0

:INSTALL_NODE
echo Checking Node.js...
where node >nul 2>&1
if !errorlevel! equ 0 (
    echo Node.js already installed.
    node --version
    exit /b 0
)
echo Node.js not found. Attempting automatic setup...
set NODE_VER=18.18.0
set NODE_ZIP=node-v%NODE_VER%-win-x64.zip
set NODE_DIR=%ProgramFiles%\nodejs
set NODE_TEMP=%TEMP%\nodejs_install
if exist "%~dp0%NODE_ZIP%" (
    echo Found pre-downloaded %NODE_ZIP%, extracting...
    set "NODE_ZIP_PATH=%~dp0%NODE_ZIP%"
    goto EXTRACT_NODE
)
where winget >nul 2>&1
if !errorlevel! equ 0 (
    echo Installing via winget...
    winget install --id OpenJS.NodeJS.LTS --silent --accept-package-agreements --accept-source-agreements
    if !errorlevel! equ 0 (
        set "PATH=%PATH%;C:\Program Files\nodejs"
        call npm config set registry https://registry.npmmirror.com
        goto VERIFY_NODE
    )
)
echo Network limitation, cannot auto-download.
echo Opening browser, please download %NODE_ZIP% and place it in script directory, then press any key...
start https://nodejs.org/dist/v%NODE_VER%/%NODE_ZIP%
pause
if not exist "%~dp0%NODE_ZIP%" (
    echo [ERROR] File not found. Please download manually and retry.
    pause
    exit /b 1
)
set "NODE_ZIP_PATH=%~dp0%NODE_ZIP%"
:EXTRACT_NODE
if not exist "%NODE_TEMP%" mkdir "%NODE_TEMP%"
set NODE_EXTRACT=%NODE_TEMP%\extract
if not exist "%NODE_EXTRACT%" mkdir "%NODE_EXTRACT%"
echo Extracting...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -Path '%NODE_ZIP_PATH%' -DestinationPath '%NODE_EXTRACT%' -Force"
if not exist "%NODE_EXTRACT%\node-v%NODE_VER%-win-x64" (
    echo [ERROR] Extraction failed.
    pause
    exit /b 1
)
move /y "%NODE_EXTRACT%\node-v%NODE_VER%-win-x64" "%NODE_DIR%" >nul 2>&1
if %errorlevel% neq 0 (
    set NODE_DIR=%USERPROFILE%\nodejs
    move /y "%NODE_EXTRACT%\node-v%NODE_VER%-win-x64" "%NODE_DIR%" >nul 2>&1
)
if not exist "%NODE_DIR%\node.exe" (
    echo [ERROR] Move failed.
    pause
    exit /b 1
)
setx /M PATH "%NODE_DIR%;%PATH%" >nul 2>&1
if %errorlevel% neq 0 setx PATH "%NODE_DIR%;%PATH%" >nul
set "PATH=%NODE_DIR%;%PATH%"
if exist "%NODE_ZIP_PATH%" del /f /q "%NODE_ZIP_PATH%" >nul 2>&1
:VERIFY_NODE
where node >nul 2>&1
if !errorlevel! neq 0 (
    echo [ERROR] Node.js installation failed, please restart PC and retry.
    pause
    exit /b 1
)
echo Node.js installed successfully.
node --version
call npm config set registry https://registry.npmmirror.com
exit /b 0

:RUN_CLEANUP
title Megatron Cleanup
color 0C
cls
set "clean_choice="
if defined MEGATRON_TEST_CLEANUP_CHOICE (
    set "clean_choice=%MEGATRON_TEST_CLEANUP_CHOICE%"
) else (
    call :SHOW_I18N show-cleanup
    set /p clean_choice=
)
if "%clean_choice%"=="0" exit /b 0
if "%clean_choice%"=="1" (
    set WIPE_DOCKER=1
    set WIPE_PY=1
    set WIPE_WEB=1
    set WIPE_DATA=0
) else if "%clean_choice%"=="2" (
    set WIPE_DOCKER=1
    set WIPE_PY=0
    set WIPE_WEB=0
    set WIPE_DATA=0
) else if "%clean_choice%"=="3" (
    set WIPE_DOCKER=0
    set WIPE_PY=1
    set WIPE_WEB=0
    set WIPE_DATA=0
) else if "%clean_choice%"=="4" (
    set WIPE_DOCKER=0
    set WIPE_PY=0
    set WIPE_WEB=1
    set WIPE_DATA=0
) else if "%clean_choice%"=="5" (
    set WIPE_DOCKER=0
    set WIPE_PY=0
    set WIPE_WEB=0
    set WIPE_DATA=1
) else (
    goto RUN_CLEANUP
)
if "%WIPE_DATA%"=="1" (
    echo Clearing conversations and agent memory...
    if not exist "venv\Scripts\python.exe" (
        echo [ERROR] Python virtual environment not found. Start the backend once before data cleanup.
        pause
        exit /b 1
    )
    if not exist "pysrc\model.toml" (
        echo [ERROR] pysrc\model.toml not found. Start the backend once before data cleanup.
        pause
        exit /b 1
    )
    venv\Scripts\python "scripts\data_admin.py" --config "pysrc\model.toml" --clear-conversations --clear-memory --confirm
    if errorlevel 1 (
        echo [ERROR] Data cleanup failed. Make sure Docker databases are running, then retry.
        pause
        exit /b 1
    )
)
if "%WIPE_DOCKER%"=="1" (
    echo Destroying Docker containers and volumes...
    docker-compose down -v >nul 2>&1
    if exist "docker-compose.yml" del /f /q "docker-compose.yml"
)
if "%WIPE_PY%"=="1" (
    echo Deleting Python venv and cache...
    if exist "venv" rmdir /s /q "venv"
    if exist "pysrc\workspace" rmdir /s /q "pysrc\workspace"
    if exist ".pytest_cache" rmdir /s /q ".pytest_cache"
    if exist ".runtime" rmdir /s /q ".runtime"
    if exist "log" rmdir /s /q "log"
    if exist "logs" rmdir /s /q "logs"
    del /s /q *.log >nul 2>&1
    for /d /r . %%d in (__pycache__) do @if exist "%%d" rd /s /q "%%d" >nul 2>&1
)
if "%WIPE_WEB%"=="1" (
    echo Deleting frontend dependencies...
    if exist "node_modules" rmdir /s /q "node_modules"
    if exist "dist" rmdir /s /q "dist"
    if exist ".npm-cache" rmdir /s /q ".npm-cache"
    if exist ".npm-home" rmdir /s /q ".npm-home"
)
echo Cleanup complete. Closing...
call :SLEEP_SECONDS 3
exit /b 0

:RUN_BACKEND
title Megatron Backend
color 0B
if not defined MEGATRON_DOCKER_WAIT_MAX set "MEGATRON_DOCKER_WAIT_MAX=80"
if not defined MEGATRON_DB_WAIT_MAX set "MEGATRON_DB_WAIT_MAX=90"
if not defined MEGATRON_NEO4J_WAIT_MAX set "MEGATRON_NEO4J_WAIT_MAX=90"
echo Checking Python...
where python >nul 2>&1
if !errorlevel! neq 0 (
    echo [ERROR] Python not found, installing...
    call :RUN_INSTALL_ENV
    if !errorlevel! neq 0 (
        pause
        exit /b 1
    )
)

if not exist "venv" (
    echo Creating virtual environment...
    python -m venv venv
) else (
    venv\Scripts\python -c "exit()" >nul 2>&1
    if errorlevel 1 (
        echo Virtual environment broken, recreating...
        rmdir /s /q venv
        python -m venv venv
    )
)

call venv\Scripts\activate.bat
venv\Scripts\python -c "import sys; sys.path; exit(0)" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Virtual environment activation failed.
    pause
    exit /b
)

set "DOCKER_CONFIG=%~dp0.docker-cli"
if not exist "%DOCKER_CONFIG%" mkdir "%DOCKER_CONFIG%" >nul 2>&1
if not exist "%DOCKER_CONFIG%\config.json" echo {} > "%DOCKER_CONFIG%\config.json"

set "REQ_FILE="
if exist "requirements.txt" set "REQ_FILE=requirements.txt"
if exist "pysrc\requirements.txt" set "REQ_FILE=pysrc\requirements.txt"
if defined REQ_FILE (
    for /f %%h in ('powershell -NoProfile -Command "(Get-FileHash -Algorithm SHA256 '!REQ_FILE!').Hash"') do set "REQ_HASH=%%h"
    set "OLD_REQ_HASH="
    if exist "venv\.requirements.sha256" set /p OLD_REQ_HASH=<"venv\.requirements.sha256"
    if "!OLD_REQ_HASH!"=="!REQ_HASH!" (
        echo [OK] Python dependencies unchanged, skipping pip install.
    ) else (
        echo Installing Python dependencies...
        venv\Scripts\pip install -r "!REQ_FILE!"
        if errorlevel 1 (
            echo [ERROR] Python dependency installation failed.
            pause
            exit /b 1
        )
        > "venv\.requirements.sha256" echo !REQ_HASH!
    )
)

set "PLAYWRIGHT_BROWSERS_PATH=%~dp0venv\ms-playwright"
if not exist "%PLAYWRIGHT_BROWSERS_PATH%" mkdir "%PLAYWRIGHT_BROWSERS_PATH%" >nul 2>&1
venv\Scripts\python -c "import playwright" >nul 2>&1
if errorlevel 1 (
    echo Installing Playwright Python package...
    venv\Scripts\pip install playwright -q
)
dir /b "%PLAYWRIGHT_BROWSERS_PATH%\chromium-*" >nul 2>&1
if errorlevel 1 (
    echo Checking optional Playwright browser runtime...
    set "SKIP_PLAYWRIGHT_BROWSER=0"
    if "%~2"=="TEST" if not defined MEGATRON_INSTALL_PLAYWRIGHT set "SKIP_PLAYWRIGHT_BROWSER=1"
    if /i "%MEGATRON_INSTALL_PLAYWRIGHT%"=="0" set "SKIP_PLAYWRIGHT_BROWSER=1"
    if /i "%MEGATRON_INSTALL_PLAYWRIGHT%"=="false" set "SKIP_PLAYWRIGHT_BROWSER=1"
    if "%MEGATRON_INSTALL_PLAYWRIGHT%"=="1" set "SKIP_PLAYWRIGHT_BROWSER=0"
    if "!SKIP_PLAYWRIGHT_BROWSER!"=="1" (
        echo [WARN] Playwright Chromium is not installed. Skipping browser download so backend startup can continue.
        echo [HINT] Browser-based skills may be unavailable. Normal startup installs it automatically; set MEGATRON_INSTALL_PLAYWRIGHT=1 to force it in self-test.
    ) else (
        echo Installing Playwright browser into workspace, may take several minutes...
        echo [HINT] Set MEGATRON_INSTALL_PLAYWRIGHT=0 before running start.bat if you want to skip this optional browser runtime.
        set "PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright"
        venv\Scripts\playwright install chromium
        if errorlevel 1 (
            echo [WARN] Playwright mirror download failed. Retrying with official host...
            set "PLAYWRIGHT_DOWNLOAD_HOST="
            venv\Scripts\playwright install chromium
            if errorlevel 1 (
                echo [WARN] Playwright browser installation failed. Browser-based skills may be unavailable, backend startup will continue.
            )
        )
    )
) else (
    echo [OK] Playwright Chromium already installed.
)

venv\Scripts\python -c "import tomli_w" >nul 2>&1
if errorlevel 1 (
    echo Installing tomli-w...
    venv\Scripts\pip install tomli-w -q
)

if not exist "pysrc" mkdir "pysrc" >nul 2>&1
set "TOML_FILE=pysrc\model.toml"
if not exist "!TOML_FILE!" if exist "pysrc\model.example.toml" copy /y "pysrc\model.example.toml" "!TOML_FILE!" >nul
if not exist "!TOML_FILE!" if exist "model.toml" copy /y "model.toml" "!TOML_FILE!" >nul

echo [INFO] Checking and repairing model.toml if corrupted...
> repair_toml.py echo import sys, os
>> repair_toml.py echo toml_path = r'!TOML_FILE!'
>> repair_toml.py echo try:
>> repair_toml.py echo     with open(toml_path, 'rb') as f:
>> repair_toml.py echo         raw = f.read()
>> repair_toml.py echo     try:
>> repair_toml.py echo         raw.decode('utf-8')
>> repair_toml.py echo     except UnicodeDecodeError:
>> repair_toml.py echo         print("File corrupted, attempting to recover...")
>> repair_toml.py echo         backup = toml_path + ".bak"
>> repair_toml.py echo         if os.path.exists(backup):
>> repair_toml.py echo             os.replace(backup, toml_path)
>> repair_toml.py echo             print("Restored from backup")
>> repair_toml.py echo         else:
>> repair_toml.py echo             content = "[llm]\nactive_provider = \"openai\"\n\n[llm.openai]\napi_key = \"\"\nbase_url = \"https://api.openai.com/v1\"\nmodel = \"gpt-4o-mini\"\nextra_params = {}\n\n[redis]\n\n[postgres]\n\n[postgresql]\n\n[pgvector]\n\n[neo4j]\n"
>> repair_toml.py echo             with open(toml_path, 'w', encoding='utf-8') as fw:
>> repair_toml.py echo                 fw.write(content)
>> repair_toml.py echo             print("Created fresh config")
>> repair_toml.py echo     print("OK")
>> repair_toml.py echo except FileNotFoundError:
>> repair_toml.py echo     content = "[llm]\nactive_provider = \"openai\"\n\n[llm.openai]\napi_key = \"\"\nbase_url = \"https://api.openai.com/v1\"\nmodel = \"gpt-4o-mini\"\nextra_params = {}\n\n[redis]\n\n[postgres]\n\n[postgresql]\n\n[pgvector]\n\n[neo4j]\n"
>> repair_toml.py echo     with open(toml_path, 'w', encoding='utf-8') as fw:
>> repair_toml.py echo         fw.write(content)
>> repair_toml.py echo     print("Created new config")
venv\Scripts\python repair_toml.py
del repair_toml.py 2>nul

if not exist "!TOML_FILE!" (
    echo [ERROR] model.toml not found.
    pause
    exit /b
)

set "RUNTIME_MODE=%~2"
if "!RUNTIME_MODE!"=="" set "RUNTIME_MODE=CLI"
if "!RUNTIME_MODE!"=="TEST" set "MEGATRON_SKIP_LLM_SETUP=1"
call :CONFIGURE_LLM
if !errorlevel! neq 0 (
    if "%MEGATRON_NO_PAUSE%"=="1" exit /b 1
    pause
    exit /b 1
)

if not exist ".runtime" mkdir ".runtime" >nul 2>&1
set "RUNTIME_ENV_CMD=.runtime\runtime_env.cmd"
if exist "!RUNTIME_ENV_CMD!" del "!RUNTIME_ENV_CMD!" >nul 2>&1
venv\Scripts\python "scripts\runtime_setup.py" --toml "!TOML_FILE!" --runtime-dir ".runtime" --mode "!RUNTIME_MODE!"
if errorlevel 1 (
    if "%MEGATRON_NO_PAUSE%"=="1" exit /b 1
    pause
    exit /b 1
)
if exist "!RUNTIME_ENV_CMD!" call "!RUNTIME_ENV_CMD!"
goto RUNTIME_SETUP_COMPLETE

set "REDIS_PORT=6379"
set "REDIS_PASSWORD=root"
set "NEO4J_HTTP_PORT=7474"
set "NEO4J_BOLT_PORT=7687"
set "PG_USER=root"
set "PG_PASSWORD=root"
set "PG_DB=root"

echo [INFO] Using Redis port: !REDIS_PORT! Neo4j: !NEO4J_HTTP_PORT!/!NEO4J_BOLT_PORT!

set "PG_PORT="
set "REUSE_PORTS=0"
for /f %%p in ('venv\Scripts\python -c "import tomllib; d=tomllib.load(open(r'!TOML_FILE!','rb')); print(d.get('postgres', d.get('postgresql', d.get('pgvector', {}))).get('port', ''))" 2^>nul') do set "CONFIG_PG_PORT=%%p"
if defined CONFIG_PG_PORT (
    powershell -NoProfile -Command "try { $tcp=New-Object System.Net.Sockets.TcpClient; $tcp.Connect('127.0.0.1',!CONFIG_PG_PORT!); $tcp.Close(); exit 0 } catch { exit 1 }" >nul 2>&1
    if !errorlevel! equ 0 (
        set "PG_PORT=!CONFIG_PG_PORT!"
        set "REUSE_PORTS=1"
        echo [OK] Reusing running database ports. PostgreSQL: !PG_PORT!
    )
)
if not defined PG_PORT (
    echo [INFO] Scanning for free PostgreSQL port in range 54320-54330...
    for /l %%p in (54320,1,54330) do (
        powershell -NoProfile -Command "try { $tcp=New-Object System.Net.Sockets.TcpClient; $tcp.Connect('127.0.0.1',%%p); $tcp.Close(); exit 1 } catch { exit 0 }" >nul 2>&1
        if !errorlevel! equ 0 (
            set "PG_PORT=%%p"
            echo [OK] Free port found: !PG_PORT!
            goto PORT_FOUND
        )
    )
)
:PORT_FOUND
if not defined PG_PORT (
    echo [ERROR] No free port found in range 54320-54330.
    pause
    exit /b 1
)
echo [INFO] Checking bindable Neo4j ports...
call :FIND_FREE_PORT !NEO4J_HTTP_PORT! NEO4J_HTTP_PORT "Neo4j HTTP"
if errorlevel 1 (
    pause
    exit /b 1
)
call :FIND_FREE_PORT !NEO4J_BOLT_PORT! NEO4J_BOLT_PORT "Neo4j Bolt"
if errorlevel 1 (
    pause
    exit /b 1
)

echo [INFO] Updating model.toml with Redis password and dynamic ports...
> update_config.py echo import sys, tomllib, tomli_w
>> update_config.py echo toml_path = r'!TOML_FILE!'
>> update_config.py echo redis_pass = '!REDIS_PASSWORD!'
>> update_config.py echo pg_port = !PG_PORT!
>> update_config.py echo try:
>> update_config.py echo     with open(toml_path, 'rb') as f:
>> update_config.py echo         data = tomllib.load(f)
>> update_config.py echo except:
>> update_config.py echo     data = {}
>> update_config.py echo if 'redis' not in data: data['redis'] = {}
>> update_config.py echo data['redis']['password'] = redis_pass
>> update_config.py echo data['redis'].setdefault('blpop_timeout', 5)
>> update_config.py echo data['redis'].setdefault('socket_connect_timeout', 3)
>> update_config.py echo data['redis'].setdefault('socket_timeout', 10)
>> update_config.py echo data['redis'].setdefault('health_check_interval', 30)
>> update_config.py echo if 'postgresql' not in data: data['postgresql'] = {}
>> update_config.py echo data['postgresql']['host'] = 'localhost'
>> update_config.py echo data['postgresql']['port'] = pg_port
>> update_config.py echo data['postgresql']['user'] = 'root'
>> update_config.py echo data['postgresql']['password'] = 'root'
>> update_config.py echo data['postgresql']['database'] = 'root'
>> update_config.py echo if 'postgres' not in data: data['postgres'] = {}
>> update_config.py echo data['postgres']['host'] = 'localhost'
>> update_config.py echo data['postgres']['port'] = pg_port
>> update_config.py echo data['postgres']['user'] = 'root'
>> update_config.py echo data['postgres']['password'] = 'root'
>> update_config.py echo data['postgres']['database'] = 'root'
>> update_config.py echo if 'pgvector' not in data: data['pgvector'] = {}
>> update_config.py echo data['pgvector']['host'] = 'localhost'
>> update_config.py echo data['pgvector']['port'] = pg_port
>> update_config.py echo data['pgvector']['user'] = 'root'
>> update_config.py echo data['pgvector']['password'] = 'root'
>> update_config.py echo data['pgvector']['database'] = 'root'
>> update_config.py echo if 'neo4j' not in data: data['neo4j'] = {}
>> update_config.py echo data['neo4j']['uri'] = 'bolt://localhost:!NEO4J_BOLT_PORT!'
>> update_config.py echo data['neo4j']['user'] = 'neo4j'
>> update_config.py echo data['neo4j']['password'] = 'root'
>> update_config.py echo data['neo4j']['http_port'] = !NEO4J_HTTP_PORT!
>> update_config.py echo data['neo4j']['bolt_port'] = !NEO4J_BOLT_PORT!
>> update_config.py echo with open(toml_path, 'wb') as f:
>> update_config.py echo     tomli_w.dump(data, f)
>> update_config.py echo print("Configuration updated successfully")
venv\Scripts\python update_config.py
if errorlevel 1 (
    echo [ERROR] Failed to update model.toml
    pause
    exit /b
)
del update_config.py 2>nul

if "%MEGATRON_RESET_DBS%"=="1" (
    echo Resetting existing database containers...
    docker-compose down 2>nul
    for %%c in (megatron_postgres megatron_redis megatron_neo4j) do (
        docker rm -f %%c 2>nul
    )
    docker container prune -f >nul 2>&1
    call :SLEEP_SECONDS 2
) else (
    echo [OK] Preserving existing database containers. Set MEGATRON_RESET_DBS=1 to recreate them.
)

echo Generating docker-compose.yml with PostgreSQL port !PG_PORT!...
set "COMPOSE_FILE=docker-compose.yml"
> "!COMPOSE_FILE!" echo services:
>> "!COMPOSE_FILE!" echo   postgres:
>> "!COMPOSE_FILE!" echo     image: pgvector/pgvector:pg15
>> "!COMPOSE_FILE!" echo     container_name: megatron_postgres
>> "!COMPOSE_FILE!" echo     environment:
>> "!COMPOSE_FILE!" echo       POSTGRES_USER: !PG_USER!
>> "!COMPOSE_FILE!" echo       POSTGRES_PASSWORD: !PG_PASSWORD!
>> "!COMPOSE_FILE!" echo       POSTGRES_DB: !PG_DB!
>> "!COMPOSE_FILE!" echo     ports:
>> "!COMPOSE_FILE!" echo       - "!PG_PORT!:5432"
>> "!COMPOSE_FILE!" echo     restart: always
>> "!COMPOSE_FILE!" echo   redis:
>> "!COMPOSE_FILE!" echo     image: redis:alpine
>> "!COMPOSE_FILE!" echo     container_name: megatron_redis
>> "!COMPOSE_FILE!" echo     command: redis-server --requirepass !REDIS_PASSWORD!
>> "!COMPOSE_FILE!" echo     ports:
>> "!COMPOSE_FILE!" echo       - "!REDIS_PORT!:6379"
>> "!COMPOSE_FILE!" echo     restart: always
>> "!COMPOSE_FILE!" echo   neo4j:
>> "!COMPOSE_FILE!" echo     image: neo4j:5
>> "!COMPOSE_FILE!" echo     container_name: megatron_neo4j
>> "!COMPOSE_FILE!" echo     environment:
>> "!COMPOSE_FILE!" echo       NEO4J_AUTH: neo4j/root
>> "!COMPOSE_FILE!" echo       NEO4J_dbms_security_auth__minimum__password__length: 4
>> "!COMPOSE_FILE!" echo     ports:
>> "!COMPOSE_FILE!" echo       - "!NEO4J_HTTP_PORT!:7474"
>> "!COMPOSE_FILE!" echo       - "!NEO4J_BOLT_PORT!:7687"
>> "!COMPOSE_FILE!" echo     restart: always

echo Checking required ports...
set "SKIP_PORT_PROMPTS=0"
if "%~2"=="TEST" if "%MEGATRON_NO_PAUSE%"=="1" set "SKIP_PORT_PROMPTS=1"
if "!REUSE_PORTS!"=="1" (
    echo [OK] Existing database ports are in use by the running stack; skipping kill prompts.
) else if "!SKIP_PORT_PROMPTS!"=="1" (
    echo [INFO] Non-interactive self-test: skipping port kill prompts.
) else (
    call :CHECK_PORT !PG_PORT! PostgreSQL
    if !errorlevel! neq 0 exit /b 1
    call :CHECK_PORT !REDIS_PORT! Redis
    if !errorlevel! neq 0 exit /b 1
    call :CHECK_PORT !NEO4J_HTTP_PORT! Neo4j-http
    if !errorlevel! neq 0 exit /b 1
    call :CHECK_PORT !NEO4J_BOLT_PORT! Neo4j-bolt
    if !errorlevel! neq 0 exit /b 1
)

echo Checking Docker service...
set "SKIP_DOCKER_WAKE=0"
if "%~2"=="TEST" if "%MEGATRON_NO_PAUSE%"=="1" set "SKIP_DOCKER_WAKE=1"
if "!SKIP_DOCKER_WAKE!"=="1" (
    echo [INFO] Non-interactive self-test: checking Docker without launching Docker Desktop.
    call :CHECK_DOCKER_ENGINE >nul 2>&1
    if not "!DOCKER_ENGINE_READY!"=="1" (
        echo [ERROR] Docker engine is unavailable or permission was denied.
        echo [HINT] Start Docker Desktop manually, or run this launcher with sufficient Docker permissions.
        exit /b 1
    )
) else (
    tasklist | find /i "Docker Desktop.exe" >nul
    if !errorlevel! neq 0 (
        echo Waking Docker Desktop...
        if exist "C:\Program Files\Docker\Docker\Docker Desktop.exe" (
            start "" "C:\Program Files\Docker\Docker\Docker Desktop.exe"
        ) else (
            echo [ERROR] Docker Desktop not found. Please install it first.
            pause
            exit /b
        )
    ) else (
        echo [OK] Docker Desktop process detected.
    )
)

echo Waiting for Docker engine...
set /a DOCKER_WAIT_COUNT=0
:WAIT_DOCKER
call :CHECK_DOCKER_ENGINE >nul 2>&1
if not "!DOCKER_ENGINE_READY!"=="1" (
    set /a DOCKER_WAIT_COUNT+=1
    echo [INFO] Docker engine not ready yet: !DOCKER_WAIT_COUNT!/!MEGATRON_DOCKER_WAIT_MAX!. Docker Desktop may need 1-3 minutes.
    if !DOCKER_WAIT_COUNT! geq !MEGATRON_DOCKER_WAIT_MAX! (
        echo [ERROR] Docker engine is unavailable or permission was denied after !MEGATRON_DOCKER_WAIT_MAX! checks.
        echo [HINT] Open Docker Desktop and wait until it says "Docker Desktop is running".
        echo [HINT] If it still fails, make sure your Windows user is in the docker-users group, then log out and back in.
        if "%MEGATRON_NO_PAUSE%"=="1" exit /b 1
        pause
        exit /b 1
    )
    call :SLEEP_SECONDS 3
    goto WAIT_DOCKER
)

echo Starting database containers...
set "NEO4J_HTTP_BIND="
set "NEO4J_BOLT_BIND="
docker inspect megatron_neo4j >nul 2>&1
if !errorlevel! equ 0 (
    for /f "delims=" %%p in ('docker port megatron_neo4j 7474 2^>nul') do set "NEO4J_HTTP_BIND=%%p"
    for /f "delims=" %%p in ('docker port megatron_neo4j 7687 2^>nul') do set "NEO4J_BOLT_BIND=%%p"
    if not defined NEO4J_HTTP_BIND (
        echo [WARN] Existing Neo4j container has no host HTTP port binding; recreating it.
        docker rm -f megatron_neo4j >nul 2>&1
    ) else if not defined NEO4J_BOLT_BIND (
        echo [WARN] Existing Neo4j container has no host Bolt port binding; recreating it.
        docker rm -f megatron_neo4j >nul 2>&1
    )
)
docker-compose up -d
if errorlevel 1 (
    echo [ERROR] Docker compose up failed.
    if "%MEGATRON_NO_PAUSE%"=="1" exit /b 1
    pause
    exit /b
)

echo Waiting for PostgreSQL on port !PG_PORT!...
set /a DB_WAIT_COUNT=0
:WAIT_DB
docker exec megatron_postgres pg_isready -U root >nul 2>&1
if !errorlevel! neq 0 (
    set /a DB_WAIT_COUNT+=1
    if !DB_WAIT_COUNT! geq !MEGATRON_DB_WAIT_MAX! (
        echo [ERROR] PostgreSQL did not become ready after !MEGATRON_DB_WAIT_MAX! checks.
        docker logs --tail 80 megatron_postgres 2>nul
        if "%MEGATRON_NO_PAUSE%"=="1" exit /b 1
        pause
        exit /b 1
    )
    call :SLEEP_SECONDS 2
    goto WAIT_DB
)

echo PostgreSQL ready. Waiting for Neo4j...
set /a NEO4J_WAIT_COUNT=0
:WAIT_NEO4J
curl.exe -s -o nul http://127.0.0.1:!NEO4J_HTTP_PORT!
if !errorlevel! neq 0 (
    set /a NEO4J_WAIT_COUNT+=1
    if !NEO4J_WAIT_COUNT! geq !MEGATRON_NEO4J_WAIT_MAX! (
        echo [ERROR] Neo4j did not become ready after !MEGATRON_NEO4J_WAIT_MAX! checks.
        docker logs --tail 80 megatron_neo4j 2>nul
        if "%MEGATRON_NO_PAUSE%"=="1" exit /b 1
        pause
        exit /b 1
    )
    call :SLEEP_SECONDS 3
    goto WAIT_NEO4J
)

echo All databases ready.

echo Testing database connection before starting agent...
> test_db.py echo import asyncio, asyncpg, sys
>> test_db.py echo async def test():
>> test_db.py echo     try:
>> test_db.py echo         conn = await asyncpg.connect(host='localhost', port=!PG_PORT!, user='root', password='root', database='root')
>> test_db.py echo         await conn.execute('SELECT 1')
>> test_db.py echo         await conn.close()
>> test_db.py echo         print("CONNECTION_SUCCESS")
>> test_db.py echo     except Exception as e:
>> test_db.py echo         print(f"CONNECTION_FAILED: {e}")
>> test_db.py echo         sys.exit(1)
>> test_db.py echo asyncio.run(test())
venv\Scripts\python test_db.py
if errorlevel 1 (
    echo [ERROR] Database connection test failed. Exiting.
    del test_db.py 2>nul
    pause
    exit /b 1
)
del test_db.py 2>nul
echo [OK] Database connection verified.

:RUNTIME_SETUP_COMPLETE
set "ENTRY_FILE="
set "ENTRY_DIR="
if exist "agent.py" (
    set "ENTRY_FILE=agent.py"
    set "ENTRY_DIR=."
) else if exist "main.py" (
    set "ENTRY_FILE=main.py"
    set "ENTRY_DIR=."
) else if exist "pysrc\agent.py" (
    set "ENTRY_FILE=agent.py"
    set "ENTRY_DIR=pysrc"
) else if exist "pysrc\main.py" (
    set "ENTRY_FILE=main.py"
    set "ENTRY_DIR=pysrc"
)

if "!ENTRY_FILE!"=="" (
    echo [ERROR] No agent.py or main.py found.
    pause
    exit /b
)

REM Quick config validation before launch
echo [INFO] Validating configuration...
venv\Scripts\python "scripts\validate_config.py" --quick --lang "%MEGATRON_LANG%" 2>nul
if errorlevel 1 (
    echo [WARN] Config validation found issues ^(non-blocking^) - see above for details.
) else (
    echo [OK] Configuration validated.
)

echo Setting PostgreSQL environment variables...
set PGHOST=localhost
set PGPORT=!PG_PORT!
set PGUSER=root
set PGPASSWORD=root
set PGDATABASE=root

if "%~2"=="TEST" (
    echo Starting backend self-test...
    set AGENT_NO_CONSOLE_CONFIRM=1
    if not defined OPENAI_API_KEY set "OPENAI_API_KEY=megatron-self-test-key"
    set "MEGATRON_SKIP_LLM_CHECK=1"
    venv\Scripts\python "!ENTRY_DIR!\!ENTRY_FILE!" --self-test
    if errorlevel 1 exit /b 1
) else if "%~2"=="API" (
    if defined MEGATRON_BACKEND_PORT (
        set "BACKEND_PORT=!MEGATRON_BACKEND_PORT!"
    ) else (
        set "BACKEND_PORT=8000"
    )
    echo Checking for stale Megatron backend processes...
    call :STOP_STALE_BACKENDS
    echo Finding backend port starting at !BACKEND_PORT!...
    call :FIND_FREE_PORT !BACKEND_PORT! BACKEND_PORT "FastAPI backend"
    if errorlevel 1 (
        pause
        exit /b 1
    )
    set "MEGATRON_BACKEND_PORT=!BACKEND_PORT!"
    if not exist ".runtime" mkdir ".runtime" >nul 2>&1
    > ".runtime\backend_port.txt" echo !BACKEND_PORT!
    echo Starting FastAPI backend...
    set AGENT_NO_CONSOLE_CONFIRM=1
    venv\Scripts\python "!ENTRY_DIR!\!ENTRY_FILE!" --api --port !BACKEND_PORT!
) else (
    echo Starting CLI interactive mode...
    set AGENT_NO_CONSOLE_CONFIRM=0
    venv\Scripts\python "!ENTRY_DIR!\!ENTRY_FILE!"
)

if "%MEGATRON_NO_PAUSE%"=="1" exit /b 0
pause
exit /b 0

:CONFIGURE_LLM
echo.
if "%MEGATRON_SKIP_LLM_SETUP%"=="1" (
    echo [INFO] Non-interactive self-test: skipping LLM provider setup.
    exit /b 0
)
if not exist ".runtime" mkdir ".runtime" >nul 2>&1
set "LLM_ENV_CMD=.runtime\llm_env.cmd"
if exist "!LLM_ENV_CMD!" del "!LLM_ENV_CMD!" >nul 2>&1
venv\Scripts\python "scripts\llm_setup.py" --toml "!TOML_FILE!" --lang "%MEGATRON_LANG%" --env-cmd "!LLM_ENV_CMD!"
if errorlevel 1 (
    if exist "!LLM_ENV_CMD!" del "!LLM_ENV_CMD!" >nul 2>&1
    exit /b 1
)
if exist "!LLM_ENV_CMD!" call "!LLM_ENV_CMD!"
exit /b 0

:SLEEP_SECONDS
set /a "__SLEEP_COUNT=%~1+1"
ping 127.0.0.1 -n !__SLEEP_COUNT! >nul
exit /b 0

:CHECK_DOCKER_ENGINE
set "DOCKER_ENGINE_READY=0"
docker info >nul 2>&1 && goto DOCKER_ENGINE_OK
docker version >nul 2>&1 && goto DOCKER_ENGINE_OK
docker ps >nul 2>&1 && goto DOCKER_ENGINE_OK
docker inspect megatron_postgres >nul 2>&1 && goto DOCKER_ENGINE_OK
docker inspect megatron_redis >nul 2>&1 && goto DOCKER_ENGINE_OK
powershell -NoProfile -Command "try { $tcp=New-Object System.Net.Sockets.TcpClient; $tcp.Connect('127.0.0.1',54320); $tcp.Close(); exit 0 } catch { exit 1 }" >nul 2>&1 && goto DOCKER_ENGINE_OK
powershell -NoProfile -Command "try { $tcp=New-Object System.Net.Sockets.TcpClient; $tcp.Connect('127.0.0.1',6379); $tcp.Close(); exit 0 } catch { exit 1 }" >nul 2>&1 && goto DOCKER_ENGINE_OK
docker --context desktop-linux info >nul 2>&1 && (
    set "DOCKER_CONTEXT=desktop-linux"
    goto DOCKER_ENGINE_OK
)
docker --context desktop-linux ps >nul 2>&1 && (
    set "DOCKER_CONTEXT=desktop-linux"
    goto DOCKER_ENGINE_OK
)
exit /b 1

:DOCKER_ENGINE_OK
set "DOCKER_ENGINE_READY=1"
exit /b 0

:RUN_FRONTEND
title Megatron Frontend
color 0E
call :INSTALL_NODE
if !errorlevel! neq 0 (
    pause
    exit /b 1
)
if not exist "package.json" (
    echo [ERROR] package.json not found. Make sure you are in the frontend root.
    pause
    exit /b
)
set "MEGATRON_NPM_HOME=%~dp0.npm-home"
if not exist "%MEGATRON_NPM_HOME%" mkdir "%MEGATRON_NPM_HOME%" >nul 2>&1
if not exist "%MEGATRON_NPM_HOME%\AppData\Roaming" mkdir "%MEGATRON_NPM_HOME%\AppData\Roaming" >nul 2>&1
if not exist "%MEGATRON_NPM_HOME%\AppData\Local" mkdir "%MEGATRON_NPM_HOME%\AppData\Local" >nul 2>&1
set "HOME=%MEGATRON_NPM_HOME%"
set "USERPROFILE=%MEGATRON_NPM_HOME%"
set "APPDATA=%MEGATRON_NPM_HOME%\AppData\Roaming"
set "LOCALAPPDATA=%MEGATRON_NPM_HOME%\AppData\Local"
set "NPM_CONFIG_CACHE=%~dp0.npm-cache"
set "NPM_CONFIG_UPDATE_NOTIFIER=false"
set "NPM_CONFIG_FUND=false"
set "NPM_CONFIG_AUDIT=false"
if not exist "%NPM_CONFIG_CACHE%" mkdir "%NPM_CONFIG_CACHE%" >nul 2>&1
set "FRONTEND_PORT=3000"
for /f %%p in ('node -e "const fs=require('fs');let p=3000;try{const s=fs.readFileSync('config.toml','utf8');const m=s.match(/\\[frontend\\][\\s\\S]*?port\\s*=\\s*(\\d+)/);if(m)p=m[1]}catch(e){};console.log(p)"') do set "FRONTEND_PORT=%%p"
if defined VITE_FRONTEND_PORT set "FRONTEND_PORT=!VITE_FRONTEND_PORT!"
echo Checking for stale Megatron frontend processes...
call :STOP_STALE_FRONTENDS
echo Finding frontend port starting at !FRONTEND_PORT!...
call :FIND_FREE_PORT !FRONTEND_PORT! FRONTEND_PORT "Vite dev server"
if !errorlevel! neq 0 (
    pause
    exit /b
)
set "VITE_FRONTEND_PORT=!FRONTEND_PORT!"
if not exist ".runtime" mkdir ".runtime" >nul 2>&1
> ".runtime\frontend_port.txt" echo !FRONTEND_PORT!
if not defined VITE_API_BASE (
    if exist ".runtime\backend_port.txt" (
        set /p BACKEND_PORT_FROM_FILE=<".runtime\backend_port.txt"
        if defined BACKEND_PORT_FROM_FILE set "VITE_API_BASE=http://localhost:!BACKEND_PORT_FROM_FILE!"
    )
)
if not defined VITE_API_BASE set "VITE_API_BASE=http://localhost:8000"
echo Frontend will use API base: !VITE_API_BASE!
echo Setting npm mirror...
call npm cache clean --force >nul 2>&1
call npm config set registry https://registry.npmmirror.com
echo Installing frontend dependencies...
if exist "package-lock.json" (
    echo package-lock.json found. Using npm ci for a clean reproducible install...
    call npm ci --no-audit --no-fund
) else (
    echo package-lock.json not found. Using npm install...
    call npm install --no-audit --no-fund
)
if !errorlevel! neq 0 (
    echo Standard install failed, trying npm install fallback...
    call npm install --no-audit --no-fund
)
if !errorlevel! neq 0 (
    echo Fallback install failed, trying legacy peer deps...
    call npm install --no-audit --no-fund --legacy-peer-deps
    if !errorlevel! neq 0 (
        echo [ERROR] Dependency installation failed.
        pause
        exit /b
    )
)
if "%~2"=="TEST" (
    echo Running frontend self-test...
    call npm run lint
    if !errorlevel! neq 0 (
        echo [ERROR] Frontend typecheck failed.
        if "%MEGATRON_NO_PAUSE%"=="1" exit /b 1
        pause
        exit /b 1
    )
    call npm run build
    if !errorlevel! neq 0 (
        echo [ERROR] Frontend build failed.
        if "%MEGATRON_NO_PAUSE%"=="1" exit /b 1
        pause
        exit /b 1
    )
    echo [OK] Frontend self-test passed.
    exit /b 0
)
echo Starting frontend dev server...
call npm run dev
pause
exit /b 0
