---
name: desktop_control
version: 1.0.0
category: agent
description: AI-controlled desktop — mouse move/click/drag, keyboard type/hotkey, window manage, screen capture, and clipboard read/write.
risk: high
actions:
  - mouse_move
  - mouse_click
  - mouse_drag
  - keyboard_type
  - keyboard_hotkey
  - screen_capture
  - window_list
  - window_focus
  - clipboard_read
  - clipboard_write
  - alert
keywords: [desktop, mouse, keyboard, screenshot, clipboard, window, automate, 桌面, 鼠标, 键盘, 截图]
parameters:
  action:
    type: string
    enum: [mouse_move, mouse_click, mouse_drag, keyboard_type, keyboard_hotkey, screen_capture, window_list, window_focus, clipboard_read, clipboard_write, alert]
    required: true
  x:
    type: integer
    description: X coordinate for mouse actions.
  y:
    type: integer
    description: Y coordinate for mouse actions.
  button:
    type: string
    description: "Mouse button: left | right | middle"
    enum: [left, right, middle]
    default: left
  text:
    type: string
    description: Text to type or clipboard content.
  keys:
    type: string
    description: "Hotkey combo like ctrl+c, alt+tab, ctrl+shift+esc"
  window_title:
    type: string
    description: Window title substring to focus.
  path:
    type: string
    description: Output path for screen capture (PNG).
  message:
    type: string
    description: Alert message text.
produces:
  stdout: JSON with status and action results.
side_effects:
  - Controls mouse and keyboard (requires user permission).
  - Reads/writes system clipboard.
  - Takes screenshots.
risk: high
---

# Desktop Control v1.0.0

AI-controlled desktop via mouse, keyboard, and window management.

## Actions

- **mouse_move** `<x> <y>` — Move mouse to coordinates
- **mouse_click** `<x> <y> [button=left]` — Click at position
- **mouse_drag** `<x1> <y1> <x2> <y2>` — Drag from (x1,y1) to (x2,y2)
- **keyboard_type** `<text>` — Type text at current focus
- **keyboard_hotkey** `<keys>` — Press key combo (e.g., "ctrl+c")
- **screen_capture** `[path]` — Take screenshot of entire screen
- **window_list** — List all open window titles
- **window_focus** `<window_title>` — Bring a window to front
- **clipboard_read** — Read current clipboard text
- **clipboard_write** `<text>` — Write text to clipboard
- **alert** `<message>` — Show a system alert dialog

## Requirements

- Windows: pyautogui + pillow
- Linux: pyautogui + pillow + scrot (for screenshots)
- High-risk skill — requires user confirmation before execution
