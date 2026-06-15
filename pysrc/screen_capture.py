"""Screen capture utility for GUI automation.

Supports full-screen capture, active window capture, and region capture.
Returns base64-encoded PNG for direct use as multimodal LLM input.
"""

from __future__ import annotations

import base64
import io
import logging
import os
import time
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# Try multiple backends in order of preference
_SCREENSHOT_BACKEND = None


def _init_backend():
    global _SCREENSHOT_BACKEND
    if _SCREENSHOT_BACKEND is not None:
        return _SCREENSHOT_BACKEND

    # 1. Try mss (fast, cross-platform, no GUI framework needed)
    try:
        import mss
        _SCREENSHOT_BACKEND = "mss"
        return "mss"
    except ImportError:
        pass

    # 2. Try PIL.ImageGrab (Windows/macOS built-in)
    try:
        from PIL import ImageGrab
        _SCREENSHOT_BACKEND = "pil"
        return "pil"
    except ImportError:
        pass

    # 3. Try pyautogui
    try:
        import pyautogui
        _SCREENSHOT_BACKEND = "pyautogui"
        return "pyautogui"
    except ImportError:
        pass

    _SCREENSHOT_BACKEND = None
    return None


def capture_fullscreen(output_path: str = None) -> dict:
    """Capture the entire screen.

    Args:
        output_path: If provided, save PNG to this path.

    Returns:
        dict with keys:
          - base64: base64-encoded PNG string
          - width, height: image dimensions in pixels
          - path: file path if saved
          - elapsed_ms: capture time
    """
    t0 = time.monotonic()
    backend = _init_backend()
    img = None

    if backend == "mss":
        import mss
        import mss.tools
        with mss.mss() as sct:
            monitor = sct.monitors[1]  # Primary monitor
            img = sct.grab(monitor)
            # mss returns BGRA; convert to PNG bytes
            png_bytes = mss.tools.to_png(img.rgb, img.size)
            width, height = img.size

    elif backend == "pil":
        from PIL import ImageGrab
        pil_img = ImageGrab.grab(all_screens=True)
        width, height = pil_img.size
        buf = io.BytesIO()
        pil_img.save(buf, format="PNG")
        png_bytes = buf.getvalue()

    elif backend == "pyautogui":
        import pyautogui
        pil_img = pyautogui.screenshot()
        width, height = pil_img.size
        buf = io.BytesIO()
        pil_img.save(buf, format="PNG")
        png_bytes = buf.getvalue()

    else:
        raise RuntimeError(
            "No screenshot backend available. Install one: pip install mss pillow pyautogui"
        )

    elapsed = (time.monotonic() - t0) * 1000

    # Save to file if requested
    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(png_bytes)

    return {
        "base64": base64.b64encode(png_bytes).decode("ascii"),
        "width": width,
        "height": height,
        "path": output_path,
        "elapsed_ms": round(elapsed, 1),
    }


def capture_region(x: int, y: int, width: int, height: int,
                   output_path: str = None) -> dict:
    """Capture a specific screen region.

    Args:
        x, y: Top-left corner coordinates.
        width, height: Region dimensions.

    Returns:
        Same dict format as capture_fullscreen.
    """
    t0 = time.monotonic()
    backend = _init_backend()

    if backend == "mss":
        import mss
        import mss.tools
        with mss.mss() as sct:
            region = {"left": x, "top": y, "width": width, "height": height}
            img = sct.grab(region)
            png_bytes = mss.tools.to_png(img.rgb, img.size)
            w, h = img.size

    elif backend == "pil":
        from PIL import ImageGrab
        pil_img = ImageGrab.grab(bbox=(x, y, x + width, y + height))
        w, h = pil_img.size
        buf = io.BytesIO()
        pil_img.save(buf, format="PNG")
        png_bytes = buf.getvalue()

    elif backend == "pyautogui":
        import pyautogui
        pil_img = pyautogui.screenshot(region=(x, y, width, height))
        w, h = pil_img.size
        buf = io.BytesIO()
        pil_img.save(buf, format="PNG")
        png_bytes = buf.getvalue()

    else:
        raise RuntimeError("No screenshot backend available.")

    elapsed = (time.monotonic() - t0) * 1000

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(png_bytes)

    return {
        "base64": base64.b64encode(png_bytes).decode("ascii"),
        "width": w,
        "height": h,
        "path": output_path,
        "elapsed_ms": round(elapsed, 1),
    }


def capture_to_data_uri(output_path: str = None) -> str:
    """Capture full screen and return as a data: URI for LLM API calls."""
    result = capture_fullscreen(output_path)
    return f"data:image/png;base64,{result['base64']}"


def capture_region_to_data_uri(x: int, y: int, width: int, height: int,
                                output_path: str = None) -> str:
    """Capture region and return as a data: URI."""
    result = capture_region(x, y, width, height, output_path)
    return f"data:image/png;base64,{result['base64']}"
