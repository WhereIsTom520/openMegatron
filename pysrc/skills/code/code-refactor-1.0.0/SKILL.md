---
name: code_refactor
version: 1.0.0
category: code
description: Safe code refactoring with git-snapshot rollback, rename extraction, dead code detection, and dry-run preview.
risk: medium
actions:
  - snapshot
  - restore
  - rename_symbol
  - extract_function
  - dead_code
  - preview
---

# Code Refactor v1.0.0

Safe, git-backed refactoring. Every destructive operation creates a snapshot first.
If something breaks, restore instantly.

## Actions

- **snapshot** `<description>`: Create a rollback point. Returns a ref you can pass to `restore`.

- **restore** `<ref>`: Restore to a previous snapshot. Uses `git stash pop` or `git reset --hard`.

- **rename_symbol** `<file> <old_name> <new_name> [--dry-run]`:
  Rename a symbol (function, class, variable) across all source files.
  With `--dry-run`, shows what would change without writing.

- **extract_function** `<file> <start_line> <end_line> <new_name> [--dry-run]`:
  Extract a block of code into a new function. Replaces the block with a call to the new function.

- **dead_code** `<path>`:
  Detect potentially dead code — unused imports, unreachable functions,
  variables defined but never read. Uses heuristic analysis.

- **preview** `<file>`:
  Show a diff-like preview of all uncommitted changes in the working tree.

## Safety Guarantees

1. Every write operation checks for a git snapshot first — if none exists in the last 5 minutes, it creates one automatically.
2. All rename operations support `--dry-run` to preview before applying.
3. Restore is always available as long as you have git history.
4. Backups (`.bak` files) are created for every edited file.

## Notes

- Requires the project to be a git repository.
- Symbol rename uses regex-based search-and-replace — review the diff before committing.
- For TypeScript, use `tsc --noEmit` after refactoring to verify type safety.
