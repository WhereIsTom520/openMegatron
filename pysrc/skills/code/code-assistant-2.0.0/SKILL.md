---
name: code_assistant
version: 2.0.0
category: code
description: Full-stack code engineering — inspect, search, edit, build, test, lint, scan, git, and analyze. Reads like External Agent JSONL, acts like a senior engineer.
risk: medium
actions:
  - inspect
  - search
  - read
  - edit
  - replace_all
  - build
  - test
  - lint
  - format
  - scan_secrets
  - scan_dangerous
  - complexity
  - deps
  - symbols
  - git_diff
  - git_log
  - git_snapshot
  - git_restore
  - git_branch
  - git_changed
  - changelog
  - commands
  - stack
---

# Code Assistant v2.0.0

A External-agent-grade software engineering assistant. Operates on real repositories
with full read, edit, build, test, lint, scan, and git capabilities.

## Actions

### Repository Understanding
- **inspect**: Full project fingerprint — language, framework, package manager, test framework,
  source directories, test directories, entry points, file/line counts.
- **stack**: Shorthand for inspect — quick tech stack summary.
- **symbols** `<file>`: Extract classes, functions, methods, interfaces from source files.
  Supports Python (AST), TypeScript/JavaScript (regex). Returns symbols with docstrings.
- **deps**: List all declared dependencies with versions (package.json, pyproject.toml,
  Cargo.toml, go.mod, requirements.txt). Distinguishes prod vs dev deps.

### Search & Read
- **search** `<pattern>` [glob] [case_sensitive]: Full-text regex search across source files
  with 2-line context. Supports glob filtering.
- **read** `<file>` [start_line] [end_line]: Read a file with optional line range.
  Masks detected secrets in output.

### Editing (with backup)
- **edit** `<file> <old_string> <new_string>`: Replace a UNIQUE string in a file.
  Creates `.bak` backup automatically. Fails if old_string matches 0 or >1 times.
- **replace_all** `<file> <old_string> <new_string>`: Replace ALL occurrences of old_string
  in a file. Creates `.bak` backup.

### Build & Test
- **build**: Run the inferred build command. Returns exit code, stdout, stderr.
- **test**: Run the inferred test command. Returns exit code, stdout, stderr.
- **lint**: Run inferred lint tools (eslint, ruff, clippy, etc.).
- **format**: Run inferred formatter (prettier, ruff format, cargo fmt, go fmt).
- **commands**: List all inferred commands for this project (build, lint, test, format, typecheck).

### Security Scanning
- **scan_secrets** `<file_or_dir>`: Scan for exposed API keys, tokens, passwords, private keys.
  Covers OpenAI keys, GitHub PATs, AWS keys, JWT secrets, and more.
- **scan_dangerous** `<file_or_dir>`: Find dangerous patterns — eval(), os.system(),
  shell=True, pickle.loads(), dangerouslySetInnerHTML, innerHTML assignment, unsafe{} blocks.

### Code Quality
- **complexity** `<file>`: Estimate cyclomatic complexity. Returns line count, function count,
  average complexity, and hotspots (functions with >10 branch points).

### Git Operations
- **git_diff** [staged]: Show working-tree changes as unified diff.
- **git_log** [count]: Show recent commits with author, date, message.
- **git_snapshot** [description]: Create a stash-based rollback point. Returns ref for restore.
- **git_restore** `<ref>`: Restore to a previous snapshot (stash pop or git reset).
- **git_branch**: Show current branch, modified files, untracked files.
- **git_changed** [since]: List files changed since a git ref (default: HEAD~1).
- **changelog**: Generate a Markdown changelog from recent git history, grouped by
  feat/fix/refactor/docs/chore.

## Usage Examples

```
# Understand a codebase
→ code_assistant inspect
→ code_assistant deps
→ code_assistant symbols src/app.py

# Find and fix
→ code_assistant search "TODO|FIXME|HACK"
→ code_assistant scan_secrets .
→ code_assistant complexity src/main.ts

# Safe refactoring workflow
→ code_assistant git_snapshot "before-refactor"
→ code_assistant edit src/utils.py "old_function(data)" "new_function(data, strict=True)"
→ code_assistant test
→ code_assistant git_diff
# If tests pass, commit. If not:
→ code_assistant git_restore "stash@{0}"

# Security audit
→ code_assistant scan_dangerous src/
→ code_assistant scan_secrets .
```

## Notes

- All edit operations create `.bak` files — roll back by renaming `.bak` → original.
- Git operations require the project to be a git repository.
- Symbol extraction for TypeScript uses regex (no full TS compiler) — Python uses AST.
- Complexity is estimated heuristically (branch counts), not precise McCabe cyclomatic.
- Secret scanning uses regex patterns — it may have false positives. Review findings.
