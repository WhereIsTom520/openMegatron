@echo off
chcp 65001 >nul 2>&1
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"

title OpenMegatron Companion Model Setup
color 0B

echo.
echo   +------------------------------------------+
echo   ^|   Companion Model One-Click Setup         ^|
echo   +------------------------------------------+
echo.
echo   This will download, convert, and launch
echo   a small model for companion inference.
echo.

REM ── Step 0: Check llama.cpp ────────────────────────────────
set "LLAMA_DIR=.runtime\llama.cpp"
set "LLAMA_SERVER=%LLAMA_DIR%\llama-server.exe"
set "MODEL_DIR=.models\companion"

if not exist "%LLAMA_SERVER%" (
    echo   [1/5] Downloading llama.cpp...
    if not exist "%LLAMA_DIR%" mkdir "%LLAMA_DIR%"

    REM Try to find a pre-built release
    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
        "$url='https://github.com/ggerganov/llama.cpp/releases/download/b4807/llama-b4807-bin-win-cuda-cu13-x64.zip';" ^
        "try { Invoke-WebRequest -Uri $url -OutFile '%LLAMA_DIR%\llama.zip' } catch { " ^
        "  Write-Host 'Pre-built not available, please install llama.cpp manually'; exit 1 }" >nul 2>&1
    if errorlevel 1 (
        echo   [WARN] Could not auto-download llama.cpp.
        echo          Please download from: https://github.com/ggerganov/llama.cpp/releases
        echo          Extract to: %LLAMA_DIR%
        echo          And ensure llama-server.exe is at: %LLAMA_SERVER%
    ) else (
        powershell -NoProfile -ExecutionPolicy Bypass -Command ^
            "Expand-Archive -Path '%LLAMA_DIR%\llama.zip' -DestinationPath '%LLAMA_DIR%' -Force" >nul 2>&1
        del "%LLAMA_DIR%\llama.zip" >nul 2>&1
        echo   [OK] llama.cpp installed
    )
) else (
    echo   [1/5] llama.cpp found: %LLAMA_SERVER%
)

REM ── Step 1: Model selection ────────────────────────────────
echo.
echo   [2/5] Available models:
echo.
echo     1. Qwen2.5-1.5B-Instruct  (~1GB, fastest, 2GB VRAM)
echo     2. Qwen2.5-7B-Instruct    (~4GB, good balance, 6GB VRAM)
echo     3. Holo-3.1-4B            (~2.5GB, GUI-optimized, 6GB VRAM)
echo     4. Skip (I already have a GGUF file)
echo.
set /p MODEL_CHOICE="   Choose [1-4]: "

if "%MODEL_CHOICE%"=="1" set "MODEL_ID=Qwen/Qwen2.5-1.5B-Instruct" & set "MODEL_FILE=qwen2.5-1.5b-instruct-q4_k_m.gguf" & set "HF_FILE=Qwen2.5-1.5B-Instruct-Q4_K_M.gguf"
if "%MODEL_CHOICE%"=="2" set "MODEL_ID=Qwen/Qwen2.5-7B-Instruct" & set "MODEL_FILE=qwen2.5-7b-instruct-q4_k_m.gguf" & set "HF_FILE=Qwen2.5-7B-Instruct-Q4_K_M.gguf"
if "%MODEL_CHOICE%"=="3" set "MODEL_ID=Holo-3.1-4B" & set "MODEL_FILE=holo-3.1-4b-q4_k_m.gguf" & set "HF_FILE=Holo-3.1-4B-Q4_K_M.gguf"
if "%MODEL_CHOICE%"=="4" goto SKIP_DOWNLOAD

if not exist "%MODEL_DIR%" mkdir "%MODEL_DIR%"
set "GGUF_PATH=%MODEL_DIR%\%MODEL_FILE%"

REM ── Step 2: Download model ─────────────────────────────────
if exist "%GGUF_PATH%" (
    echo   [3/5] Model already downloaded: %GGUF_PATH%
    goto SKIP_DOWNLOAD
)

echo   [3/5] Downloading %MODEL_ID% (Q4_K_M quantized)...
echo   This may take 5-15 minutes depending on network speed...

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$url='https://huggingface.co/' + '%MODEL_ID%'.replace('/','/') + '/resolve/main/' + '%HF_FILE%';" ^
    "Write-Host '  URL: ' $url;" ^
    "try { Invoke-WebRequest -Uri $url -OutFile '%GGUF_PATH%' } catch {" ^
    "  Write-Host '  [WARN] Direct download failed. Please download manually from:';" ^
    "  Write-Host '  https://huggingface.co/%MODEL_ID%';" ^
    "  Write-Host '  and place the GGUF file at: %GGUF_PATH%';" ^
    "  exit 1 }" >nul 2>&1

if exist "%GGUF_PATH%" (
    echo   [OK] Model downloaded: %GGUF_PATH%
) else (
    echo   [WARN] Auto-download failed. You can manually place a GGUF file at:
    echo          %GGUF_PATH%
    goto SKIP_DOWNLOAD
)

:SKIP_DOWNLOAD
REM ── Step 3: Check for custom GGUF ──────────────────────────
if not exist "%GGUF_PATH%" (
    echo   [3/5] No GGUF found at %GGUF_PATH%
    echo   Please specify the path to your GGUF file:
    set /p GGUF_PATH="   GGUF path: "
    if not exist "!GGUF_PATH!" (
        echo   [ERROR] File not found: !GGUF_PATH!
        pause
        exit /b 1
    )
)

REM ── Step 4: Write model info ───────────────────────────────
echo   [4/5] Writing model metadata...
python -c "import json; json.dump({'model_id':'companion','model_path':'%GGUF_PATH%','backend':'llama_cpp','base_model':'%MODEL_ID%','task_domain':'text','is_active':True,'created_at':'%date%'}, open('%MODEL_DIR%/model_info.json','w'), indent=2)" 2>nul

REM ── Step 5: Launch llama.cpp server ─────────────────────────
echo   [5/5] Launching llama.cpp server...
echo.
echo   +------------------------------------------+
echo   ^|   Companion model: %MODEL_FILE%          ^|
echo   ^|   API: http://127.0.0.1:1234/v1          ^|
echo   +------------------------------------------+
echo.
echo   Starting server (keep this window open)...

"%LLAMA_SERVER%" ^
  -m "%GGUF_PATH%" ^
  -ngl 999 ^
  -c 4096 ^
  -fa ^
  --cache-type-k q4_0 ^
  --cache-type-v q4_0 ^
  --temp 0.2 ^
  --top-p 0.9 ^
  --host 127.0.0.1 ^
  --port 1234

pause
exit /b 0
