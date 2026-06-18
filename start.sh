#!/usr/bin/env bash
# OpenMegatron one-click launcher for Git Bash, WSL, Linux, and macOS.
# This script is fully self-contained and does not depend on start.bat or start.ps1.
#
# Usage:
#   bash start.sh                Start backend + frontend
#   bash start.sh health         Check service status
#   bash start.sh stop           Stop started processes
#   bash start.sh install        Install/update dependencies
#   bash start.sh test           Run tests
#   bash start.sh menu           Show interactive menu
#
# Options:
#   SKIP_DOCKER=1 bash start.sh  Skip Docker database setup
#   NO_BROWSER=1 bash start.sh   Do not open browser

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

RUNTIME_DIR="$ROOT/.runtime"
mkdir -p "$RUNTIME_DIR"
STARTUP_LOG="$RUNTIME_DIR/startup.log"

export PYTHONUTF8=1
export TQDM_DISABLE=1
export TOKENIZERS_PARALLELISM=false
export TRANSFORMERS_VERBOSITY=error

info() { printf '  [..] %s\n' "$*"; }
ok() { printf '  [OK] %s\n' "$*"; }
warn() { printf '  [!!] %s\n' "$*"; }
fail() { printf '  [XX] %s\n' "$*" >&2; }

# ==============================================
# PROGRESS BAR + ANIMATED SPINNER
# ==============================================
progress_bar() {
  local percent="$1" message="$2"
  local width=40
  local filled=$((percent * width / 100))
  local empty=$((width - filled))
  local bar=""
  for ((i = 0; i < filled; i++)); do bar="${bar}="; done
  for ((i = 0; i < empty; i++)); do bar="${bar}-"; done
  printf "   [%s] %d%%  %s\n" "$bar" "$percent" "$message"
}

_spinner_chars='|/-\'
_spinner_idx=0

spin_once() {
  local message="$1" seconds="$2"
  local char="${_spinner_chars:_spinner_idx:1}"
  _spinner_idx=$(((_spinner_idx + 1) % 4))
  printf "\r  %s %s (%s sec)... " "$char" "$message" "$seconds"
}

spin_clear() {
  printf "\r%s\r" "                                                                 "
}

spin_finish() {
  spin_clear
  printf "  \r"
}

# Usage: spinner_wait "check_command" "message" max_seconds
spinner_wait() {
  local check_fn="$1" message="$2" max_sec="$3"
  local start=$SECONDS
  local elapsed=0
  while (( elapsed < max_sec )); do
    if eval "$check_fn"; then
      spin_finish
      return 0
    fi
    elapsed=$((SECONDS - start))
    spin_once "$message" "$elapsed"
    sleep 0.2
  done
  spin_finish
  return 1
}

banner() {
  printf '\n============================================================\n'
  printf '  OpenMegatron one-click launcher\n'
  printf '============================================================\n\n'
}

check_port() {
  local port="$1"
  if command -v nc >/dev/null 2>&1; then
    nc -z 127.0.0.1 "$port" >/dev/null 2>&1
  else
    (echo >/dev/tcp/127.0.0.1/"$port") >/dev/null 2>&1
  fi
}

find_free_port() {
  local port="$1"
  while check_port "$port"; do port=$((port + 1)); done
  printf '%s\n' "$port"
}

wait_port() {
  local port="$1" label="${2:-service}" seconds="${3:-60}"
  local i
  for ((i = 1; i <= seconds; i++)); do
    if check_port "$port"; then return 0; fi
    if (( i % 5 == 0 )); then printf '      waiting for %s (%s/%s sec)...\n' "$label" "$i" "$seconds"; fi
    sleep 1
  done
  return 1
}

wait_http() {
  local url="$1" label="${2:-service}" seconds="${3:-90}"
  local i
  for ((i = 1; i <= seconds; i++)); do
    if command -v curl >/dev/null 2>&1 && curl -fsS "$url" >/dev/null 2>&1; then return 0; fi
    if (( i % 5 == 0 )); then printf '      waiting for %s (%s/%s sec)...\n' "$label" "$i" "$seconds"; fi
    sleep 1
  done
  return 1
}

hash_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

python_bin=""
venv_python() {
  case "$(uname -s 2>/dev/null || echo unknown)" in
    MINGW*|MSYS*|CYGWIN*)
      if [[ -x "$ROOT/venv/Scripts/python.exe" ]]; then printf '%s\n' "$ROOT/venv/Scripts/python.exe"; return; fi
      ;;
    *)
      if [[ -x "$ROOT/venv/bin/python" ]]; then printf '%s\n' "$ROOT/venv/bin/python"; return; fi
      ;;
  esac
  return 1
}

ensure_venv() {
  info "Checking Python virtual environment"
  if python_bin="$(venv_python 2>/dev/null)"; then
    ok "venv is ready"
    return
  fi
  local system_python=""
  for candidate in python3 python py; do
    if command -v "$candidate" >/dev/null 2>&1; then system_python="$candidate"; break; fi
  done
  if [[ -z "$system_python" ]]; then
    fail "Python 3.10+ was not found"
    exit 1
  fi
  info "Creating venv (first run only)"
  if [[ "$system_python" == "py" ]]; then
    "$system_python" -3 -m venv "$ROOT/venv"
  else
    "$system_python" -m venv "$ROOT/venv"
  fi
  python_bin="$(venv_python)"
  ok "venv created"
}

run_logged() {
  printf 'RUN %q' "$1" >> "$STARTUP_LOG"
  shift
  printf ' %q' "$@" >> "$STARTUP_LOG"
  printf '\n' >> "$STARTUP_LOG"
  "$@" 2>&1 | tee -a "$STARTUP_LOG"
}

ensure_python_deps() {
  info "Checking Python packages"
  local req="$ROOT/pysrc/requirements.txt"
  local hash_file_path="$ROOT/venv/.requirements.sha256"
  local hash old_hash=""
  hash="$(hash_file "$req")"
  [[ -f "$hash_file_path" ]] && old_hash="$(<"$hash_file_path")"
  if [[ "${REINSTALL:-0}" == "1" || "$hash" != "$old_hash" ]]; then
    info "Installing Python packages. This can take a few minutes."
    printf "\n"
    if ! "$python_bin" -m pip install -r "$req" --progress-bar on 2>&1; then
      printf "\n"
      warn "Default pip install failed, retrying with Tsinghua mirror"
      printf "\n"
      "$python_bin" -m pip install -r "$req" --progress-bar on -i https://pypi.tuna.tsinghua.edu.cn/simple 2>&1
    fi
    printf "\n"
    printf '%s' "$hash" > "$hash_file_path"
  fi
  "$python_bin" -c "import fastapi, uvicorn, pydantic" >/dev/null
  ok "Python packages are ready"
}

npm_bin=""
ensure_node() {
  if command -v npm >/dev/null 2>&1; then npm_bin="npm"; ok "npm is available"; return; fi
  if [[ -x "/c/Program Files/nodejs/npm.cmd" ]]; then npm_bin="/c/Program Files/nodejs/npm.cmd"; ok "npm is available"; return; fi
  fail "Node.js/npm was not found. Install Node.js LTS."
  exit 1
}

ensure_node_deps() {
  info "Checking frontend packages"
  local source="$ROOT/package.json"
  [[ -f "$ROOT/package-lock.json" ]] && source="$ROOT/package-lock.json"
  local hash_file_path="$RUNTIME_DIR/node_deps.sha256"
  local hash old_hash=""
  hash="$(hash_file "$source")"
  [[ -f "$hash_file_path" ]] && old_hash="$(<"$hash_file_path")"
  if [[ "${REINSTALL:-0}" == "1" || ! -d "$ROOT/node_modules" || "$hash" != "$old_hash" ]]; then
    info "Installing frontend packages"
    printf "\n"
    if [[ -f "$ROOT/package-lock.json" ]]; then
      if ! "$npm_bin" ci --no-audit --no-fund 2>&1; then
        printf "\n"
        warn "npm ci failed, retrying with npmmirror"
        printf "\n"
        "$npm_bin" config set registry https://registry.npmmirror.com >/dev/null
        "$npm_bin" ci --no-audit --no-fund 2>&1
      fi
    else
      "$npm_bin" install --no-audit --no-fund 2>&1
    fi
    printf "\n"
    printf '%s' "$hash" > "$hash_file_path"
  fi
  ok "Frontend packages are ready"
}

ensure_config() {
  info "Checking model config"
  if [[ ! -f "$ROOT/pysrc/model.toml" ]]; then
    if [[ ! -f "$ROOT/pysrc/model.example.toml" ]]; then
      fail "Missing pysrc/model.example.toml"
      exit 1
    fi
    cp "$ROOT/pysrc/model.example.toml" "$ROOT/pysrc/model.toml"
    warn "Created pysrc/model.toml from template. Add real API keys before using cloud models."
  else
    ok "model.toml exists"
  fi
}

ensure_llm_config() {
  if [[ "${MEGATRON_SKIP_LLM_SETUP:-0}" == "1" ]]; then
    warn "Skipping LLM setup because MEGATRON_SKIP_LLM_SETUP=1"
    return 0
  fi

  info "Checking LLM provider configuration"
  local model_toml="$ROOT/pysrc/model.toml"
  local env_cmd="$RUNTIME_DIR/runtime_env.cmd"
  local setup_script="$ROOT/scripts/llm_setup.py"
  if [[ ! -f "$setup_script" ]]; then
    warn "scripts/llm_setup.py is missing; backend will start in degraded mode if no API key is configured"
    return 0
  fi

  if "$python_bin" "$setup_script" --toml "$model_toml" --env-cmd "$env_cmd" --lang zh 2>&1 | tee -a "$STARTUP_LOG"; then
    ok "LLM provider configuration is ready"
    return 0
  fi

  warn "LLM provider is not configured. Starting is allowed, but chat will stay in setup/degraded mode."
  warn "Run this to configure later:"
  printf '      %q %q --toml %q --env-cmd %q --lang zh\n' "$python_bin" "$setup_script" "$model_toml" "$env_cmd"
  export MEGATRON_SKIP_LLM_SETUP=1
  if [[ "${MEGATRON_REQUIRE_LLM_SETUP:-0}" == "1" ]]; then
    return 1
  fi
  return 0
}

ensure_runtime() {
  if [[ "${SKIP_DOCKER:-0}" == "1" ]]; then
    warn "Skipping Docker/database setup"
    return
  fi

  # FAST: Check if database ports are already open
  if check_port 54320 && check_port 6379 && check_port 7807; then
    ok "Database ports reachable (already running)"
    return
  fi

  info "Checking Docker databases"

  # Check if Docker is available at all
  if ! docker info >/dev/null 2>&1 && ! docker --context desktop-linux info >/dev/null 2>&1; then
    warn "Docker is not running or not installed"
    warn "Skipping database setup - app may work in degraded mode"
    warn "Install/start Docker Desktop if you need full database support"
    return
  fi

  # TRY Docker setup - BUT WITH 60 SEC TIMEOUT!
  info "Starting Docker databases (MAX 60 SECONDS TIMEOUT)..."
  printf "        If this takes too long, press Ctrl+C and run: SKIP_DOCKER=1 bash start.sh\n"

  # Run setup in background with timeout
  local setup_done="$RUNTIME_DIR/docker_done.tmp"
  rm -f "$setup_done"

  ( "$python_bin" "$ROOT/scripts/runtime_setup.py" --toml "$ROOT/pysrc/model.toml" --runtime-dir "$RUNTIME_DIR" --mode API 2>&1 | tee -a "$STARTUP_LOG"
    echo "DONE" > "$setup_done" ) &
  local setup_pid=$!

  # Wait UP TO 60 seconds, NOT forever!
  local elapsed=0
  while (( elapsed < 60 )); do
    if [[ -f "$setup_done" ]]; then
      wait $setup_pid 2>/dev/null || true
      ok "Docker databases ready"
      rm -f "$setup_done"
      return
    fi
    sleep 2
    elapsed=$((elapsed + 2))
    local left=$((60 - elapsed))
    printf "\r  | Starting Docker DB (%s sec) - %s sec left     " "$elapsed" "$left"
  done
  printf "\n"

  # TIMEOUT! Kill setup and continue anyway
  warn "Docker setup TIMEOUT after 60 seconds"
  kill $setup_pid 2>/dev/null || true
  wait $setup_pid 2>/dev/null || true
  rm -f "$setup_done"

  warn "Skipping database check - OpenMegatron will try to start anyway"
  printf "      If this persists, run: SKIP_DOCKER=1 bash start.sh\n"
  printf "      Or check Docker manually: docker ps -a\n"
  printf "      Logs: %s\n" "$STARTUP_LOG"
}

read_port() {
  local name="$1" default="$2" path="$RUNTIME_DIR/${name}_port.txt"
  if [[ -f "$path" ]]; then
    tr -d '\r\n ' < "$path"
  else
    printf '%s\n' "$default"
  fi
}

open_url() {
  local url="$1"
  if [[ "${NO_BROWSER:-0}" == "1" ]]; then return; fi
  if command -v cmd.exe >/dev/null 2>&1; then cmd.exe /c start "" "$url" >/dev/null 2>&1 || true
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$url" >/dev/null 2>&1 || true
  elif command -v open >/dev/null 2>&1; then open "$url" >/dev/null 2>&1 || true
  fi
}

start_backend() {
  local port="${1:-$(read_port backend 8000)}"
  export PYTHONPATH="$ROOT/pysrc"
  export AGENT_NO_CONSOLE_CONFIRM=1
  "$python_bin" "$ROOT/pysrc/agent.py" --api --port "$port"
}

start_frontend() {
  local fp="${1:-$(read_port frontend 3000)}" bp="${2:-$(read_port backend 8000)}"
  export VITE_API_BASE="http://localhost:$bp"
  export VITE_FRONTEND_PORT="$fp"
  "$npm_bin" run dev -- --host 0.0.0.0 --port "$fp"
}

do_start() {
  banner
  : > "$STARTUP_LOG"

  progress_bar 10 "Step 1/6: Checking Python virtual environment"
  ensure_venv
  progress_bar 25 "Step 2/6: Checking Python packages"
  ensure_python_deps
  progress_bar 40 "Step 3/6: Checking Node.js + frontend packages"
  ensure_node
  ensure_node_deps
  progress_bar 55 "Step 4/6: Checking model config + databases"
  ensure_config
  ensure_llm_config
  ensure_runtime

  local bp fp
  bp="$(read_port backend 8000)"
  fp="$(find_free_port 3000)"
  printf '%s\n' "$fp" > "$RUNTIME_DIR/frontend_port.txt"

  progress_bar 70 "Step 5/6: Starting backend API"
  start_backend "$bp" > "$RUNTIME_DIR/backend_output.txt" 2> "$RUNTIME_DIR/backend_error.txt" &
  echo $! > "$RUNTIME_DIR/backend_pid.txt"
  info "Waiting for backend to start"
  if spinner_wait "check_port $bp" "Starting backend" 30; then
    ok "Backend is responding"
  else
    fail "Backend did not become ready on port $bp"
    # Try another port
    for ((attempt = 1; attempt < 5; attempt++)); do
      bp=$((bp + 1))
      warn "Port was unavailable, trying port $bp"
      start_backend "$bp" > "$RUNTIME_DIR/backend_output.txt" 2> "$RUNTIME_DIR/backend_error.txt" &
      echo $! > "$RUNTIME_DIR/backend_pid.txt"
      if spinner_wait "check_port $bp" "Starting backend" 30; then
        ok "Backend ready on port $bp"
        break
      fi
    done
  fi

  if ! check_port "$bp"; then
    fail "Backend did not become ready after trying multiple ports."
    echo ''
    echo '  =============================================================='
    echo '   Troubleshooting:'
    echo '   1. Check: .runtime/backend_error.txt'
    echo '   2. Run: bash start.sh install'
    echo '   3. Check your model.toml configuration'
    echo '  =============================================================='
    exit 1
  fi
  ok "Backend ready: http://localhost:$bp"

  progress_bar 85 "Step 6/6: Starting frontend (Vite)"
  start_frontend "$fp" "$bp" > "$RUNTIME_DIR/frontend_output.txt" 2> "$RUNTIME_DIR/frontend_error.txt" &
  echo $! > "$RUNTIME_DIR/frontend_pid.txt"
  # Friendly Vite compilation feedback with spinner
  info "Waiting for frontend to compile"
  spinner_wait "check_port $fp" "Compiling frontend" 120 || true

  if check_port "$fp"; then
    ok "Frontend ready: http://localhost:$fp"
  else
    warn "Frontend may still be compiling. Check the frontend window if browser shows blank page."
  fi

  progress_bar 100 "Complete!"

  printf '\n============================================================\n'
  printf '  OpenMegatron is ready!\n'
  printf '  Frontend: http://localhost:%s\n' "$fp"
  printf '  Backend:  http://localhost:%s\n' "$bp"
  printf '  API docs: http://localhost:%s/docs\n' "$bp"
  printf '  Logs:     %s\n' "$RUNTIME_DIR"
  printf '============================================================\n\n'
  open_url "http://localhost:$fp"
}

do_health() {
  banner
  local bp fp
  bp="$(read_port backend 8000)"
  fp="$(read_port frontend 3000)"
  if docker info >/dev/null 2>&1 || docker --context desktop-linux info >/dev/null 2>&1; then ok "Docker engine is available"; else warn "Docker engine is offline"; fi
  if check_port "$bp"; then ok "Backend port is open: $bp"; else warn "Backend is offline on port $bp"; fi
  if check_port "$fp"; then ok "Frontend port is open: $fp"; else warn "Frontend is offline on port $fp"; fi
}

do_stop() {
  info "Stopping processes started by launcher"
  for name in frontend backend; do
    local pid_file="$RUNTIME_DIR/${name}_pid.txt"
    if [[ -f "$pid_file" ]]; then
      local pid
      pid="$(<"$pid_file")"
      if kill "$pid" >/dev/null 2>&1; then ok "Stopped $name PID $pid"; else warn "$name PID $pid was not running"; fi
      rm -f "$pid_file"
    fi
  done
}

show_menu() {
  banner
  printf '  1. Start everything\n'
  printf '  2. Health check\n'
  printf '  3. Stop backend/frontend\n'
  printf '  4. Install/update dependencies\n'
  printf '  5. Run tests\n'
  printf '  0. Exit\n\n'
  read -r -p 'Choose: ' choice
  case "$choice" in
    1) do_start ;;
    2) do_health ;;
    3) do_stop ;;
    4) progress_bar 20 "Step 1/3: Python + virtual environment"; ensure_venv
       progress_bar 50 "Step 2/3: Python packages"; ensure_python_deps
       progress_bar 80 "Step 3/3: Node.js + frontend packages"; ensure_node; ensure_node_deps
       ensure_config
       progress_bar 100 "Complete!"
       ok "Install/update complete" ;;
    5) ensure_venv; ensure_python_deps; "$python_bin" -m pytest tests -q ;;
    *) exit 0 ;;
  esac
}

ACTION="${1:-start}"
case "$ACTION" in
  start) do_start ;;
  backend) ensure_venv; ensure_config; ensure_llm_config; start_backend "${2:-$(read_port backend 8000)}" ;;
  frontend) ensure_node; start_frontend "${2:-$(read_port frontend 3000)}" "${3:-$(read_port backend 8000)}" ;;
  health) do_health ;;
  stop) do_stop ;;
  install) progress_bar 20 "Step 1/3: Python"; ensure_venv
           progress_bar 50 "Step 2/3: Python packages"; ensure_python_deps
           progress_bar 80 "Step 3/3: Node.js packages"; ensure_node; ensure_node_deps
           ensure_config
           progress_bar 100 "Complete!"
           ok "Install complete" ;;
  test) ensure_venv; ensure_python_deps; "$python_bin" -m pytest tests -q ;;
  menu) show_menu ;;
  *) fail "Unknown action: $ACTION"; exit 1 ;;
esac
