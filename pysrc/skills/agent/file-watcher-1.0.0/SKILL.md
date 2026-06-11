---
name: file_watcher
version: 1.0.0
category: agent
description: Watch files/directories for changes and trigger actions — monitor log files, detect new files, run commands on change, and coalesce rapid events.
risk: medium
actions:
  - watch_start
  - watch_stop
  - watch_list
  - watch_status
keywords: [watch, file, directory, monitor, change, trigger, log, 监控, 文件, 目录]
parameters:
  action:
    type: string
    enum: [watch_start, watch_stop, watch_list, watch_status]
    required: true
  path:
    type: string
    description: File or directory path to watch.
  patterns:
    type: array
    description: "File patterns to watch, e.g. [\"*.log\", \"*.json\"]. Default: all files."
    items:
      type: string
  on_change:
    type: string
    description: Shell command to run when a change is detected. Use {file} placeholder.
  debounce_ms:
    type: integer
    description: Debounce rapid changes (ms). Default 500.
    default: 500
  watch_id:
    type: string
    description: Watch ID for stop/status.
produces:
  stdout: JSON with watch status and change events.
side_effects:
  - Starts background watcher threads.
  - Can execute shell commands on file changes.
risk: medium
---

# File Watcher v1.0.0

Watch files and directories for changes, trigger actions when files are
created, modified, or deleted.

## Actions

- **watch_start** `<path> [patterns] [on_change] [debounce_ms]` — Start watching. Returns watch_id.
- **watch_stop** `<watch_id>` — Stop a watcher.
- **watch_list** — List all active watchers.
- **watch_status** `<watch_id>` — Get recent change events for a watcher.

## Change Event Format

```json
{"type": "created|modified|deleted", "path": "relative/path", "time": "ISO timestamp"}
```

## Examples

```
→ file_watcher watch_start "logs/" patterns=["*.log"] on_change="echo {file} changed"
→ file_watcher watch_list
→ file_watcher watch_status "watch_abc123"
```
