#!/usr/bin/env python3
"""gui-automation v1.0.0 — GUI automation via PyAutoGUI.

Provides click, type, scroll, drag, hotkey, press, move, sleep,
screenshot, and action sequence execution.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

# Ensure pysrc is on path for imports
sys.path.insert(0, str(Path(__file__).resolve().parents[4]))


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Missing action JSON."}))
        sys.exit(1)

    try:
        params = json.loads(sys.argv[1])
    except json.JSONDecodeError:
        print(json.dumps({"error": "Invalid JSON."}))
        sys.exit(1)

    action = params.get("action", "screenshot")

    try:
        if action == "screenshot":
            from screen_capture import capture_fullscreen
            output_path = params.get("screenshot_path")
            result = capture_fullscreen(output_path)
            # Don't include full base64 in stdout (too large for tool output)
            result["base64_length"] = len(result.get("base64", ""))
            if output_path:
                result["saved_to"] = output_path
            result["base64"] = result.get("base64", "")[:200] + "..."
            result["status"] = "success"
            print(json.dumps(result, ensure_ascii=False))

        elif action == "sequence":
            from gui_actions import execute_sequence
            actions_list = params.get("actions", [])
            stop_on_error = params.get("stop_on_error", True)
            results = execute_sequence(actions_list)
            success_count = sum(1 for r in results if r.get("status") == "success")
            print(json.dumps({
                "status": "success" if all(r.get("status") == "success" for r in results) else "partial",
                "action": "sequence",
                "total": len(results),
                "success_count": success_count,
                "results": results,
            }, ensure_ascii=False))

        else:
            from gui_actions import dispatch_action
            result = dispatch_action(action, params)
            print(json.dumps(result, ensure_ascii=False))

    except Exception as e:
        print(json.dumps({
            "status": "error",
            "action": action,
            "message": str(e),
        }, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    main()
