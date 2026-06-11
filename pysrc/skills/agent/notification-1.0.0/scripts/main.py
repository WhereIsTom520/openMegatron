#!/usr/bin/env python3
"""notification v1.0.0 — system toasts, alerts, reminders, sounds."""
import json, sys, os, time, uuid, subprocess, platform, threading
from pathlib import Path

OS = platform.system()
REMINDERS_FILE = Path(__file__).resolve().parent.parent.parent.parent.parent / ".runtime" / "reminders.json"
_reminders: list[dict] = []
_reminder_thread: threading.Thread | None = None

def _load_reminders():
    global _reminders
    if REMINDERS_FILE.exists():
        try:
            _reminders = json.loads(REMINDERS_FILE.read_text())
        except Exception:
            _reminders = []

def _save_reminders():
    REMINDERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    REMINDERS_FILE.write_text(json.dumps(_reminders, ensure_ascii=False, default=str))

def _check_reminders_loop():
    """Background thread that checks for due reminders."""
    while True:
        time.sleep(30)
        now = time.time()
        due = []
        for r in _reminders:
            try:
                remind_at = time.mktime(time.strptime(r["remind_at"], "%Y-%m-%dT%H:%M:%S"))
                if remind_at <= now and not r.get("fired"):
                    due.append(r)
                    r["fired"] = True
            except Exception:
                pass
        for r in due:
            _fire_reminder(r)
        # Cleanup fired reminders older than 1 hour
        _reminders[:] = [r for r in _reminders if not r.get("fired") or time.time() - r.get("fired_at", 0) < 3600]
        if due:
            _save_reminders()

def _fire_reminder(r):
    msg = r.get("remind_message", "Reminder!")
    if OS == "Windows":
        try:
            subprocess.run(["msg", "*", msg], capture_output=True, timeout=5)
        except Exception:
            pass
    elif OS == "Linux":
        subprocess.run(["notify-send", "openMegatron Reminder", msg], capture_output=True, timeout=5)
    r["fired_at"] = time.time()

def _start_reminder_thread():
    global _reminder_thread
    if _reminder_thread is None or not _reminder_thread.is_alive():
        _reminder_thread = threading.Thread(target=_check_reminders_loop, daemon=True)
        _reminder_thread.start()


def main():
    _load_reminders()
    _start_reminder_thread()

    if len(sys.argv) < 2:
        print(json.dumps({"error": "Missing action JSON."})); sys.exit(1)
    try:
        params = json.loads(sys.argv[1])
    except json.JSONDecodeError:
        print(json.dumps({"error": "Invalid JSON."})); sys.exit(1)

    action = params.get("action", "")
    result = {"status": "success", "action": action}

    try:
        if action == "toast":
            title = params.get("title", "openMegatron")
            msg = params.get("message", "")
            duration = int(params.get("duration_sec", 5))
            if OS == "Windows":
                subprocess.run(["powershell", "-Command",
                    f"[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime] | Out-Null;"
                    f"$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(0);"
                    f"$template.GetElementsByTagName('text')[0].AppendChild($template.CreateTextNode('{msg}')) | Out-Null;"
                    f"$toast = New-Object Windows.UI.Notifications.ToastNotification($template);"
                    f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('openMegatron').Show($toast)"],
                    capture_output=True, timeout=5)
            elif OS == "Linux":
                subprocess.run(["notify-send", title, msg, "-t", str(duration * 1000)],
                              capture_output=True, timeout=5)
            elif OS == "Darwin":
                subprocess.run(["osascript", "-e", f'display notification "{msg}" with title "{title}"'],
                              capture_output=True, timeout=5)
            result["toasted"] = msg[:80]

        elif action == "alert_box":
            title = params.get("title", "Agent Alert")
            msg = params.get("message", "")
            if OS == "Windows":
                subprocess.run(["msg", "*", f"{title}: {msg}"], capture_output=True, timeout=5)
            elif OS == "Linux":
                subprocess.run(["zenity", "--info", "--title", title, "--text", msg],
                              capture_output=True, timeout=5)
            result["alerted"] = msg[:80]

        elif action == "reminder":
            remind_at = params.get("remind_at", "")
            msg = params.get("remind_message", "")
            if not remind_at:
                result = {"status": "error", "error": "Missing remind_at (ISO timestamp)"}
            else:
                rid = f"rem_{uuid.uuid4().hex[:8]}"
                _reminders.append({
                    "id": rid, "remind_at": remind_at, "remind_message": msg,
                    "created_at": time.time(), "fired": False,
                })
                _save_reminders()
                result["reminder_id"] = rid
                result["remind_at"] = remind_at

        elif action == "reminder_list":
            result["reminders"] = [
                {"id": r["id"], "remind_at": r["remind_at"], "message": r["remind_message"],
                 "fired": r.get("fired", False)}
                for r in _reminders
            ]
            result["count"] = len(_reminders)

        elif action == "reminder_cancel":
            rid = params.get("reminder_id", "")
            before = len(_reminders)
            _reminders[:] = [r for r in _reminders if r["id"] != rid]
            _save_reminders()
            result["cancelled"] = before > len(_reminders)

        elif action == "sound":
            stype = params.get("sound_type", "beep")
            if OS == "Windows":
                import winsound
                if stype == "beep":
                    winsound.Beep(1000, 300)
                elif stype == "chime":
                    winsound.Beep(800, 200); winsound.Beep(1200, 300)
                elif stype == "alert":
                    winsound.Beep(600, 200); winsound.Beep(400, 200); winsound.Beep(600, 200)
            else:
                print("\a")  # Terminal bell
            result["sound"] = stype

        else:
            result = {"status": "error", "error": f"Unknown action: {action}"}

        print(json.dumps(result, ensure_ascii=False, default=str))

    except Exception as exc:
        print(json.dumps({"status": "error", "action": action,
                          "error": f"{exc.__class__.__name__}: {exc}"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
