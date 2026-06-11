---
name: notification
version: 1.0.0
category: agent
description: System notifications, alerts, and scheduled reminders — toast popups, sound alerts, email-like reminders, and status bar messages.
risk: low
actions:
  - toast
  - alert_box
  - reminder
  - reminder_list
  - reminder_cancel
  - sound
keywords: [notification, alert, reminder, toast, popup, sound, schedule, 通知, 提醒, 弹窗]
parameters:
  action:
    type: string
    enum: [toast, alert_box, reminder, reminder_list, reminder_cancel, sound]
    required: true
  title:
    type: string
    description: Notification title.
  message:
    type: string
    description: Notification body text.
  duration_sec:
    type: integer
    description: "Toast display duration in seconds. Default 5."
    default: 5
  remind_at:
    type: string
    description: "ISO timestamp for scheduled reminder, e.g. 2026-06-12T09:00:00"
  remind_message:
    type: string
    description: Reminder message.
  reminder_id:
    type: string
    description: Reminder ID for cancel.
  sound_type:
    type: string
    description: "Sound type: beep | chime | alert | custom_path"
    enum: [beep, chime, alert]
    default: beep
produces:
  stdout: JSON with status.
side_effects:
  - Shows system notification toasts.
  - Plays system sounds.
  - Stores reminders in .runtime/reminders.json.
risk: low
---

# Notification v1.0.0

System notifications and reminders for the agent to communicate with the user.

## Actions

- **toast** `<title> <message> [duration_sec]` — Show a system toast notification.
- **alert_box** `<title> <message>` — Show a modal alert dialog.
- **reminder** `<remind_at> <remind_message>` — Schedule a reminder for a future time.
- **reminder_list** — List all pending reminders.
- **reminder_cancel** `<reminder_id>` — Cancel a scheduled reminder.
- **sound** `[sound_type=beep]` — Play a system sound.

## Reminder Persistence

Reminders are stored in `.runtime/reminders.json`. Active reminders are checked
every 30 seconds by a background thread. Expired reminders are cleaned up
automatically.

## Platform Support

- Windows: win10toast or PowerShell toast
- Linux: notify-send
- macOS: osascript display notification
