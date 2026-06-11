import argparse
import os
import re
import subprocess
import socket
import time
import urllib.request
from pathlib import Path

import tomli_w

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib


SERVICES = {
    "postgres": {"container": "megatron_postgres", "container_port": 5432, "default_host_port": 54320},
    "redis": {"container": "megatron_redis", "container_port": 6379, "default_host_port": 6379},
    "neo4j_http": {"container": "megatron_neo4j", "container_port": 7474, "default_host_port": 7474},
    "neo4j_bolt": {"container": "megatron_neo4j", "container_port": 7687, "default_host_port": 7687},
}


def run(cmd: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)


def run_probe(cmd: list[str], timeout: int = 8) -> subprocess.CompletedProcess | None:
    try:
        return run(cmd, timeout=timeout)
    except subprocess.TimeoutExpired:
        print(f"[WARN] Docker probe timed out after {timeout}s: {' '.join(cmd)}")
        return None
    except OSError as exc:
        print(f"[WARN] Docker probe failed: {' '.join(cmd)} ({exc})")
        return None


def is_bindable(port: int) -> bool:
    for host in ("127.0.0.1", "0.0.0.0"):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind((host, int(port)))
            except OSError:
                return False
    return True


def find_bindable_port(preferred: int, reserved: set[int] | None = None) -> int:
    reserved = reserved or set()
    port = int(preferred)
    while port < 65535:
        if port not in reserved and is_bindable(port):
            return port
        port += 1
    raise RuntimeError(f"No bindable port found from {preferred}")


def tcp_open(port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def try_docker_base_cmd() -> list[str] | None:
    candidates = [["docker"], ["docker", "--context", "desktop-linux"]]
    probe_timeout = int(os.environ.get("MEGATRON_DOCKER_PROBE_TIMEOUT", "8"))
    for candidate in candidates:
        for probe in (["version"], ["ps"], ["info"]):
            result = run_probe(candidate + probe, timeout=probe_timeout)
            if result is None:
                continue
            if result.returncode == 0:
                if len(candidate) > 1:
                    os.environ["DOCKER_CONTEXT"] = candidate[-1]
                return candidate
    return None


def wake_docker_desktop() -> None:
    if os.name != "nt" or os.environ.get("MEGATRON_SKIP_DOCKER_WAKE") == "1":
        return

    candidates = [
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Docker" / "Docker" / "Docker Desktop.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Docker" / "Docker Desktop.exe",
    ]
    for exe in candidates:
        if exe.exists():
            print("[INFO] Waking Docker Desktop...")
            subprocess.Popen([str(exe)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return


def docker_base_cmd() -> list[str]:
    docker_cmd = try_docker_base_cmd()
    if docker_cmd:
        return docker_cmd

    wake_docker_desktop()
    wait_max = int(os.environ.get("MEGATRON_DOCKER_WAIT_MAX", "80"))
    for index in range(1, wait_max + 1):
        docker_cmd = try_docker_base_cmd()
        if docker_cmd:
            print("[OK] Docker engine is ready.")
            return docker_cmd
        print(f"[INFO] Docker engine not ready yet: {index}/{wait_max}. Docker Desktop may need 1-3 minutes.")
        time.sleep(3)

    raise RuntimeError(
        "Docker engine is unavailable. Open Docker Desktop, wait until it is running, "
        "and make sure your Windows user can access Docker."
    )


def compose_cmd(docker_cmd: list[str]) -> list[str]:
    if run(docker_cmd + ["compose", "version"], timeout=20).returncode == 0:
        return docker_cmd + ["compose"]
    if run(["docker-compose", "version"], timeout=20).returncode == 0:
        return ["docker-compose"]
    raise RuntimeError("Docker Compose is unavailable. Install Docker Desktop with Compose support.")


def docker_port(docker_cmd: list[str], container: str, container_port: int) -> int | None:
    result = run(docker_cmd + ["port", container, str(container_port)], timeout=10)
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        value = line.strip()
        if not value or ":" not in value:
            continue
        tail = value.rsplit(":", 1)[-1]
        if tail.isdigit():
            return int(tail)
    return None


def container_exists(docker_cmd: list[str], container: str) -> bool:
    return run(docker_cmd + ["inspect", container], timeout=10).returncode == 0


def remove_container(docker_cmd: list[str], container: str) -> None:
    run(docker_cmd + ["rm", "-f", container], timeout=30)


def load_toml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("rb") as file:
        return tomllib.load(file)


def choose_service_ports(docker_cmd: list[str], data: dict, blocked_ports: set[int] | None = None) -> dict[str, int]:
    postgres_cfg = data.get("postgres") or data.get("postgresql") or data.get("pgvector") or {}
    redis_cfg = data.get("redis") or {}
    neo4j_cfg = data.get("neo4j") or {}
    preferred = {
        "postgres": int(os.environ.get("MEGATRON_POSTGRES_PORT") or postgres_cfg.get("port") or SERVICES["postgres"]["default_host_port"]),
        "redis": int(os.environ.get("MEGATRON_REDIS_PORT") or redis_cfg.get("port") or SERVICES["redis"]["default_host_port"]),
        "neo4j_http": int(os.environ.get("MEGATRON_NEO4J_HTTP_PORT") or neo4j_cfg.get("http_port") or SERVICES["neo4j_http"]["default_host_port"]),
        "neo4j_bolt": int(os.environ.get("MEGATRON_NEO4J_BOLT_PORT") or neo4j_cfg.get("bolt_port") or SERVICES["neo4j_bolt"]["default_host_port"]),
    }

    ports: dict[str, int] = {}
    recreate: set[str] = set()
    reserved_ports: set[int] = set(blocked_ports or set())
    for name, spec in SERVICES.items():
        mapped = docker_port(docker_cmd, spec["container"], spec["container_port"])
        if mapped:
            ports[name] = mapped
            reserved_ports.add(mapped)
            print(f"[OK] Reusing {name} port {mapped} from {spec['container']}.")
            continue
        ports[name] = find_bindable_port(preferred[name], reserved_ports)
        reserved_ports.add(ports[name])
        if ports[name] != preferred[name]:
            print(f"[WARN] {name} port {preferred[name]} is unavailable; using {ports[name]}.")
        else:
            print(f"[OK] {name} port {ports[name]} is bindable.")
        if container_exists(docker_cmd, spec["container"]):
            recreate.add(spec["container"])

    for container in sorted(recreate):
        print(f"[WARN] Recreating {container} because required host port mappings were missing.")
        remove_container(docker_cmd, container)

    return ports


def allocated_ports_from_compose_error(text: str) -> set[int]:
    ports: set[int] = set()
    for match in re.finditer(r"(?:0\.0\.0\.0|127\.0\.0\.1|\[::\]):(\d+)", text or ""):
        ports.add(int(match.group(1)))
    for match in re.finditer(r"port\s+(\d+)\s+is already allocated", text or "", re.IGNORECASE):
        ports.add(int(match.group(1)))
    return ports


def compose_up_with_retry(compose: list[str], docker_cmd: list[str], toml_path: Path, data: dict, runtime_dir: Path, ports: dict[str, int]) -> dict[str, int]:
    blocked_ports: set[int] = set()
    for attempt in range(1, 3):
        print("[INFO] Starting local database containers...")
        result = run(compose + ["-f", "docker-compose.yml", "up", "-d"], timeout=180)
        if result.stdout.strip():
            print(result.stdout.strip())
        if result.stderr.strip():
            print(result.stderr.strip())
        if result.returncode == 0:
            return ports
        combined = f"{result.stdout}\n{result.stderr}"
        newly_blocked = allocated_ports_from_compose_error(combined)
        if attempt >= 2 or not newly_blocked:
            raise RuntimeError("Docker Compose failed to start local services.")
        blocked_ports.update(newly_blocked)
        print(f"[WARN] Docker reported allocated port(s) {sorted(newly_blocked)}. Re-selecting service ports and retrying...")
        for spec in SERVICES.values():
            if container_exists(docker_cmd, spec["container"]):
                remove_container(docker_cmd, spec["container"])
        ports = choose_service_ports(docker_cmd, data, blocked_ports=blocked_ports)
        update_model_toml(toml_path, data, ports)
        write_compose(Path("docker-compose.yml"), ports)
    raise RuntimeError("Docker Compose failed to start local services.")


def update_model_toml(path: Path, data: dict, ports: dict[str, int]) -> None:
    data.setdefault("redis", {})
    data["redis"]["host"] = "localhost"
    data["redis"]["port"] = ports["redis"]
    data["redis"]["password"] = "root"
    data["redis"].setdefault("blpop_timeout", 5)
    data["redis"].setdefault("socket_connect_timeout", 3)
    data["redis"].setdefault("socket_timeout", 10)
    data["redis"].setdefault("health_check_interval", 30)

    for key in ("postgresql", "postgres", "pgvector"):
        data.setdefault(key, {})
        data[key]["host"] = "localhost"
        data[key]["port"] = ports["postgres"]
        data[key]["user"] = "root"
        data[key]["password"] = "root"
        data[key]["database"] = "root"

    data.setdefault("neo4j", {})
    data["neo4j"]["uri"] = f"bolt://localhost:{ports['neo4j_bolt']}"
    data["neo4j"]["user"] = "neo4j"
    data["neo4j"]["password"] = "root"
    data["neo4j"]["http_port"] = ports["neo4j_http"]
    data["neo4j"]["bolt_port"] = ports["neo4j_bolt"]

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as file:
        tomli_w.dump(data, file)


def write_compose(path: Path, ports: dict[str, int]) -> None:
    content = f"""services:
  postgres:
    image: pgvector/pgvector:pg15
    container_name: megatron_postgres
    environment:
      POSTGRES_USER: root
      POSTGRES_PASSWORD: root
      POSTGRES_DB: root
    ports:
      - "{ports['postgres']}:5432"
    restart: always
  redis:
    image: redis:alpine
    container_name: megatron_redis
    command: redis-server --requirepass root
    ports:
      - "{ports['redis']}:6379"
    restart: always
  neo4j:
    image: neo4j:5
    container_name: megatron_neo4j
    environment:
      NEO4J_AUTH: neo4j/root
      NEO4J_dbms_security_auth__minimum__password__length: 4
    ports:
      - "{ports['neo4j_http']}:7474"
      - "{ports['neo4j_bolt']}:7687"
    restart: always
"""
    path.write_text(content, encoding="utf-8")


def wait_for(predicate, label: str, attempts: int, delay: float = 2.0) -> None:
    for index in range(1, attempts + 1):
        if predicate():
            print(f"[OK] {label} is ready.")
            return
        print(f"[INFO] Waiting for {label}: {index}/{attempts}")
        time.sleep(delay)
    raise RuntimeError(f"{label} did not become ready after {attempts} checks.")


def wait_for_services(docker_cmd: list[str], ports: dict[str, int]) -> None:
    db_wait = int(os.environ.get("MEGATRON_DB_WAIT_MAX", "90"))
    neo4j_wait = int(os.environ.get("MEGATRON_NEO4J_WAIT_MAX", "90"))
    redis_wait = int(os.environ.get("MEGATRON_REDIS_WAIT_MAX", "45"))

    wait_for(lambda: run(docker_cmd + ["exec", "megatron_postgres", "pg_isready", "-U", "root"], timeout=10).returncode == 0, "PostgreSQL", db_wait)
    wait_for(lambda: run(docker_cmd + ["exec", "megatron_redis", "redis-cli", "-a", "root", "ping"], timeout=10).returncode == 0, "Redis", redis_wait)

    def neo4j_ready() -> bool:
        if not tcp_open(ports["neo4j_http"], timeout=1.0):
            return False
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{ports['neo4j_http']}", timeout=2).close()
        except Exception:
            return False
        return True

    wait_for(neo4j_ready, "Neo4j", neo4j_wait, delay=3.0)


def write_runtime_env(path: Path, ports: dict[str, int], backend_port: int | None) -> None:
    lines = [
        "@echo off",
        'set "PGHOST=localhost"',
        f'set "PG_PORT={ports["postgres"]}"',
        f'set "PGPORT={ports["postgres"]}"',
        'set "PGUSER=root"',
        'set "PGPASSWORD=root"',
        'set "PGDATABASE=root"',
        f'set "REDIS_PORT={ports["redis"]}"',
        f'set "MEGATRON_REDIS_PORT={ports["redis"]}"',
        f'set "NEO4J_HTTP_PORT={ports["neo4j_http"]}"',
        f'set "NEO4J_BOLT_PORT={ports["neo4j_bolt"]}"',
        f'set "MEGATRON_NEO4J_HTTP_PORT={ports["neo4j_http"]}"',
        f'set "MEGATRON_NEO4J_BOLT_PORT={ports["neo4j_bolt"]}"',
    ]
    if backend_port is not None:
        lines.append(f'set "MEGATRON_BACKEND_PORT={backend_port}"')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_ports_json(runtime_dir: Path, ports: dict[str, int], backend_port: int | None) -> None:
    """Write .runtime/ports.json for cross-process service discovery."""
    import json
    payload = {
        "postgres": ports.get("postgres", 54320),
        "redis": ports.get("redis", 6379),
        "neo4j_http": ports.get("neo4j_http", 7474),
        "neo4j_bolt": ports.get("neo4j_bolt", 7687),
    }
    if backend_port is not None:
        payload["backend"] = backend_port
    (runtime_dir / "ports.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--toml", required=True)
    parser.add_argument("--runtime-dir", default=".runtime")
    parser.add_argument("--mode", choices=["API", "CLI", "TEST"], default="CLI")
    args = parser.parse_args()

    toml_path = Path(args.toml)
    runtime_dir = Path(args.runtime_dir)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    data = load_toml(toml_path)

    docker_cmd = docker_base_cmd()
    compose = compose_cmd(docker_cmd)
    ports = choose_service_ports(docker_cmd, data)
    update_model_toml(toml_path, data, ports)
    write_compose(Path("docker-compose.yml"), ports)

    ports = compose_up_with_retry(compose, docker_cmd, toml_path, data, runtime_dir, ports)

    wait_for_services(docker_cmd, ports)

    backend_port = None
    if args.mode == "API":
        preferred = int(os.environ.get("MEGATRON_BACKEND_PORT", "8000"))
        backend_port = find_bindable_port(preferred)
        if backend_port != preferred:
            print(f"[WARN] Backend port {preferred} is unavailable; using {backend_port}.")
        else:
            print(f"[OK] Backend port {backend_port} is bindable.")
        (runtime_dir / "backend_port.txt").write_text(str(backend_port) + "\n", encoding="utf-8")

    write_runtime_env(runtime_dir / "runtime_env.cmd", ports, backend_port)
    write_ports_json(runtime_dir, ports, backend_port)
    print("[OK] Runtime setup complete.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[ERROR] {exc}")
        raise SystemExit(1)
