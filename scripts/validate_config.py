#!/usr/bin/env python3
"""
Config validator: checks model.toml, docker-compose.yml, and runtime ports
before startup, with clear bilingual error messages.

Usage:
  python scripts/validate_config.py                    # full check
  python scripts/validate_config.py --quick            # skip port/docker checks
  python scripts/validate_config.py --lang zh          # Chinese output
"""

import argparse
import json
import os
import socket
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

MESSAGES = {
    "zh": {
        "title": "openMegatron 配置检查",
        "divider": "=" * 48,
        "ok": "[OK]",
        "fail": "[ERR]",
        "warn": "[WARN]",
        "info": "[*]",
        "toml_found": "找到 model.toml",
        "toml_missing": "未找到 model.toml — 请先运行 llm_setup.py 或在 pysrc/ 下创建它",
        "toml_parse_error": "model.toml 解析失败: {err}",
        "llm_section": "LLM 配置",
        "llm_no_provider": "未配置任何 LLM 提供商 — 至少需要一个 API key",
        "active_provider": "活跃提供商: {name}",
        "provider_no_key": "{name}: API key 为空 — 前端会提示配置",
        "provider_ok": "{name}: model={model}",
        "db_section": "数据库配置",
        "postgres_ok": "PostgreSQL: localhost:{port}",
        "redis_ok": "Redis: localhost:{port}",
        "neo4j_ok": "Neo4j: {uri}",
        "ports_section": "端口检查",
        "port_free": "端口 {port} 可用",
        "port_in_use": "端口 {port} 已被占用 ({proc}) — 启动器自动切换",
        "docker_section": "Docker 状态",
        "docker_ok": "Docker 引擎就绪",
        "docker_not_ready": "Docker 未运行 — 启动器会自动启动 Docker Desktop",
        "containers_section": "容器状态",
        "container_running": "{name}: 运行中",
        "container_stopped": "{name}: 已停止 (启动器会启动它们)",
        "container_missing": "{name}: 未创建 (启动器会创建)",
        "all_ok": "所有检查通过！可以启动。",
        "warnings_found": "发现 {count} 个警告 (不影响启动)。",
        "errors_found": "发现 {count} 个错误，需要修复后才能启动。",
        "critical": "严重错误",
        "llm_setup_tip": "运行 python scripts/llm_setup.py 来配置 AI 提供商",
        "docker_tip": "运行 Docker Desktop 或使用选项 4 安装",
        "hint": "提示",
    },
    "en": {
        "title": "openMegatron Config Check",
        "divider": "=" * 48,
        "ok": "[OK]",
        "fail": "[ERR]",
        "warn": "[WARN]",
        "info": "[*]",
        "toml_found": "Found model.toml",
        "toml_missing": "model.toml not found — run llm_setup.py or create pysrc/model.toml",
        "toml_parse_error": "model.toml parse error: {err}",
        "llm_section": "LLM Configuration",
        "llm_no_provider": "No LLM provider configured — at least one API key is required",
        "active_provider": "Active provider: {name}",
        "provider_no_key": "{name}: API key is empty — frontend will prompt for setup",
        "provider_ok": "{name}: model={model}",
        "db_section": "Database Configuration",
        "postgres_ok": "PostgreSQL: localhost:{port}",
        "redis_ok": "Redis: localhost:{port}",
        "neo4j_ok": "Neo4j: {uri}",
        "ports_section": "Port Check",
        "port_free": "Port {port} available",
        "port_in_use": "Port {port} in use ({proc}) — launcher will auto-switch",
        "docker_section": "Docker Status",
        "docker_ok": "Docker engine ready",
        "docker_not_ready": "Docker not running — launcher will auto-start Docker Desktop",
        "containers_section": "Container Status",
        "container_running": "{name}: running",
        "container_stopped": "{name}: stopped (launcher will start)",
        "container_missing": "{name}: not created (launcher will create)",
        "all_ok": "All checks passed. Ready to launch.",
        "warnings_found": "{count} warning(s) found (non-blocking).",
        "errors_found": "{count} error(s) found — must fix before launch.",
        "critical": "CRITICAL",
        "llm_setup_tip": "Run python scripts/llm_setup.py to configure AI providers",
        "docker_tip": "Start Docker Desktop or use option 4 to install",
        "hint": "HINT",
    },
}


def t(key: str, lang: str, **fmt) -> str:
    text = MESSAGES.get(lang, MESSAGES["en"]).get(key, key)
    if fmt:
        return text.format(**fmt)
    return text


def load_toml(path: Path) -> dict | None:
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except Exception:
        return None


def parse_toml_quiet(path: Path) -> dict:
    try:
        return load_toml(path) or {}
    except Exception:
        return {}


def check_port(port: int) -> tuple[bool, str]:
    """Return (is_free, process_info)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", port))
            return True, ""
    except OSError:
        if sys.platform == "win32":
            try:
                import subprocess
                result = subprocess.run(
                    ["netstat", "-ano", "-p", "tcp"],
                    capture_output=True, text=True, timeout=5,
                )
                for line in result.stdout.splitlines():
                    if f":{port}" in line and "LISTENING" in line:
                        parts = line.strip().split()
                        pid = parts[-1] if parts else "?"
                        try:
                            proc = subprocess.run(
                                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                                capture_output=True, text=True, timeout=3,
                            )
                            name = proc.stdout.strip().split(",")[0].replace('"', "") if proc.stdout else str(pid)
                        except Exception:
                            name = str(pid)
                        return False, f"PID {pid} ({name})"
            except Exception:
                pass
        return False, "unknown"


def check_docker() -> bool:
    """Check if Docker is accessible."""
    import subprocess
    for cmd in (["docker", "info"], ["docker", "ps"], ["docker", "--context", "desktop-linux", "info"]):
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=8)
            if result.returncode == 0:
                return True
        except Exception:
            continue
    return False


def get_container_status() -> dict[str, str]:
    """Return {container_name: status} for megatron containers."""
    import subprocess
    status = {}
    containers = ["megatron_postgres", "megatron_redis", "megatron_neo4j"]
    for name in containers:
        try:
            result = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.Status}}", name],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                status[name] = result.stdout.strip()
            else:
                status[name] = "not_found"
        except Exception:
            status[name] = "unknown"
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate openMegatron configuration")
    parser.add_argument("--quick", action="store_true", help="Skip port and Docker checks")
    parser.add_argument("--lang", choices=["zh", "en"], help="Output language")
    args = parser.parse_args()

    # Language detection
    lang = args.lang or os.environ.get("MEGATRON_LANG", "")
    if lang not in ("zh", "en"):
        lang = "en"

    errors: list[str] = []
    warnings: list[str] = []

    print()
    print(t("divider", lang))
    print(f"  {t('title', lang)}")
    print(t("divider", lang))

    # ── 1. model.toml ──────────────────────────────────
    print(f"\n  [{t('info', lang)}] {t('toml_found', lang)}")
    toml_path = PROJECT_ROOT / "pysrc" / "model.toml"
    if not toml_path.exists():
        toml_path = PROJECT_ROOT / "model.toml"
    if not toml_path.exists():
        errors.append(t("toml_missing", lang))
        print(f"    {t('fail', lang)} {t('toml_missing', lang)}")
    else:
        config = load_toml(toml_path)
        if config is None:
            errors.append(t("toml_parse_error", lang, err="invalid TOML"))
            print(f"    {t('fail', lang)} {t('toml_parse_error', lang, err='invalid TOML')}")
        else:
            print(f"    {t('ok', lang)} {toml_path}")

            # ── 2. LLM config ──────────────────────────
            print(f"\n  [{t('info', lang)}] {t('llm_section', lang)}")
            llm = config.get("llm", {})
            active = llm.get("active_provider", "") if isinstance(llm, dict) else ""
            providers_found = 0
            if isinstance(llm, dict):
                for provider_name, section_value in llm.items():
                    if provider_name in ("active_provider", "extra_params"):
                        continue
                    if not isinstance(section_value, dict):
                        continue
                    providers_found += 1
                    api_key = section_value.get("api_key", "")
                    model = section_value.get("model", "?")
                    if not api_key:
                        warnings.append(t("provider_no_key", lang, name=provider_name))
                        print(f"    {t('warn', lang)} {t('provider_no_key', lang, name=provider_name)}")
                    else:
                        print(f"    {t('ok', lang)} {t('provider_ok', lang, name=provider_name, model=model)}")

            if providers_found == 0:
                errors.append(t("llm_no_provider", lang))
                print(f"    {t('fail', lang)} {t('llm_no_provider', lang)}")
            elif active:
                print(f"    {t('info', lang)} {t('active_provider', lang, name=active)}")

            # ── 3. Database config ──────────────────────
            print(f"\n  [{t('info', lang)}] {t('db_section', lang)}")
            for key in ("postgresql", "postgres", "pgvector"):
                db_cfg = config.get(key, {})
                if db_cfg:
                    print(f"    {t('ok', lang)} {t('postgres_ok', lang, port=db_cfg.get('port', '?'))}")
                    break
            redis_cfg = config.get("redis", {})
            if redis_cfg:
                print(f"    {t('ok', lang)} {t('redis_ok', lang, port=redis_cfg.get('port', '?'))}")
            neo4j_cfg = config.get("neo4j", {})
            if neo4j_cfg:
                print(f"    {t('ok', lang)} {t('neo4j_ok', lang, uri=neo4j_cfg.get('uri', '?'))}")

    # ── 4. Port checks ─────────────────────────────────
    if not args.quick:
        print(f"\n  [{t('info', lang)}] {t('ports_section', lang)}")
        config = parse_toml_quiet(toml_path) if toml_path.exists() else {}
        pg_cfg = config.get("postgres") or config.get("postgresql") or config.get("pgvector") or {}
        check_ports = [
            ("PostgreSQL", pg_cfg.get("port", 54320)),
            ("Redis", (config.get("redis") or {}).get("port", 6379)),
            ("Neo4j HTTP", (config.get("neo4j") or {}).get("http_port", 7474)),
            ("Neo4j Bolt", (config.get("neo4j") or {}).get("bolt_port", 7687)),
            ("Backend API", 8000),
            ("Frontend", 3000),
        ]
        ports_ok = True
        for label, port in check_ports:
            free, proc = check_port(int(port))
            if free:
                print(f"    {t('ok', lang)} {t('port_free', lang, port=port)} ({label})")
            else:
                ports_ok = False
                warnings.append(t("port_in_use", lang, port=port, proc=proc))
                print(f"    {t('warn', lang)} {t('port_in_use', lang, port=port, proc=proc)} ({label})")

        # ── 5. Docker ──────────────────────────────────
        print(f"\n  [{t('info', lang)}] {t('docker_section', lang)}")
        docker_ok = check_docker()
        if docker_ok:
            print(f"    {t('ok', lang)} {t('docker_ok', lang)}")
        else:
            warnings.append(t("docker_not_ready", lang))
            print(f"    {t('warn', lang)} {t('docker_not_ready', lang)}")

        # ── 6. Containers ──────────────────────────────
        if docker_ok:
            print(f"\n  [{t('info', lang)}] {t('containers_section', lang)}")
            for name, status in get_container_status().items():
                if status == "running":
                    print(f"    {t('ok', lang)} {t('container_running', lang, name=name)}")
                elif status == "not_found":
                    print(f"    {t('warn', lang)} {t('container_missing', lang, name=name)}")
                elif status == "exited":
                    print(f"    {t('warn', lang)} {t('container_stopped', lang, name=name)}")

    # ── Summary ────────────────────────────────────────
    print(f"\n{t('divider', lang)}")
    if not errors and not warnings:
        print(f"  {t('ok', lang)} {t('all_ok', lang)}")
    else:
        if warnings:
            print(f"  {t('warn', lang)} {t('warnings_found', lang, count=len(warnings))}")
        if errors:
            print(f"  {t('fail', lang)} {t('errors_found', lang, count=len(errors))}")

    # Hints for fixable issues
    hints = set()
    if any("API key" in e or "provider" in e.lower() for e in errors + warnings):
        hints.add(t("llm_setup_tip", lang))
    if any("Docker" in e for e in errors + warnings):
        hints.add(t("docker_tip", lang))
    for hint in sorted(hints):
        print(f"  {t('info', lang)} {t('hint', lang)}: {hint}")

    print(t("divider", lang))
    print()

    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
