---
name: shell_session
version: 1.0.0
category: agent
description: Persistent shell sessions across multiple agent turns — execute commands, maintain working directory and env vars, stream output, and manage multiple sessions.
risk: high
actions:
  - session_new
  - session_run
  - session_output
  - session_list
  - session_kill
  - session_env
  - session_cd
keywords: [shell, bash, cmd, terminal, session, execute, command, persistent, 终端, 命令行, 会话]
parameters:
  action:
    type: string
    enum: [session_new, session_run, session_output, session_list, session_kill, session_env, session_cd]
    required: true
  session_id:
    type: string
    description: Session ID (auto-generated if omitted).
  command:
    type: string
    description: Shell command to run.
  cwd:
    type: string
    description: Working directory for new session.
  env_vars:
    type: object
    description: Environment variables to set.
  timeout_sec:
    type: integer
    description: Command timeout in seconds. Default 30.
    default: 30
  shell:
    type: string
    description: "Shell type: auto | cmd | powershell | bash"
    enum: [auto, cmd, powershell, bash]
    default: auto
produces:
  stdout: JSON with exit code, stdout, stderr, and session metadata.
side_effects:
  - Executes arbitrary shell commands.
  - Maintains persistent state across turns.
risk: high
---

# Shell Session v1.0.0

Persistent shell sessions that survive across agent turns. The agent can start
a long-running process in one turn, check its output in the next, and kill it
when done.

## Actions

- **session_new** `[cwd] [env_vars] [shell]` — Create a new shell session. Returns session_id.
- **session_run** `<session_id> <command> [timeout_sec]` — Run a command in a session. Returns exit code + output.
- **session_output** `<session_id>` — Get buffered output from last command.
- **session_list** — List all active sessions with their CWD and running time.
- **session_kill** `<session_id>` — Kill a session and its child processes.
- **session_env** `<session_id> <env_vars>` — Set environment variables in a session.
- **session_cd** `<session_id> <cwd>` — Change working directory of a session.

## Session Persistence

Sessions are stored in `.runtime/shell_sessions/` as JSON state files.
Each session remembers: CWD, env vars, shell type, last exit code, output buffer.
