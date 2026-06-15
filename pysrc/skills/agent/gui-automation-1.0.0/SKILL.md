---
name: gui_automation
version: 1.0.0
category: agent
description: GUI automation — control mouse, keyboard, and screen via PyAutoGUI. Supports click, type, scroll, drag, hotkey, press, move, sleep, screenshot, and action sequences.
risk: high
actions:
  - click
  - type
  - scroll
  - drag
  - hotkey
  - press
  - move
  - sleep
  - screenshot
  - sequence
keywords: [gui, mouse, keyboard, click, type, screenshot, automate, pyautogui, desktop, control, 鼠标, 键盘, 截图, 自动化, 桌面]
parameters:
  action:
    type: string
    enum: [click, type, scroll, drag, hotkey, press, move, sleep, screenshot, sequence]
    required: true
  x:
    type: number
    description: X coordinate (pixels absolute or [0,1000] relative).
  y:
    type: number
    description: Y coordinate (pixels absolute or [0,1000] relative).
  relative:
    type: boolean
    description: If true, x/y are in [0,1000] range mapped to screen dimensions.
    default: false
  button:
    type: string
    enum: [left, right, middle]
    default: left
  text:
    type: string
    description: Text to type.
  keys:
    type: array
    items: string
    description: Hotkey combination, e.g. ["ctrl", "c"].
  key:
    type: string
    description: Single key to press, e.g. "enter", "escape", "tab".
  clicks:
    type: integer
    description: Scroll amount (negative=down, positive=up).
  x1:
    type: number
    description: Drag start X.
  y1:
    type: number
    description: Drag start Y.
  x2:
    type: number
    description: Drag end X.
  y2:
    type: number
    description: Drag end Y.
  duration:
    type: number
    description: Movement/drag duration in seconds.
  seconds:
    type: number
    description: Sleep duration in seconds.
  interval:
    type: number
    description: Interval between keystrokes for typing.
  actions:
    type: array
    description: Array of action objects for sequence execution.
  screenshot_path:
    type: string
    description: Optional path to save screenshot.
  stop_on_error:
    type: boolean
    description: Stop sequence on first error.
    default: true
produces:
  stdout: JSON with status and action result.
side_effects:
  - Controls mouse and keyboard directly.
  - Can interact with any application on screen.
  - Screenshots may capture sensitive information.
risk: high
---

# GUI Automation v1.0.0

Low-level OS automation: control mouse, keyboard, and capture screen.
Used by the agent to interact with desktop applications and browsers
when text-based APIs are insufficient.

## Actions

- **click** `<x> <y> [button=left] [relative=false]` — Click at coordinates.
- **type** `<text> [interval=0.02]` — Type text at cursor.
- **scroll** `[clicks=-3] [x] [y]` — Scroll mouse wheel.
- **drag** `<x1> <y1> <x2> <y2> [duration=0.5]` — Drag from start to end.
- **hotkey** `<keys>` — Press key combo, e.g. `["ctrl","c"]`.
- **press** `<key>` — Press single key.
- **move** `<x> <y> [duration=0.2]` — Move mouse without clicking.
- **sleep** `<seconds>` — Pause between actions.
- **screenshot** `[path]` — Capture full screen, return base64.
- **sequence** `<actions>` — Execute a list of actions.

## Coordinate Systems

- **Absolute** (default): pixel coordinates relative to screen origin.
- **Relative**: [0,1000] range mapped proportionally to screen dimensions.
  Use relative when the model doesn't know actual screen resolution.

## Examples

```
→ gui_automation screenshot screenshot_path="/tmp/screen.png"
→ gui_automation click x=500 y=300 button="left"
→ gui_automation type text="Hello World" interval=0.05
→ gui_automation sequence actions=[{"action":"click","params":{"x":100,"y":200}},{"action":"sleep","params":{"seconds":1}},{"action":"type","params":{"text":"done"}}]
→ gui_automation hotkey keys=["ctrl","v"]
```

## Safety

- Failsafe: moving mouse to any screen corner triggers PyAutoGUI failsafe.
- Max sleep: 30 seconds per sleep action.
- All actions return status="error" on failure (never crash).
