---
name: code_assistant
description: External-agent-style software engineering workflow for repository inspection, bug fixing, feature implementation, refactoring, and verification without loading unrelated skill categories.
category: code
entry_function: main
parameters:
  type: object
  properties:
    action:
      type: string
      description: One of inspect, search, read, test_plan.
    root:
      type: string
      description: Workspace root. Defaults to the current project directory.
    pattern:
      type: string
      description: Search pattern for action=search.
    regex:
      type: boolean
      description: Treat pattern as a regular expression.
    path:
      type: string
      description: Single file or directory path for read/search.
    paths:
      type: array
      description: File or directory paths for read/search.
    max_results:
      type: integer
      description: Maximum search results.
    max_chars:
      type: integer
      description: Maximum characters to return per file.
  required:
    - action
keywords: [code, coding, programmer, external_agent code, externalagentjsonl, repository, bug, debug, fix, implement, refactor, patch, test, build, lint]
capabilities: [inspect, search, read, analyze, plan]
consumes:
  path: optional workspace path or file paths
  pattern: optional search pattern
produces:
  workspace_overview: detected stack, key files, and likely verification commands
  search_results: matching files and redacted matching lines
risk: Read-only helper. Code edits must be made separately with targeted patches and verified with explicit commands.
---

# Code Assistant

Use this skill for software engineering tasks: implement features, fix bugs, debug errors, refactor code, explain a repository, or decide which tests to run.

## Workflow

1. Orient: inspect the workspace, search with narrow terms, and read only the files needed for the task.
2. Plan: keep a short checklist when the change spans multiple files. Ask only for blockers that cannot be inferred from the repo.
3. Edit: make small targeted patches, preserve user changes, and follow the existing style.
4. Verify: run the smallest relevant test, lint, typecheck, build, or startup check. Expand verification when the change touches shared behavior.
5. Report: summarize changed files and exact verification results. If verification is blocked, include the command and the blocker.

## Helper Actions

- `inspect`: map the repo, detected stack, key files, and likely verification commands.
- `search`: search source files and return redacted matching lines.
- `read`: read specific files with secrets redacted.
- `test_plan`: suggest verification commands from repo metadata.

Do not use this skill as a substitute for applying patches. It is a compact repo navigator plus a coding workflow guide.
