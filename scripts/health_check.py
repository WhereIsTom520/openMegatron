#!/usr/bin/env python3
"""Pre-startup health checker — diagnose and auto-repair common issues.

Usage:
    python scripts/health_check.py              # Check only
    python scripts/health_check.py --repair     # Auto-repair
    python scripts/health_check.py --json       # JSON output for CI/CD
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

# ── Config ───────────────────────────────────────────────────────────────────

REQUIRED_PORTS = {
    "PostgreSQL": 5432,
    "Redis": 6379,
    "Neo4j Bolt": 7687,
    "Neo4j HTTP": 7474,
}

REQUIRED_PYTHON_PACKAGES = [
    "fastapi", "uvicorn", "pydantic", "redis", "asyncpg", "neo4j",
    "numpy", "openai", "sentence_transformers", "scikit_learn",
]

REQUIRED_NODE_PACKAGES = ["react", "vite", "typescript"]


def can_connect(host: str, port: int, timeout: float = 2.0) -> dict:
    """Check if a TCP port is reachable."""
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.close()
        return {"status": "ok", "host": host, "port": port}
    except (ConnectionRefusedError, socket.timeout, OSError) as e:
        return {"status": "error", "host": host, "port": port, "error": str(e)}


def check_docker() -> dict:
    """Check if Docker is running and our containers are healthy."""
    result = {"docker_installed": False, "containers": {}}

    # Check Docker availability
    try:
        subprocess.run(["docker", "--version"], capture_output=True, timeout=5, check=True)
        result["docker_installed"] = True
    except Exception:
        return result

    # Check our containers
    try:
        ps = subprocess.run(
            ["docker", "ps", "--filter", "name=megatron_",
             "--format", "{{.Names}}\t{{.Status}}"],
            capture_output=True, text=True, timeout=5,
        )
        for line in ps.stdout.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) >= 2:
                name = parts[0]
                status = parts[1]
                healthy = "up" in status.lower() and "unhealthy" not in status.lower()
                result["containers"][name] = {"status": status, "healthy": healthy}
    except Exception:
        pass

    return result


def check_python_deps(venv_python: str = None) -> dict:
    """Check Python dependencies are installed."""
    python = venv_python or sys.executable
    result = {"ok": True, "missing": [], "installed": []}

    for pkg in REQUIRED_PYTHON_PACKAGES:
        pkg_name = pkg.replace("_", "-")
        try:
            subprocess.run(
                [python, "-c", f"import {pkg}"],
                capture_output=True, timeout=10, check=True,
            )
            result["installed"].append(pkg_name)
        except subprocess.CalledProcessError:
            result["missing"].append(pkg_name)
            result["ok"] = False

    return result


def check_node_deps(project_dir: str = None) -> dict:
    """Check Node.js dependencies."""
    cwd = project_dir or os.getcwd()
    result = {"ok": True, "missing": [], "installed": []}

    node_modules = Path(cwd) / "node_modules"
    if not node_modules.exists():
        result["ok"] = False
        result["missing"] = ["node_modules/ directory not found"]
        return result

    for pkg in REQUIRED_NODE_PACKAGES:
        pkg_dir = node_modules / pkg
        if pkg_dir.exists():
            result["installed"].append(pkg)
        else:
            result["missing"].append(pkg)
            result["ok"] = False

    return result


def check_config() -> dict:
    """Check model.toml configuration."""
    config_path = Path("pysrc/model.toml")
    result = {"exists": False, "issues": []}

    if not config_path.exists():
        result["issues"].append("model.toml not found. Copy model.example.toml → model.toml and configure.")
        return result

    result["exists"] = True

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Check API key
        if 'api_key = ""' in content:
            result["issues"].append("API key is empty in model.toml. Set at least one provider's api_key.")

        # Check database ports match docker-compose
        if "port = 54320" in content:
            result["issues"].append("PostgreSQL port is 54320 but docker-compose uses 5432.")
        if "7807" in content:
            result["issues"].append("Neo4j bolt port is 7807 but docker-compose uses 7687.")

    except Exception as e:
        result["issues"].append(f"Cannot read config: {e}")

    return result


def check_disk_space() -> dict:
    """Check available disk space."""
    try:
        import shutil
        usage = shutil.disk_usage(".")
        free_gb = usage.free / (1024 ** 3)
        total_gb = usage.total / (1024 ** 3)
        return {
            "total_gb": round(total_gb, 1),
            "free_gb": round(free_gb, 1),
            "ok": free_gb > 1.0,
            "warning": free_gb < 5.0 and free_gb > 1.0,
        }
    except Exception:
        return {"ok": True, "warning": False}


def run_all_checks(project_dir: str = None) -> dict:
    """Run all health checks."""
    cwd = project_dir or os.getcwd()
    os.chdir(cwd)

    results = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "project_dir": str(Path(cwd).resolve()),
    }

    # Database ports
    ports = {}
    for name, port in REQUIRED_PORTS.items():
        ports[name] = can_connect("localhost", port)
    results["ports"] = ports

    # Docker
    results["docker"] = check_docker()

    # Python deps
    venv_python = None
    for candidate in ["venv/Scripts/python.exe", "venv/bin/python"]:
        if os.path.exists(os.path.join(cwd, candidate)):
            venv_python = os.path.join(cwd, candidate)
            break
    results["python_deps"] = check_python_deps(venv_python)

    # Node deps
    results["node_deps"] = check_node_deps(cwd)

    # Config
    results["config"] = check_config()

    # Disk
    results["disk"] = check_disk_space()

    # Overall
    all_ok = True
    for name, port in ports.items():
        if port["status"] != "ok":
            all_ok = False
    if not results["python_deps"]["ok"]:
        all_ok = False
    if not results["node_deps"]["ok"]:
        all_ok = False
    if results["config"]["issues"]:
        all_ok = False
    results["all_ok"] = all_ok

    return results


def auto_repair(results: dict) -> dict:
    """Attempt to auto-repair common issues."""
    repairs = []

    # Repair: missing model.toml
    if not results["config"]["exists"]:
        example = Path("pysrc/model.example.toml")
        target = Path("pysrc/model.toml")
        if example.exists():
            target.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
            repairs.append({"action": "copy_model_toml", "status": "ok"})

    # Repair: node_modules missing
    if not results["node_deps"]["ok"]:
        try:
            subprocess.run(["npm", "install", "--no-audit", "--no-fund"],
                         capture_output=True, timeout=120, check=True)
            repairs.append({"action": "npm_install", "status": "ok"})
        except Exception as e:
            repairs.append({"action": "npm_install", "status": "error", "error": str(e)})

    # Repair: pip deps missing
    if not results["python_deps"]["ok"]:
        python = sys.executable
        for candidate in ["venv/Scripts/python.exe", "venv/bin/python"]:
            if os.path.exists(candidate):
                python = candidate
                break
        try:
            subprocess.run(
                [python, "-m", "pip", "install", "-r", "pysrc/requirements.txt", "-q"],
                capture_output=True, timeout=300,
            )
            repairs.append({"action": "pip_install", "status": "ok"})
        except Exception as e:
            repairs.append({"action": "pip_install", "status": "error", "error": str(e)})

    # Repair: Docker containers
    docker = results.get("docker", {})
    if docker.get("docker_installed") and not all(
        c.get("healthy") for c in docker.get("containers", {}).values()
    ):
        try:
            subprocess.run(["docker-compose", "down"], capture_output=True, timeout=30)
            subprocess.run(["docker-compose", "up", "-d"], capture_output=True, timeout=60)
            repairs.append({"action": "docker_restart", "status": "ok"})
        except Exception as e:
            repairs.append({"action": "docker_restart", "status": "error", "error": str(e)})

    results["repairs"] = repairs
    return results


def format_report(results: dict) -> str:
    """Format results as human-readable text."""
    lines = []
    lines.append("=" * 55)
    lines.append("  OpenMegatron Health Check")
    lines.append("=" * 55)
    lines.append(f"  Overall: {'OK' if results['all_ok'] else 'ISSUES FOUND'}")

    # Ports
    lines.append("\n[Database Ports]")
    for name, port in results.get("ports", {}).items():
        icon = "OK" if port["status"] == "ok" else "FAIL"
        lines.append(f"  {icon:>4}  {name}: {port.get('port')}")

    # Docker
    docker = results.get("docker", {})
    lines.append(f"\n[Docker]")
    lines.append(f"  Installed: {docker.get('docker_installed')}")
    for name, info in docker.get("containers", {}).items():
        lines.append(f"  {name}: {info.get('status', 'unknown')}")

    # Python deps
    py = results.get("python_deps", {})
    lines.append(f"\n[Python Dependencies]")
    if py.get("missing"):
        lines.append(f"  Missing: {', '.join(py['missing'])}")
    else:
        lines.append("  All installed")

    # Node deps
    node = results.get("node_deps", {})
    lines.append(f"\n[Node.js Dependencies]")
    if node.get("missing"):
        lines.append(f"  Missing: {', '.join(node['missing'])}")
    else:
        lines.append("  All installed")

    # Config
    cfg = results.get("config", {})
    lines.append(f"\n[Configuration]")
    if cfg.get("issues"):
        for issue in cfg["issues"]:
            lines.append(f"  ! {issue}")
    else:
        lines.append("  OK")

    # Disk
    disk = results.get("disk", {})
    lines.append(f"\n[Disk Space]")
    lines.append(f"  Free: {disk.get('free_gb', '?')} GB / {disk.get('total_gb', '?')} GB")

    # Repairs
    repairs = results.get("repairs", [])
    if repairs:
        lines.append(f"\n[Auto-Repairs]")
        for r in repairs:
            lines.append(f"  {r['action']}: {r['status']}")

    lines.append("\n" + "=" * 55)
    return "\n".join(lines)


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="OpenMegatron health checker")
    p.add_argument("--repair", action="store_true", help="Auto-repair common issues")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    p.add_argument("--project-dir", default=None, help="Project root directory")
    args = p.parse_args()

    results = run_all_checks(args.project_dir)

    if args.repair:
        results = auto_repair(results)

    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False, default=str))
    else:
        print(format_report(results))

    sys.exit(0 if results["all_ok"] else 1)
