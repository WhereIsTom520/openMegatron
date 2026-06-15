"""GUI action execution via PyAutoGUI/Playwright.

Provides low-level OS automation: click, type, scroll, drag, hotkey, screenshot.
Used as the backend for the gui-automation skill pack.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _get_screen_size() -> tuple:
    """Get primary screen dimensions."""
    try:
        import pyautogui
        return pyautogui.size()
    except ImportError:
        try:
            import tkinter
            root = tkinter.Tk()
            w = root.winfo_screenwidth()
            h = root.winfo_screenheight()
            root.destroy()
            return (w, h)
        except Exception:
            return (1920, 1080)


def _normalize_coordinates(x: float, y: float, screen_w: int, screen_h: int,
                           relative: bool = False) -> tuple:
    """Convert relative [0,1000] coordinates to absolute pixels if needed."""
    if relative:
        return (int(x / 1000.0 * screen_w), int(y / 1000.0 * screen_h))
    return (int(x), int(y))


def execute_click(x: float, y: float, button: str = "left",
                  relative: bool = False) -> dict:
    """Click at the given coordinates.

    Args:
        x, y: Pixel coordinates (absolute) or [0,1000] (relative).
        button: "left", "right", or "middle".
        relative: If True, x/y are in [0,1000] range, mapped to screen.

    Returns:
        dict with status, action, coordinate, elapsed_ms.
    """
    t0 = time.monotonic()
    try:
        import pyautogui
        pyautogui.FAILSAFE = True
        screen_w, screen_h = _get_screen_size()
        abs_x, abs_y = _normalize_coordinates(x, y, screen_w, screen_h, relative)

        pyautogui.click(abs_x, abs_y, button=button)
        elapsed = (time.monotonic() - t0) * 1000
        return {
            "status": "success",
            "action": "click",
            "coordinate": [abs_x, abs_y],
            "button": button,
            "screen_size": [screen_w, screen_h],
            "elapsed_ms": round(elapsed, 1),
        }
    except ImportError:
        return {"status": "error", "message": "pyautogui not installed. Run: pip install pyautogui"}
    except Exception as e:
        elapsed = (time.monotonic() - t0) * 1000
        return {"status": "error", "message": str(e), "elapsed_ms": round(elapsed, 1)}


def execute_type(text: str, interval: float = 0.02) -> dict:
    """Type text at the current cursor position.

    Args:
        text: Text to type. Supports newlines.
        interval: Seconds between keystrokes.

    Returns:
        dict with status, text_length, elapsed_ms.
    """
    t0 = time.monotonic()
    try:
        import pyautogui
        pyautogui.typewrite(text, interval=interval)
        elapsed = (time.monotonic() - t0) * 1000
        return {
            "status": "success",
            "action": "type",
            "text_length": len(text),
            "elapsed_ms": round(elapsed, 1),
        }
    except ImportError:
        return {"status": "error", "message": "pyautogui not installed. Run: pip install pyautogui"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def execute_scroll(clicks: int = -3, x: float = None, y: float = None) -> dict:
    """Scroll the mouse wheel.

    Args:
        clicks: Positive = scroll up, negative = scroll down. Default -3.
        x, y: Optional coordinates to move to before scrolling.

    Returns:
        dict with status, scroll_amount, elapsed_ms.
    """
    t0 = time.monotonic()
    try:
        import pyautogui
        if x is not None and y is not None:
            pyautogui.moveTo(int(x), int(y), duration=0.1)
        pyautogui.scroll(clicks)
        elapsed = (time.monotonic() - t0) * 1000
        return {
            "status": "success",
            "action": "scroll",
            "clicks": clicks,
            "elapsed_ms": round(elapsed, 1),
        }
    except ImportError:
        return {"status": "error", "message": "pyautogui not installed. Run: pip install pyautogui"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def execute_drag(x1: float, y1: float, x2: float, y2: float,
                 duration: float = 0.5, relative: bool = False) -> dict:
    """Drag from (x1,y1) to (x2,y2).

    Args:
        x1, y1: Start coordinates.
        x2, y2: End coordinates.
        duration: Drag duration in seconds.
        relative: If True, coordinates are in [0,1000] range.

    Returns:
        dict with status, start, end, elapsed_ms.
    """
    t0 = time.monotonic()
    try:
        import pyautogui
        screen_w, screen_h = _get_screen_size()
        if relative:
            x1 = int(x1 / 1000.0 * screen_w)
            y1 = int(y1 / 1000.0 * screen_h)
            x2 = int(x2 / 1000.0 * screen_w)
            y2 = int(y2 / 1000.0 * screen_h)
        pyautogui.moveTo(int(x1), int(y1), duration=0.1)
        pyautogui.drag(int(x2) - int(x1), int(y2) - int(y1), duration=duration)
        elapsed = (time.monotonic() - t0) * 1000
        return {
            "status": "success",
            "action": "drag",
            "start": [int(x1), int(y1)],
            "end": [int(x2), int(y2)],
            "elapsed_ms": round(elapsed, 1),
        }
    except ImportError:
        return {"status": "error", "message": "pyautogui not installed. Run: pip install pyautogui"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def execute_hotkey(keys: list) -> dict:
    """Press a hotkey combination.

    Args:
        keys: List of key names, e.g. ["ctrl", "c"] or ["alt", "tab"].

    Returns:
        dict with status, keys, elapsed_ms.
    """
    t0 = time.monotonic()
    try:
        import pyautogui
        pyautogui.hotkey(*keys)
        elapsed = (time.monotonic() - t0) * 1000
        return {
            "status": "success",
            "action": "hotkey",
            "keys": keys,
            "elapsed_ms": round(elapsed, 1),
        }
    except ImportError:
        return {"status": "error", "message": "pyautogui not installed. Run: pip install pyautogui"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def execute_press(key: str) -> dict:
    """Press and release a single key.

    Args:
        key: Key name, e.g. "enter", "escape", "tab", "backspace".

    Returns:
        dict with status, key, elapsed_ms.
    """
    t0 = time.monotonic()
    try:
        import pyautogui
        pyautogui.press(key)
        elapsed = (time.monotonic() - t0) * 1000
        return {
            "status": "success",
            "action": "press",
            "key": key,
            "elapsed_ms": round(elapsed, 1),
        }
    except ImportError:
        return {"status": "error", "message": "pyautogui not installed. Run: pip install pyautogui"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def execute_move(x: float, y: float, duration: float = 0.2,
                 relative: bool = False) -> dict:
    """Move mouse to coordinates without clicking.

    Args:
        x, y: Target coordinates.
        duration: Movement duration in seconds.
        relative: If True, coordinates are in [0,1000] range.

    Returns:
        dict with status, coordinate, elapsed_ms.
    """
    t0 = time.monotonic()
    try:
        import pyautogui
        screen_w, screen_h = _get_screen_size()
        abs_x, abs_y = _normalize_coordinates(x, y, screen_w, screen_h, relative)
        pyautogui.moveTo(abs_x, abs_y, duration=duration)
        elapsed = (time.monotonic() - t0) * 1000
        return {
            "status": "success",
            "action": "move",
            "coordinate": [abs_x, abs_y],
            "elapsed_ms": round(elapsed, 1),
        }
    except ImportError:
        return {"status": "error", "message": "pyautogui not installed. Run: pip install pyautogui"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def execute_sleep(seconds: float) -> dict:
    """Wait for a specified duration. Useful between actions."""
    time.sleep(max(0, min(seconds, 30)))
    return {"status": "success", "action": "sleep", "seconds": seconds}


# ── Action dispatcher ───────────────────────────────────────────────────────

ACTION_MAP = {
    "click": lambda p: execute_click(
        p.get("x", 0), p.get("y", 0),
        p.get("button", "left"), p.get("relative", False),
    ),
    "type": lambda p: execute_type(
        p.get("text", ""), p.get("interval", 0.02),
    ),
    "scroll": lambda p: execute_scroll(
        p.get("clicks", -3), p.get("x"), p.get("y"),
    ),
    "drag": lambda p: execute_drag(
        p.get("x1", 0), p.get("y1", 0),
        p.get("x2", 0), p.get("y2", 0),
        p.get("duration", 0.5), p.get("relative", False),
    ),
    "hotkey": lambda p: execute_hotkey(p.get("keys", [])),
    "press": lambda p: execute_press(p.get("key", "enter")),
    "move": lambda p: execute_move(
        p.get("x", 0), p.get("y", 0),
        p.get("duration", 0.2), p.get("relative", False),
    ),
    "sleep": lambda p: execute_sleep(p.get("seconds", 1.0)),
}


def dispatch_action(action: str, params: dict = None) -> dict:
    """Execute a named GUI action.

    Args:
        action: One of click, type, scroll, drag, hotkey, press, move, sleep.
        params: Action-specific parameters dict.

    Returns:
        dict with status and action-specific result fields.
    """
    params = params or {}
    if action not in ACTION_MAP:
        return {
            "status": "error",
            "message": f"Unknown action: {action}. Available: {list(ACTION_MAP)}",
        }
    try:
        return ACTION_MAP[action](params)
    except Exception as e:
        return {"status": "error", "action": action, "message": str(e)}


def execute_sequence(actions: list) -> list:
    """Execute a sequence of GUI actions.

    Args:
        actions: List of {"action": str, "params": dict} dicts.

    Returns:
        List of result dicts, one per action.
    """
    results = []
    for i, step in enumerate(actions):
        action_name = step.get("action", "sleep")
        params = step.get("params", {})
        result = dispatch_action(action_name, params)
        result["step"] = i
        results.append(result)
        if result.get("status") == "error" and step.get("stop_on_error", True):
            break
    return results
