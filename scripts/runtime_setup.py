import argparse
import asyncio
import os
import re
import subprocess
import socket
import sys
import time
import urllib.request
from pathlib import Path

try:
    import tomli_w
except ModuleNotFoundError:
    class _TomliWFallback:
        @staticmethod
        def dumps(data: dict) -> str:
            lines = []

            def write_table(prefix: str, table: dict):
                if prefix:
                    lines.append(f"[{prefix}]")
                for key, value in table.items():
                    if isinstance(value, dict):
                        continue
                    if isinstance(value, str):
                        rendered = '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
                    elif isinstance(value, bool):
                        rendered = "true" if value else "false"
                    else:
                        rendered = str(value)
                    lines.append(f"{key} = {rendered}")
                for key, value in table.items():
                    if isinstance(value, dict):
                        if lines and lines[-1] != "":
                            lines.append("")
                        write_table(f"{prefix}.{key}" if prefix else key, value)

            write_table("", data)
            return "\n".join(lines) + "\n"

    tomli_w = _TomliWFallback()

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib


SERVICES = {
    "postgres": {"container": "megatron_postgres", "container_port": 5432, "default_host_port": 54320, "image": "pgvector/pgvector:pg15"},
    "redis": {"container": "megatron_redis", "container_port": 6379, "default_host_port": 6379, "image": "redis:alpine"},
    "neo4j_http": {"container": "megatron_neo4j", "container_port": 7474, "default_host_port": 7474, "image": "neo4j:5"},
    "neo4j_bolt": {"container": "megatron_neo4j", "container_port": 7687, "default_host_port": 7687, "image": "neo4j:5"},
}

# Docker Hub mirrors for China (in order of preference)
DOCKER_MIRRORS = [
    "docker.m.daocloud.io",
    "hub-mirror.c.163.com",
    "dockerhub.timeweb.cloud",
]


def run(cmd: list[str], timeout: int = 30, show_progress: bool = False) -> subprocess.CompletedProcess:
    if show_progress:
        print(f"       Running: {' '.join(cmd[:5])}...")
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

    # WINDOWS 10013 BUG FIX: Windows randomly reserves MASSIVE port ranges!
    # Use HIGH port numbers (18000+) that are almost NEVER reserved by Windows
    safe_start = 18000
    if os.name == "nt" and port < safe_start:
        print(f"[INFO] Windows detected: using safe port range {safe_start}+")
        print(f"       (Avoids WinError 10013 reserved port BUG)")
        port = safe_start

    while port < 65535:
        if port not in reserved and is_bindable(port):
            if port != preferred and preferred < safe_start:
                print(f"[INFO] Original port {preferred} blocked, using port {port} instead")
            return port
        port += 1
    raise RuntimeError(f"No bindable port found from {preferred}")


def tcp_open(port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def postgres_protocol_ready(port: int) -> bool:
    try:
        import asyncpg
    except Exception:
        return tcp_open(port, timeout=2.0)

    async def _probe() -> bool:
        conn = None
        try:
            conn = await asyncpg.connect(
                host="127.0.0.1",
                port=int(port),
                user="root",
                password="root",
                database="root",
                timeout=5,
            )
            return await conn.fetchval("SELECT 1") == 1
        except Exception as exc:
            return False
        finally:
            if conn is not None:
                await conn.close()

    return bool(asyncio.run(_probe()))


def try_docker_base_cmd() -> list[str] | None:
    candidates = [["docker"], ["docker", "--context", "desktop-linux"]]
    probe_timeout = int(os.environ.get("MEGATRON_DOCKER_PROBE_TIMEOUT", "5"))
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
            print("[INFO] Starting Docker Desktop... (this takes 10-30 seconds)")
            subprocess.Popen([str(exe)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return


def docker_base_cmd() -> list[str]:
    docker_cmd = try_docker_base_cmd()
    if docker_cmd:
        return docker_cmd

    wake_docker_desktop()
    wait_max = int(os.environ.get("MEGATRON_DOCKER_WAIT_MAX", "30"))
    for index in range(1, wait_max + 1):
        docker_cmd = try_docker_base_cmd()
        if docker_cmd:
            print(f"[OK] Docker engine is ready (after {index * 2} seconds).")
            return docker_cmd
        print(f"[INFO] Waiting for Docker: {index * 2}/{wait_max * 2} seconds...")
        time.sleep(2)

    # Auto-fix attempt: common Docker issues
    print()
    print("[WARN] Docker did not respond. Trying automatic fixes...")

    # Fix 1: Try to restart Docker service (Windows)
    if os.name == "nt":
        print("[INFO] Attempting to restart Docker service...")
        try:
            subprocess.run(["sc", "stop", "com.docker.service"], timeout=10, capture_output=True)
            time.sleep(3)
            subprocess.run(["sc", "start", "com.docker.service"], timeout=10, capture_output=True)
            # Wait and try again
            for i in range(10):
                docker_cmd = try_docker_base_cmd()
                if docker_cmd:
                    print("[OK] Docker service restarted successfully!")
                    return docker_cmd
                time.sleep(2)
        except Exception as e:
            print(f"[INFO] Could not restart Docker service (need admin rights): {e}")

    raise RuntimeError(
        "\n[ERROR] Docker engine did not start within timeout.\n"
        "\n"
        "  Please try:\n"
        "  1. Close and reopen Docker Desktop\n"
        "  2. Right-click Docker icon → 'Restart'\n"
        "  3. Wait for the status bar to turn GREEN\n"
        "  4. Run this script again\n"
        "\n"
        "  If Docker won't start at all:\n"
        "  - Reinstall Docker Desktop (https://www.docker.com/products/docker-desktop/)\n"
        "  - Or run with SKIP_DOCKER=1 to use without databases\n"
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


def image_exists_locally(docker_cmd: list[str], image: str) -> bool:
    return run(docker_cmd + ["inspect", "--type=image", image], timeout=10).returncode == 0


def pull_image_with_mirror(docker_cmd: list[str], image: str) -> bool:
    """Pull image with automatic mirror fallback for China users."""
    print(f"       Pulling: {image}")

    # Try direct pull first (fast if already cached or outside China)
    try:
        result = run(docker_cmd + ["pull", image], timeout=120)
        if result.returncode == 0:
            print(f"       ✓ Pulled: {image}")
            return True
    except subprocess.TimeoutExpired:
        print(f"       ! Direct pull timed out, trying mirrors...")

    # Try mirrors if direct pull failed
    for mirror in DOCKER_MIRRORS:
        print(f"       Trying mirror: {mirror}")
        try:
            result = run(docker_cmd + ["pull", f"{mirror}/{image}"], timeout=90)
            if result.returncode == 0:
                # Retag to original image name
                run(docker_cmd + ["tag", f"{mirror}/{image}", image], timeout=10)
                run(docker_cmd + ["rmi", f"{mirror}/{image}"], timeout=10)
                print(f"       ✓ Pulled via {mirror}: {image}")
                return True
        except subprocess.TimeoutExpired:
            continue
        except Exception:
            continue

    print(f"       ✗ Failed to pull {image} after trying all mirrors")
    return False


def prefetch_images(docker_cmd: list[str]) -> None:
    """Prefetch Docker images before starting containers, with progress feedback."""
    images_needed = set()
    for spec in SERVICES.values():
        if not container_exists(docker_cmd, spec["container"]):
            images_needed.add(spec["image"])

    if not images_needed:
        print("[OK] All containers exist, no images to pull.")
        return

    # Filter out already-cached images
    to_pull = [img for img in images_needed if not image_exists_locally(docker_cmd, img)]
    if not to_pull:
        print("[OK] All images already cached locally.")
        return

    print(f"[INFO] Need to pull {len(to_pull)} Docker images...")
    print(f"       This may take 1-5 minutes on first run.")
    print(f"       Images: {', '.join(sorted(to_pull))}")
    print()

    for i, image in enumerate(sorted(to_pull), 1):
        print(f"  [{i}/{len(to_pull)}] Pulling {image}...")
        success = pull_image_with_mirror(docker_cmd, image)
        if not success:
            print(f"  [WARN] Could not pull {image}. Will retry during compose up.")
        print()

    print("[OK] Image prefetch complete.")


def auto_fix_docker(docker_cmd: list[str]) -> None:
    """Automatically detect and fix common Docker issues."""
    print("[INFO] Running Docker health checks...")
    fixes_applied = 0

    # Fix 1: Remove stopped containers with our names
    for name in ["megatron_postgres", "megatron_redis", "megatron_neo4j"]:
        if container_exists(docker_cmd, name):
            # Check if it's running
            result = run(docker_cmd + ["inspect", "--format='{{.State.Running}}'", name], timeout=10)
            if "false" in result.stdout.lower():
                print(f"[INFO] Removing stopped container: {name}")
                remove_container(docker_cmd, name)
                fixes_applied += 1

    # Fix 2: Check for Docker network issues
    result = run(docker_cmd + ["network", "ls"], timeout=10)
    if result.returncode != 0:
        print("[WARN] Docker network error, attempting to reset...")
        run(docker_cmd + ["network", "prune", "-f"], timeout=30)
        fixes_applied += 1

    # Fix 3: Prune dangling images (frees up space and avoids corruption)
    print("[INFO] Cleaning up Docker cache...")
    run(docker_cmd + ["image", "prune", "-f"], timeout=60)

    if fixes_applied > 0:
        print(f"[OK] Applied {fixes_applied} automatic fixes to Docker.")
    else:
        print("[OK] Docker environment looks healthy.")
    print()


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
        print(f"[INFO] Recreating {container} (port mapping changed)...")
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
    start_time = time.time()
    for index in range(1, attempts + 1):
        if predicate():
            elapsed = int(time.time() - start_time)
            print(f"[OK] {label} is ready (after {elapsed} seconds).")
            return
        elapsed = int(time.time() - start_time)
        eta = int(attempts * delay - elapsed)
        print(f"[INFO] Waiting for {label}: {index}/{attempts} (ETA ~{eta}s)...")
        time.sleep(delay)
    raise RuntimeError(f"{label} did not become ready after {int(attempts * delay)} seconds.")


def wait_for_services(docker_cmd: list[str], ports: dict[str, int]) -> None:
    db_wait = int(os.environ.get("MEGATRON_DB_WAIT_MAX", "30"))
    neo4j_wait = int(os.environ.get("MEGATRON_NEO4J_WAIT_MAX", "45"))
    redis_wait = int(os.environ.get("MEGATRON_REDIS_WAIT_MAX", "20"))

    print()
    print("[INFO] Waiting for database services to become healthy...")
    print()

    wait_for(lambda: run(docker_cmd + ["exec", "megatron_postgres", "pg_isready", "-U", "root"], timeout=10).returncode == 0, "PostgreSQL (container)", db_wait)
    wait_for(lambda: tcp_open(ports["postgres"], timeout=2.0), f"PostgreSQL TCP port {ports['postgres']}", 15, delay=2.0)
    try:
        wait_for(lambda: postgres_protocol_ready(ports["postgres"]), "PostgreSQL protocol", 3, delay=2.0)
    except RuntimeError:
        print("[WARN] PostgreSQL TCP port is open but protocol handshake failed; restarting container...")
        run(docker_cmd + ["restart", "megatron_postgres"], timeout=30)
        wait_for(lambda: run(docker_cmd + ["exec", "megatron_postgres", "pg_isready", "-U", "root"], timeout=10).returncode == 0, "PostgreSQL (container)", db_wait)
        wait_for(lambda: tcp_open(ports["postgres"], timeout=2.0), f"PostgreSQL TCP port {ports['postgres']}", 15, delay=2.0)
        wait_for(lambda: postgres_protocol_ready(ports["postgres"]), "PostgreSQL protocol", 10, delay=2.0)
    wait_for(lambda: run(docker_cmd + ["exec", "megatron_redis", "redis-cli", "-a", "root", "ping"], timeout=10).returncode == 0, "Redis", redis_wait)

    def neo4j_ready() -> bool:
        if not tcp_open(ports["neo4j_bolt"], timeout=1.0):
            return False
        return run(
            docker_cmd + [
                "exec", "megatron_neo4j",
                "cypher-shell", "-u", "neo4j", "-p", "root",
                "RETURN 1 AS ok",
            ],
            timeout=15,
        ).returncode == 0

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
    parser.add_argument("--fix-win10013", action="store_true", help="Fix Windows 10013 reserved port bug (requires admin)")
    args = parser.parse_args()

    # Quick Windows 10013 BUG fix
    if args.fix_win10013:
        print("=" * 60)
        print("  Fixing Windows 10013 reserved port BUG...")
        print("  This requires ADMINISTRATOR privileges!")
        print("=" * 60)
        print()
        import subprocess
        try:
            # Set dynamic port range to start from 50000 instead of 1024
            print("[1/3] Setting TCP dynamic port range...")
            subprocess.run(
                ["netsh", "int", "ipv4", "set", "dynamicport", "tcp", "start=50000", "num=15535"],
                check=True, capture_output=True
            )
            print("        OK: TCP dynamic port range now starts at 50000")

            print("[2/3] Excluding our port range from reservation...")
            # Exclude 8000-9000 from dynamic port allocation
            try:
                subprocess.run(
                    ["netsh", "int", "ipv4", "add", "excludedportrange", "tcp", "8000", "1000"],
                    check=True, capture_output=True
                )
                print("        OK: Excluded ports 8000-8999 from Windows reservation")
            except subprocess.CalledProcessError:
                print("        (may already be excluded - ignoring)")

            print("[3/3] Restarting NAT service...")
            subprocess.run(["net", "stop", "winnat"], check=True, capture_output=True)
            subprocess.run(["net", "start", "winnat"], check=True, capture_output=True)

            print()
            print("=" * 60)
            print("[OK] Windows 10013 BUG has been fixed!")
            print("     Ports 8000+ should now work correctly.")
            print("=" * 60)
            return 0
        except subprocess.CalledProcessError as e:
            print(f"[ERROR] Command failed: {e}")
            print()
            print("You need to run this as ADMINISTRATOR!")
            print("  1. Right-click Command Prompt")
            print("  2. Select 'Run as administrator'")
            print("  3. Run this script again")
            return 1

    toml_path = Path(args.toml)
    runtime_dir = Path(args.runtime_dir)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    data = load_toml(toml_path)

    print()
    print("=" * 60)
    print("  OpenMegatron Docker Database Setup")
    print("=" * 60)
    print()

    docker_cmd = docker_base_cmd()
    compose = compose_cmd(docker_cmd)

    # Auto-fix common Docker issues BEFORE starting anything
    auto_fix_docker(docker_cmd)

    print()
    print("[1/4] Checking ports...")
    ports = choose_service_ports(docker_cmd, data)
    update_model_toml(toml_path, data, ports)
    write_compose(Path("docker-compose.yml"), ports)

    print()
    print("[2/4] Prefetching Docker images...")
    prefetch_images(docker_cmd)

    print()
    print("[3/4] Starting containers...")
    ports = compose_up_with_retry(compose, docker_cmd, toml_path, data, runtime_dir, ports)

    print()
    print("[4/4] Waiting for database health...")
    wait_for_services(docker_cmd, ports)

    backend_port = None
    if args.mode == "API":
        preferred = int(os.environ.get("MEGATRON_BACKEND_PORT", "8000"))
        backend_port = find_bindable_port(preferred, reserved=set(ports.values()))
        if backend_port != preferred:
            print(f"[WARN] Backend port {preferred} is unavailable; using {backend_port}.")
        else:
            print(f"[OK] Backend port {backend_port} is bindable.")
        (runtime_dir / "backend_port.txt").write_text(str(backend_port) + "\n", encoding="utf-8")

    write_runtime_env(runtime_dir / "runtime_env.cmd", ports, backend_port)
    write_ports_json(runtime_dir, ports, backend_port)

    print()
    print("=" * 60)
    print("[OK] Docker databases setup complete!")
    print(f"     PostgreSQL: port {ports['postgres']}")
    print(f"     Redis:      port {ports['redis']}")
    print(f"     Neo4j:      ports {ports['neo4j_http']} (HTTP), {ports['neo4j_bolt']} (Bolt)")
    print("=" * 60)
    print()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        print()
        print("Troubleshooting tips:", file=sys.stderr)
        print("  1. Ensure Docker Desktop is running (check status bar)", file=sys.stderr)
        print("  2. Try: docker system prune -f (cleans up old containers)", file=sys.stderr)
        print("  3. Try: docker ps -a (list all containers)", file=sys.stderr)
        print("  4. Check network/VPN/proxy settings", file=sys.stderr)
        print("  5. Run with SKIP_DOCKER=1 to bypass database setup", file=sys.stderr)
        raise SystemExit(1)
