#Requires -Version 5.1
<#
OpenMegatron one-click launcher.

Common usage:
  .\start.bat
  .\start.bat health
  .\start.bat stop

PowerShell usage:
  powershell -ExecutionPolicy Bypass -File .\start.ps1 start
#>
param(
    [ValidateSet('start', 'backend', 'frontend', 'health', 'install', 'stop', 'test', 'menu')]
    [string]$Action = 'start',
    [int]$BackendPort = 0,
    [int]$FrontendPort = 0,
    [switch]$NoBrowser,
    [switch]$SkipDocker,
    [switch]$Reinstall
)

$ErrorActionPreference = 'Stop'
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptRoot

$env:PYTHONUTF8 = '1'
$env:TQDM_DISABLE = '1'
$env:TOKENIZERS_PARALLELISM = 'false'
$env:TRANSFORMERS_VERBOSITY = 'error'

$RuntimeDir = Join-Path $ScriptRoot '.runtime'
if (-not (Test-Path $RuntimeDir)) {
    New-Item -ItemType Directory -Path $RuntimeDir -Force | Out-Null
}
$StartupLog = Join-Path $RuntimeDir 'startup.log'

function Write-Info { param([string]$Text) Write-Host "  [..] $Text" -ForegroundColor Cyan }
function Write-Ok { param([string]$Text) Write-Host "  [OK] $Text" -ForegroundColor Green }
function Write-Warn { param([string]$Text) Write-Host "  [!!] $Text" -ForegroundColor Yellow }
function Write-Fail { param([string]$Text) Write-Host "  [XX] $Text" -ForegroundColor Red }
function Add-Log { param([string]$Text) Add-Content -Path $StartupLog -Encoding UTF8 -Value "$(Get-Date -Format s) $Text" }

function Show-Banner {
    Write-Host ''
    Write-Host '============================================================' -ForegroundColor Cyan
    Write-Host '  OpenMegatron one-click launcher' -ForegroundColor Cyan
    Write-Host '============================================================' -ForegroundColor Cyan
    Write-Host ''
}

function Test-PortOpen {
    param([int]$Port, [int]$TimeoutMs = 300)
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $result = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
        if ($result.AsyncWaitHandle.WaitOne($TimeoutMs)) {
            $client.EndConnect($result)
            $client.Close()
            return $true
        }
        $client.Close()
        return $false
    } catch {
        return $false
    }
}

function Find-FreePort {
    param([int]$Preferred)
    for ($port = $Preferred; $port -lt 65535; $port++) {
        if (-not (Test-PortOpen -Port $port)) { return $port }
    }
    throw "No free TCP port found from $Preferred"
}

function Wait-ForHttp {
    param([string]$Url, [int]$Seconds = 90, [string]$Label = 'service')
    for ($i = 1; $i -le $Seconds; $i++) {
        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) { return $true }
        } catch { }
        if (($i % 5) -eq 0) { Write-Host "      waiting for $Label ($i/$Seconds sec)..." }
        Start-Sleep -Seconds 1
    }
    return $false
}

function Wait-ForPort {
    param([int]$Port, [int]$Seconds = 60, [string]$Label = 'service')
    for ($i = 1; $i -le $Seconds; $i++) {
        if (Test-PortOpen -Port $Port) { return $true }
        if (($i % 5) -eq 0) { Write-Host "      waiting for $Label ($i/$Seconds sec)..." }
        Start-Sleep -Seconds 1
    }
    return $false
}

function Read-PortFile {
    param([string]$Name, [int]$Default)
    $path = Join-Path $RuntimeDir "$Name`_port.txt"
    if (Test-Path $path) {
        $raw = (Get-Content $path -Raw -ErrorAction SilentlyContinue).Trim()
        $value = 0
        if ([int]::TryParse($raw, [ref]$value)) { return $value }
    }
    return $Default
}

function Write-PortFile {
    param([string]$Name, [int]$Port)
    $path = Join-Path $RuntimeDir "$Name`_port.txt"
    Set-Content -Path $path -Encoding ASCII -NoNewline -Value $Port
}

function Get-CommandPath {
    param([string[]]$Names)
    foreach ($name in $Names) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) { return $cmd.Source }
    }
    return $null
}

function Get-Python {
    $venvPython = Join-Path $ScriptRoot 'venv\Scripts\python.exe'
    if (Test-Path $venvPython) { return $venvPython }

    $python = Get-CommandPath @('py.exe', 'python.exe', 'python')
    if (-not $python) {
        Write-Fail 'Python was not found. Install Python 3.10+ and run start.bat again.'
        Start-Process 'https://www.python.org/downloads/' | Out-Null
        throw 'Python missing'
    }
    return $python
}

function Ensure-Venv {
    Write-Info 'Checking Python virtual environment'
    $venvPython = Join-Path $ScriptRoot 'venv\Scripts\python.exe'
    if (Test-Path $venvPython) {
        Write-Ok 'venv is ready'
        return $venvPython
    }

    $python = Get-CommandPath @('py.exe', 'python.exe', 'python')
    if (-not $python) {
        Write-Fail 'Python was not found. Opening download page.'
        Start-Process 'https://www.python.org/downloads/' | Out-Null
        throw 'Python missing'
    }

    Write-Info 'Creating venv (first run only)'
    if ((Split-Path -Leaf $python) -ieq 'py.exe') {
        & $python -3 -m venv (Join-Path $ScriptRoot 'venv')
    } else {
        & $python -m venv (Join-Path $ScriptRoot 'venv')
    }
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $venvPython)) {
        throw 'Could not create Python virtual environment'
    }
    Write-Ok 'venv created'
    return $venvPython
}

function Invoke-WithLog {
    param([string]$File, [string[]]$Arguments, [string]$Label)
    Add-Log "RUN $File $($Arguments -join ' ')"
    & $File @Arguments 2>&1 | Tee-Object -FilePath $StartupLog -Append
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed. See $StartupLog"
    }
}

function Ensure-PythonDeps {
    param([string]$Python)
    Write-Info 'Checking Python packages'
    $requirements = Join-Path $ScriptRoot 'pysrc\requirements.txt'
    if (-not (Test-Path $requirements)) { throw 'Missing pysrc\requirements.txt' }

    $hashFile = Join-Path $ScriptRoot 'venv\.requirements.sha256'
    $hash = (Get-FileHash -Algorithm SHA256 $requirements).Hash
    $oldHash = ''
    if (Test-Path $hashFile) { $oldHash = (Get-Content $hashFile -Raw).Trim() }

    if ($Reinstall -or $hash -ne $oldHash) {
        Write-Info 'Installing Python packages. This can take a few minutes.'
        try {
            Invoke-WithLog -File $Python -Arguments @('-m', 'pip', 'install', '-r', $requirements) -Label 'pip install'
        } catch {
            Write-Warn 'Default pip install failed, retrying with Tsinghua mirror'
            Invoke-WithLog -File $Python -Arguments @('-m', 'pip', 'install', '-r', $requirements, '-i', 'https://pypi.tuna.tsinghua.edu.cn/simple') -Label 'pip install mirror'
        }
        Set-Content -Path $hashFile -Encoding ASCII -NoNewline -Value $hash
    }

    & $Python -c "import fastapi, uvicorn, pydantic" 2>$null
    if ($LASTEXITCODE -ne 0) { throw 'Python dependency check failed' }
    Write-Ok 'Python packages are ready'
}

function Get-Npm {
    $npm = Get-CommandPath @('npm.cmd', 'npm')
    if (-not $npm) {
        Write-Fail 'Node.js/npm was not found. Install Node.js LTS and run start.bat again.'
        Start-Process 'https://nodejs.org/' | Out-Null
        throw 'Node.js missing'
    }
    return $npm
}

function Ensure-NodeDeps {
    param([string]$Npm)
    Write-Info 'Checking frontend packages'
    $lock = Join-Path $ScriptRoot 'package-lock.json'
    $pkg = Join-Path $ScriptRoot 'package.json'
    $hashSource = if (Test-Path $lock) { $lock } else { $pkg }
    $hashFile = Join-Path $RuntimeDir 'node_deps.sha256'
    $hash = (Get-FileHash -Algorithm SHA256 $hashSource).Hash
    $oldHash = ''
    if (Test-Path $hashFile) { $oldHash = (Get-Content $hashFile -Raw).Trim() }

    if ($Reinstall -or -not (Test-Path (Join-Path $ScriptRoot 'node_modules')) -or $hash -ne $oldHash) {
        Write-Info 'Installing frontend packages'
        if (Test-Path $lock) {
            try {
                Invoke-WithLog -File $Npm -Arguments @('ci', '--no-audit', '--no-fund') -Label 'npm ci'
            } catch {
                Write-Warn 'npm ci failed, retrying with npmmirror'
                & $Npm config set registry https://registry.npmmirror.com | Out-Null
                Invoke-WithLog -File $Npm -Arguments @('ci', '--no-audit', '--no-fund') -Label 'npm ci mirror'
            }
        } else {
            Invoke-WithLog -File $Npm -Arguments @('install', '--no-audit', '--no-fund') -Label 'npm install'
        }
        Set-Content -Path $hashFile -Encoding ASCII -NoNewline -Value $hash
    }
    Write-Ok 'Frontend packages are ready'
}

function Ensure-Config {
    Write-Info 'Checking model config'
    $modelToml = Join-Path $ScriptRoot 'pysrc\model.toml'
    $example = Join-Path $ScriptRoot 'pysrc\model.example.toml'
    if (-not (Test-Path $modelToml)) {
        if (-not (Test-Path $example)) { throw 'Missing pysrc\model.example.toml' }
        Copy-Item $example $modelToml
        Write-Warn 'Created pysrc\model.toml from template. Add real API keys before using cloud models.'
    } else {
        Write-Ok 'model.toml exists'
    }
}

function Get-DockerCommand {
    $docker = Get-CommandPath @('docker.exe', 'docker')
    if (-not $docker) { return $null }

    $oldPreference = $ErrorActionPreference
    $ErrorActionPreference = 'SilentlyContinue'
    try {
        & $docker info *> $null
        if ($LASTEXITCODE -eq 0) { return @($docker) }
        & $docker --context desktop-linux info *> $null
        if ($LASTEXITCODE -eq 0) { return @($docker, '--context', 'desktop-linux') }
    } catch {
        return $null
    } finally {
        $ErrorActionPreference = $oldPreference
    }
    return $null
}

function Wake-DockerDesktop {
    $candidates = @(
        "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe",
        "$env:LOCALAPPDATA\Docker\Docker Desktop.exe"
    )
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) {
            Write-Warn 'Docker is not ready; starting Docker Desktop'
            Start-Process $candidate | Out-Null
            return $true
        }
    }
    return $false
}

function Ensure-DockerRuntime {
    param([string]$Python)
    if ($SkipDocker) {
        Write-Warn 'Skipping Docker/database setup because -SkipDocker was passed'
        return
    }

    Write-Info 'Checking Docker databases'

    # Quick check: are database ports already reachable? If so, skip Docker.
    $pgPort = 5432
    $redisPort = 6379
    $neo4jPort = 7687
    $pgOk = Test-PortOpen $pgPort
    $redisOk = Test-PortOpen $redisPort
    $neo4jOk = Test-PortOpen $neo4jPort

    if ($pgOk -and $redisOk -and $neo4jOk) {
        Write-Ok "All database ports reachable (PG:$pgPort Redis:$redisPort Neo4j:$neo4jPort)"
        return
    }

    $docker = Get-DockerCommand
    if (-not $docker) {
        if (Wake-DockerDesktop) {
            Write-Info 'Waiting for Docker Desktop to start (max 60s)...'
            for ($i = 1; $i -le 20; $i++) {
                Start-Sleep -Seconds 3
                $docker = Get-DockerCommand
                if ($docker) { break }
                if ($i % 4 -eq 0) { Write-Host "      waiting for Docker ($($i * 3)/60 sec)..." }
            }
        }
    }
    if (-not $docker) {
        Write-Warn 'Docker is not available — databases may be running externally'
        Write-Warn "If you have PostgreSQL, Redis, and Neo4j running elsewhere,"
        Write-Warn "configure their ports in pysrc\model.toml and continue."
        Write-Warn ''
        $proceed = Read-Host 'Continue without Docker? [Y/n]'
        if ($proceed -ne '' -and $proceed -notmatch '^[yY]') {
            throw 'Startup cancelled by user'
        }
        Write-Warn 'Proceeding without Docker. Make sure databases are running externally.'
        return
    }

    $modelToml = Join-Path $ScriptRoot 'pysrc\model.toml'
    if ($BackendPort -gt 0) { $env:MEGATRON_BACKEND_PORT = "$BackendPort" }
    try {
        Invoke-WithLog -File $Python -Arguments @((Join-Path $ScriptRoot 'scripts\runtime_setup.py'), '--toml', $modelToml, '--runtime-dir', $RuntimeDir, '--mode', 'API') -Label 'runtime setup'
    } catch {
        Write-Warn 'Runtime setup failed — databases may not be ready.'
        Write-Warn 'If databases are running externally, you can ignore this warning.'
        Write-Warn ''
        $proceed = Read-Host 'Continue anyway? [Y/n]'
        if ($proceed -ne '' -and $proceed -notmatch '^[yY]') {
            throw 'Startup cancelled by user'
        }
    }
    Write-Ok 'Docker databases are ready'
}

function Load-RuntimeEnv {
    $envFile = Join-Path $RuntimeDir 'runtime_env.cmd'
    if (-not (Test-Path $envFile)) { return }
    foreach ($line in Get-Content $envFile) {
        if ($line -match '^set "([^=]+)=(.*)"$') {
            [Environment]::SetEnvironmentVariable($matches[1], $matches[2], 'Process')
        }
    }
}

function Start-BackendWindow {
    param([int]$Port)
    Write-PortFile 'backend' $Port
    $ps = Get-CommandPath @('powershell.exe', 'powershell')
    $script = Join-Path $ScriptRoot 'start.ps1'
    $args = @('-NoExit', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $script, 'backend', '-BackendPort', "$Port")
    $proc = Start-Process -FilePath $ps -ArgumentList $args -PassThru -WindowStyle Normal
    Set-Content -Path (Join-Path $RuntimeDir 'backend_pid.txt') -Encoding ASCII -NoNewline -Value $proc.Id
    return $proc
}

function Start-FrontendWindow {
    param([int]$Port, [int]$ApiPort)
    Write-PortFile 'frontend' $Port
    $ps = Get-CommandPath @('powershell.exe', 'powershell')
    $script = Join-Path $ScriptRoot 'start.ps1'
    $args = @('-NoExit', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $script, 'frontend', '-FrontendPort', "$Port", '-BackendPort', "$ApiPort")
    $proc = Start-Process -FilePath $ps -ArgumentList $args -PassThru -WindowStyle Normal
    Set-Content -Path (Join-Path $RuntimeDir 'frontend_pid.txt') -Encoding ASCII -NoNewline -Value $proc.Id
    return $proc
}

function Stop-StartedProcesses {
    Write-Info 'Stopping processes started by launcher'
    foreach ($name in @('frontend', 'backend')) {
        $pidFile = Join-Path $RuntimeDir "$name`_pid.txt"
        if (Test-Path $pidFile) {
            $raw = (Get-Content $pidFile -Raw).Trim()
            $pidValue = 0
            if ([int]::TryParse($raw, [ref]$pidValue)) {
                try {
                    Stop-Process -Id $pidValue -Force -ErrorAction Stop
                    Write-Ok "Stopped $name PID $pidValue"
                } catch {
                    Write-Warn "$name PID $pidValue was not running"
                }
            }
            Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
        }
    }
}

function Show-Health {
    $bp = Read-PortFile 'backend' 8000
    $fp = Read-PortFile 'frontend' 3000
    Show-Banner
    Write-Info 'Health check'

    if (Get-DockerCommand) { Write-Ok 'Docker engine is available' } else { Write-Warn 'Docker engine is not available' }

    if (Test-PortOpen $bp) {
        try {
            $status = Invoke-RestMethod -Uri "http://127.0.0.1:$bp/runtime_status" -TimeoutSec 3
            Write-Ok "Backend API is online on http://localhost:$bp"
            if ($status.skills) { Write-Host "      skills: $($status.skills.loaded)/$($status.skills.total)" }
        } catch {
            Write-Warn "Backend port $bp is open, but /runtime_status did not respond"
        }
    } else {
        Write-Warn "Backend API is offline on port $bp"
    }

    if (Test-PortOpen $fp) {
        Write-Ok "Frontend is online on http://localhost:$fp"
    } else {
        Write-Warn "Frontend is offline on port $fp"
    }
}

function Show-Menu {
    Show-Banner
    Write-Host '  1. Start everything'
    Write-Host '  2. Health check'
    Write-Host '  3. Stop backend/frontend'
    Write-Host '  4. Install/update dependencies'
    Write-Host '  5. Run tests'
    Write-Host '  0. Exit'
    Write-Host ''
    $choice = Read-Host 'Choose'
    switch ($choice) {
        '1' { & $PSCommandPath start }
        '2' { & $PSCommandPath health }
        '3' { & $PSCommandPath stop }
        '4' { & $PSCommandPath install }
        '5' { & $PSCommandPath test }
        default { return }
    }
}

function Install-All {
    Show-Banner
    Set-Content -Path $StartupLog -Encoding UTF8 -Value "OpenMegatron startup log"
    $python = Ensure-Venv
    Ensure-PythonDeps -Python $python
    $npm = Get-Npm
    Ensure-NodeDeps -Npm $npm
    Ensure-Config
    Write-Ok 'Install/update complete'
}

function Start-All {
    Show-Banner
    Set-Content -Path $StartupLog -Encoding UTF8 -Value "OpenMegatron startup log"
    $python = Ensure-Venv
    Ensure-PythonDeps -Python $python
    $npm = Get-Npm
    Ensure-NodeDeps -Npm $npm
    Ensure-Config
    Ensure-DockerRuntime -Python $python

    $bp = Read-PortFile 'backend' 8000
    if ($BackendPort -gt 0) { $bp = $BackendPort }
    if (Test-PortOpen $bp) {
        Write-Warn "Backend port $bp is already in use; using existing service or next free port"
    }
    Start-BackendWindow -Port $bp | Out-Null

    if (-not (Wait-ForHttp "http://127.0.0.1:$bp/runtime_status" 120 'backend API')) {
        Write-Fail "Backend did not become ready. Check $RuntimeDir\backend_pid.txt and the backend window."
        throw 'Backend startup failed'
    }
    Write-Ok "Backend ready: http://localhost:$bp"

    $fp = if ($FrontendPort -gt 0) { $FrontendPort } else { Find-FreePort 3000 }
    Start-FrontendWindow -Port $fp -ApiPort $bp | Out-Null
    if (-not (Wait-ForPort $fp 90 'frontend')) {
        Write-Warn "Frontend may still be compiling. Check the frontend window if the browser is blank."
    } else {
        Write-Ok "Frontend ready: http://localhost:$fp"
    }

    Write-Host ''
    Write-Host '============================================================' -ForegroundColor Green
    Write-Host '  OpenMegatron is ready' -ForegroundColor Green
    Write-Host "  Frontend: http://localhost:$fp"
    Write-Host "  Backend:  http://localhost:$bp"
    Write-Host "  API docs: http://localhost:$bp/docs"
    Write-Host "  Logs:     $RuntimeDir"
    Write-Host '============================================================' -ForegroundColor Green
    Write-Host ''

    if (-not $NoBrowser) {
        Start-Process "http://localhost:$fp" | Out-Null
    }
}

try {
    switch ($Action) {
        'menu' { Show-Menu }
        'install' { Install-All }
        'health' { Show-Health }
        'stop' { Stop-StartedProcesses }
        'test' {
            $python = Ensure-Venv
            Ensure-PythonDeps -Python $python
            Invoke-WithLog -File $python -Arguments @('-m', 'pytest', 'tests', '-q') -Label 'pytest'
        }
        'backend' {
            Load-RuntimeEnv
            $python = Ensure-Venv
            if ($BackendPort -le 0) { $BackendPort = Read-PortFile 'backend' 8000 }
            $env:PYTHONPATH = Join-Path $ScriptRoot 'pysrc'
            $env:AGENT_NO_CONSOLE_CONFIRM = '1'
            & $python (Join-Path $ScriptRoot 'pysrc\agent.py') --api --port $BackendPort
        }
        'frontend' {
            Load-RuntimeEnv
            $npm = Get-Npm
            if ($FrontendPort -le 0) { $FrontendPort = Read-PortFile 'frontend' 3000 }
            if ($BackendPort -le 0) { $BackendPort = Read-PortFile 'backend' 8000 }
            $env:VITE_API_BASE = "http://localhost:$BackendPort"
            $env:VITE_FRONTEND_PORT = "$FrontendPort"
            & $npm run dev -- --host 0.0.0.0 --port $FrontendPort
        }
        'start' { Start-All }
    }
    exit 0
} catch {
    Write-Fail $_.Exception.Message
    Add-Log "ERROR $($_.Exception.Message)"
    Write-Host ''
    Write-Host "Troubleshooting:"
    Write-Host "  1. Run: start.bat health"
    Write-Host "  2. Run: start.bat install"
    Write-Host "  3. Check logs in: $RuntimeDir"
    Write-Host ''
    exit 1
}
