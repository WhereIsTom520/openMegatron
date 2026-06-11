#!/usr/bin/env python3
"""file-watcher v1.0.0 — watch files/dirs and trigger actions."""
import json, sys, os, time, subprocess, uuid, threading, fnmatch
from pathlib import Path

STATE_FILE = Path(__file__).resolve().parent.parent.parent.parent.parent / ".runtime" / "watchers.json"
WATCHERS: dict[str, dict] = {}

def _load_watchers():
    global WATCHERS
    if STATE_FILE.exists():
        try:
            WATCHERS = json.loads(STATE_FILE.read_text())
        except Exception:
            WATCHERS = {}

def _save_watchers():
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(WATCHERS, ensure_ascii=False, default=str))

def _watch_loop(wid: str, path: str, patterns: list, on_change: str, debounce_ms: int):
    """Background thread that polls a directory for changes."""
    p = Path(path).resolve()
    if not p.exists():
        WATCHERS[wid]["error"] = f"Path not found: {path}"
        return
    known = {}
    # Initial scan
    for f in (p.rglob("*") if p.is_dir() else [p]):
        if f.is_file() and (not patterns or any(fnmatch.fnmatch(f.name, pat) for pat in patterns)):
            known[str(f)] = f.stat().st_mtime
    WATCHERS[wid]["known_files"] = len(known)
    last_fire = 0

    while WATCHERS[wid].get("active"):
        time.sleep(1)
        try:
            current = {}
            for f in (p.rglob("*") if p.is_dir() else [p]):
                if f.is_file() and (not patterns or any(fnmatch.fnmatch(f.name, pat) for pat in patterns)):
                    current[str(f)] = f.stat().st_mtime
            # Detect changes
            for fpath, mtime in current.items():
                if fpath not in known:
                    rel = Path(fpath).relative_to(p.parent)
                    WATCHERS[wid]["events"].append({"type": "created", "path": str(rel), "time": time.time()})
                elif mtime != known[fpath]:
                    rel = Path(fpath).relative_to(p.parent)
                    WATCHERS[wid]["events"].append({"type": "modified", "path": str(rel), "time": time.time()})
            for fpath in known:
                if fpath not in current:
                    rel = Path(fpath).relative_to(p.parent)
                    WATCHERS[wid]["events"].append({"type": "deleted", "path": str(rel), "time": time.time()})
            # Keep last 50 events
            WATCHERS[wid]["events"] = WATCHERS[wid]["events"][-50:]
            known = current
            # Fire callback (debounced)
            if on_change and WATCHERS[wid]["events"]:
                now = time.time()
                if now - last_fire > debounce_ms / 1000.0:
                    last_fire = now
                    latest = WATCHERS[wid]["events"][-1]
                    cmd = on_change.replace("{file}", latest["path"])
                    try:
                        subprocess.run(cmd, shell=True, capture_output=True, timeout=30)
                    except Exception:
                        pass
        except Exception:
            pass


def main():
    _load_watchers()
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Missing action JSON."})); sys.exit(1)
    try:
        params = json.loads(sys.argv[1])
    except json.JSONDecodeError:
        print(json.dumps({"error": "Invalid JSON."})); sys.exit(1)

    action = params.get("action", "")
    result = {"status": "success", "action": action}

    try:
        if action == "watch_start":
            path = params.get("path", "")
            if not path:
                result = {"status": "error", "error": "Missing path"}
            else:
                wid = f"watch_{uuid.uuid4().hex[:8]}"
                patterns = params.get("patterns") or []
                # Scan initial state immediately
                p = Path(path).resolve()
                known = {}
                if p.exists():
                    for f in (p.rglob("*") if p.is_dir() else [p]):
                        if f.is_file() and (not patterns or any(fnmatch.fnmatch(f.name, pat) for pat in patterns)):
                            known[str(f)] = f.stat().st_mtime
                WATCHERS[wid] = {"id": wid, "path": path, "patterns": patterns,
                    "active": True, "events": [], "known_files": len(known),
                    "known_snapshot": known, "started_at": time.time()}
                _save_watchers()
                # Spawn watcher as a detached subprocess
                watcher_script = __file__
                subprocess.Popen([sys.executable, watcher_script, json.dumps({
                    "action": "_watch_daemon", "watch_id": wid, "path": path,
                    "patterns": patterns, "on_change": params.get("on_change", ""),
                    "debounce_ms": int(params.get("debounce_ms", 500)),
                })], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   cwd=Path.cwd(), env=os.environ.copy())
                result["watch_id"] = wid
                result["watching"] = path
                result["known_files"] = len(known)

        elif action == "watch_stop":
            wid = params.get("watch_id", "")
            if wid in WATCHERS:
                WATCHERS[wid]["active"] = False
                _save_watchers()
                result["stopped"] = wid
                result["events_captured"] = len(WATCHERS[wid].get("events", []))
            else:
                result = {"status": "error", "error": f"Watcher {wid} not found"}

        elif action == "watch_list":
            result["watchers"] = [
                {"id": w["id"], "path": w.get("path",""), "patterns": w.get("patterns",[]),
                 "active": w.get("active", False), "events_count": len(w.get("events", []))}
                for w in WATCHERS.values()
            ]
            result["count"] = len(WATCHERS)

        elif action == "watch_status":
            wid = params.get("watch_id", "")
            if wid in WATCHERS:
                w = WATCHERS[wid]
                result["watch_id"] = wid
                result["active"] = w.get("active")
                result["known_files"] = w.get("known_files", 0)
                result["recent_events"] = (w.get("events") or [])[-20:]
            else:
                result = {"status": "error", "error": f"Watcher {wid} not found"}

        # Internal daemon mode (called via subprocess)
        elif action == "_watch_daemon":
            wid = params.get("watch_id")
            path = params.get("path")
            patterns = params.get("patterns") or []
            on_change = params.get("on_change", "")
            debounce_ms = int(params.get("debounce_ms", 500))
            _watch_loop(wid, path, patterns, on_change, debounce_ms)
            return  # Don't print anything

        else:
            result = {"status": "error", "error": f"Unknown action: {action}"}

        print(json.dumps(result, ensure_ascii=False, default=str))

    except Exception as exc:
        print(json.dumps({"status": "error", "action": action,
                          "error": f"{exc.__class__.__name__}: {exc}"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
