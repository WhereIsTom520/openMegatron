---
name: code_review
version: 1.0.0
category: code
description: Comprehensive code review — security audit, complexity analysis, dependency health, pattern anti-patterns, and actionable fix suggestions.
risk: low
actions:
  - review
  - security_audit
  - complexity_report
  - deps_health
  - diff_review
---

# Code Review v1.0.0

A comprehensive code review tool that analyzes code for security vulnerabilities,
complexity hotspots, dependency issues, and anti-patterns. Generates actionable reports.

## Actions

- **review** `<path>`: Full review — runs security scan, complexity analysis, dependency check,
  and pattern analysis on the specified path (default: entire project).
  Returns a structured report with findings grouped by severity.

- **security_audit** `<path>`: Deep security scan only — secrets, dangerous patterns,
  injection risks, unsafe deserialization. Returns findings with file/line/severity.

- **complexity_report** `<path>`: Complexity analysis across all source files.
  Returns top hotspots ranked by branch count, with refactoring suggestions.

- **deps_health**: Dependency health check — outdated packages, known vulnerabilities
  (via npm audit / pip audit if available), unused dependencies detection.

- **diff_review** [since]: Review only the files changed since a git ref (default: HEAD~1).
  Runs the full review pipeline on just the changed files. Ideal for pre-commit review.

## Review Severity Levels

| Level | Description |
|-------|-------------|
| **critical** | Secrets exposed, remote code execution, SQL injection |
| **high** | Unsafe deserialization, shell injection, XSS |
| **medium** | High complexity (>20 branches), deprecated APIs, missing error handling |
| **low** | Style issues, missing docstrings, unused imports |
| **info** | Suggestions for improvement |

## Notes

- The review runs entirely locally — no code is sent to external services.
- Dependency vulnerability scanning requires the project's package manager to be installed.
- For diff_review, the project must be a git repository.
