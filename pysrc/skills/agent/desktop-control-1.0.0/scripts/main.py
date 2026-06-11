#!/usr/bin/env python3
"""desktop-control v1.0.0 — mouse, keyboard, screen, clipboard, windows."""
import json, sys, base64, subprocess, platform
from pathlib import Path

OS = platform.system()

def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Missing action JSON."})); sys.exit(1)
    try:
        params = json.loads(sys.argv[1])
    except json.JSONDecodeError:
        print(json.dumps({"error": "Invalid JSON."})); sys.exit(1)

    action = params.get("action", "")
    result = {"status": "success", "action": action}

    try:
        if action == "mouse_move":
            try:
                import pyautogui
                pyautogui.moveTo(int(params.get("x", 0)), int(params.get("y", 0)))
                result["position"] = {"x": params.get("x"), "y": params.get("y")}
            except ImportError:
                result = {"status": "error", "error": "pyautogui not installed"}

        elif action == "mouse_click":
            try:
                import pyautogui
                btn = params.get("button", "left")
                pyautogui.click(int(params.get("x", 0)), int(params.get("y", 0)), button=btn)
                result["clicked"] = {"x": params.get("x"), "y": params.get("y"), "button": btn}
            except ImportError:
                result = {"status": "error", "error": "pyautogui not installed"}

        elif action == "mouse_drag":
            try:
                import pyautogui
                pyautogui.moveTo(int(params.get("x1", 0)), int(params.get("y1", 0)))
                pyautogui.drag(int(params.get("x2", 0)) - int(params.get("x1", 0)),
                              int(params.get("y2", 0)) - int(params.get("y1", 0)), duration=0.5)
                result["dragged"] = "ok"
            except ImportError:
                result = {"status": "error", "error": "pyautogui not installed"}

        elif action == "keyboard_type":
            try:
                import pyautogui
                text = params.get("text", "")
                pyautogui.typewrite(text, interval=0.05)
                result["typed"] = text[:50]
            except ImportError:
                result = {"status": "error", "error": "pyautogui not installed"}

        elif action == "keyboard_hotkey":
            try:
                import pyautogui
                keys = params.get("keys", "").split("+")
                pyautogui.hotkey(*[k.strip() for k in keys])
                result["hotkey"] = params.get("keys")
            except ImportError:
                result = {"status": "error", "error": "pyautogui not installed"}

        elif action == "screen_capture":
            try:
                import pyautogui
                img = pyautogui.screenshot()
                out = params.get("path", "")
                if out:
                    img.save(out)
                    result["path"] = out
                else:
                    import io; buf = io.BytesIO(); img.save(buf, format="PNG")
                    result["screenshot_base64"] = base64.b64encode(buf.getvalue()).decode()
                result["size"] = f"{img.width}x{img.height}"
            except ImportError:
                result = {"status": "error", "error": "pyautogui not installed"}

        elif action == "window_list":
            if OS == "Windows":
                r = subprocess.run(["tasklist", "/FO", "CSV", "/NH", "/FI", "STATUS eq RUNNING"],
                                   capture_output=True, text=True, timeout=10)
                titles = [line.split(",")[0].replace('"', "") for line in r.stdout.splitlines() if line.strip()][:20]
            elif OS == "Linux":
                r = subprocess.run(["wmctrl", "-l"], capture_output=True, text=True, timeout=5)
                titles = [line.split(None, 3)[-1] if len(line.split(None, 3)) > 3 else line for line in r.stdout.splitlines()][:20]
            else:
                titles = ["macOS: use osascript"]
            result["windows"] = titles
            result["count"] = len(titles)

        elif action == "window_focus":
            title = params.get("window_title", "")
            if OS == "Windows":
                subprocess.run(["powershell", "-Command",
                    f"(Get-Process | Where-Object {{$_.MainWindowTitle -like '*{title}*'}} | Select-Object -First 1).MainWindowHandle"],
                    capture_output=True, timeout=5)
            result["focused"] = title

        elif action == "clipboard_read":
            try:
                import pyperclip
                result["text"] = pyperclip.paste()
            except ImportError:
                result = {"status": "error", "error": "pyperclip not installed"}

        elif action == "clipboard_write":
            try:
                import pyperclip
                pyperclip.copy(params.get("text", ""))
                result["written"] = True
            except ImportError:
                result = {"status": "error", "error": "pyperclip not installed"}

        elif action == "alert":
            msg = params.get("message", "Agent alert")
            if OS == "Windows":
                subprocess.run(["msg", "*", msg], capture_output=True, timeout=5)
            elif OS == "Linux":
                subprocess.run(["notify-send", "openMegatron Agent", msg], capture_output=True, timeout=5)
            result["alerted"] = msg[:80]

        else:
            result = {"status": "error", "error": f"Unknown action: {action}"}

        print(json.dumps(result, ensure_ascii=False))

    except Exception as exc:
        print(json.dumps({"status": "error", "action": action,
                          "error": f"{exc.__class__.__name__}: {exc}"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
