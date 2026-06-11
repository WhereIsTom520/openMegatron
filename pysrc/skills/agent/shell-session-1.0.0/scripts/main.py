#!/usr/bin/env python3
"""shell-session v1.0.0 — persistent shell sessions across agent turns."""
import json, sys, subprocess, os, time, uuid, threading
from pathlib import Path

SESSIONS_DIR = Path(__file__).resolve().parent.parent.parent.parent.parent / ".runtime" / "shell_sessions"
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
_sessions: dict[str, dict] = {}

def _load_sessions():
    for f in SESSIONS_DIR.glob("*.json"):
        try:
            sid = f.stem
            _sessions[sid] = json.loads(f.read_text())
        except Exception:
            pass

def _save_session(sid: str):
    if sid in _sessions:
        (SESSIONS_DIR / f"{sid}.json").write_text(json.dumps(_sessions[sid], ensure_ascii=False, default=str))

def _detect_shell() -> str:
    if os.name == "nt":
        return "cmd"
    return "bash"

def main():
    _load_sessions()
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Missing action JSON."})); sys.exit(1)
    try:
        params = json.loads(sys.argv[1])
    except json.JSONDecodeError:
        print(json.dumps({"error": "Invalid JSON."})); sys.exit(1)

    action = params.get("action", "")
    result = {"status": "success", "action": action}

    try:
        if action == "session_new":
            sid = params.get("session_id") or f"sh_{uuid.uuid4().hex[:8]}"
            shell = params.get("shell", "auto")
            if shell == "auto":
                shell = _detect_shell()
            cwd = params.get("cwd") or str(Path.cwd())
            _sessions[sid] = {
                "id": sid, "shell": shell, "cwd": cwd,
                "env": params.get("env_vars") or {},
                "created_at": time.time(), "last_exit_code": 0,
                "output_buffer": [], "history": [],
            }
            _save_session(sid)
            result.update({"session_id": sid, "shell": shell, "cwd": cwd})

        elif action == "session_run":
            sid = params.get("session_id", "")
            if sid not in _sessions:
                result = {"status": "error", "error": f"Session {sid} not found"}
            else:
                sess = _sessions[sid]
                cmd = params.get("command", "")
                timeout = int(params.get("timeout_sec", 30))
                env = os.environ.copy()
                env.update(sess["env"])
                try:
                    if sess["shell"] == "powershell":
                        proc = subprocess.run(["powershell", "-Command", cmd],
                            capture_output=True, text=True, timeout=timeout,
                            cwd=sess["cwd"], env=env, shell=False)
                    elif sess["shell"] == "cmd":
                        proc = subprocess.run(["cmd", "/c", cmd],
                            capture_output=True, text=True, timeout=timeout,
                            cwd=sess["cwd"], env=env, shell=False)
                    else:
                        proc = subprocess.run(cmd,
                            capture_output=True, text=True, timeout=timeout,
                            cwd=sess["cwd"], env=env, shell=True)
                    sess["last_exit_code"] = proc.returncode
                    sess["output_buffer"].append({
                        "command": cmd, "exit_code": proc.returncode,
                        "stdout": proc.stdout[-2000:], "stderr": proc.stderr[-1000:],
                        "time": time.time(),
                    })
                    sess["history"].append(cmd)
                    sess["output_buffer"] = sess["output_buffer"][-20:]  # keep last 20
                    _save_session(sid)
                    result.update({
                        "exit_code": proc.returncode,
                        "stdout": proc.stdout[-2000:],
                        "stderr": proc.stderr[-500:],
                    })
                except subprocess.TimeoutExpired:
                    result = {"status": "error", "error": f"Command timed out after {timeout}s"}

        elif action == "session_output":
            sid = params.get("session_id", "")
            if sid not in _sessions:
                result = {"status": "error", "error": f"Session {sid} not found"}
            else:
                sess = _sessions[sid]
                result["outputs"] = sess["output_buffer"][-5:]
                result["last_exit_code"] = sess["last_exit_code"]

        elif action == "session_list":
            result["sessions"] = [
                {"id": s["id"], "shell": s["shell"], "cwd": s["cwd"],
                 "created_at": s["created_at"], "history_count": len(s["history"])}
                for s in _sessions.values()
            ]
            result["count"] = len(_sessions)

        elif action == "session_kill":
            sid = params.get("session_id", "")
            if sid in _sessions:
                del _sessions[sid]
                (SESSIONS_DIR / f"{sid}.json").unlink(missing_ok=True)
                result["killed"] = sid
            else:
                result = {"status": "error", "error": f"Session {sid} not found"}

        elif action == "session_env":
            sid = params.get("session_id", "")
            if sid not in _sessions:
                result = {"status": "error", "error": f"Session {sid} not found"}
            else:
                _sessions[sid]["env"].update(params.get("env_vars") or {})
                _save_session(sid)
                result["env"] = _sessions[sid]["env"]

        elif action == "session_cd":
            sid = params.get("session_id", "")
            new_cwd = params.get("cwd", "")
            if sid not in _sessions:
                result = {"status": "error", "error": f"Session {sid} not found"}
            elif not new_cwd:
                result = {"status": "error", "error": "Missing cwd"}
            else:
                _sessions[sid]["cwd"] = new_cwd
                _save_session(sid)
                result["cwd"] = new_cwd

        else:
            result = {"status": "error", "error": f"Unknown action: {action}"}

        print(json.dumps(result, ensure_ascii=False, default=str))

    except Exception as exc:
        print(json.dumps({"status": "error", "action": action,
                          "error": f"{exc.__class__.__name__}: {exc}"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
