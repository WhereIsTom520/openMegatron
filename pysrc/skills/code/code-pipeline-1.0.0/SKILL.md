---
name: code_pipeline
version: 1.0.0
category: code
description: Automated coding agent loop — understand → plan → snapshot → implement → verify → report. Mirrors review_pipeline for code tasks. One action orchestrates the full workflow.
risk: medium
actions:
  - run
  - plan_only
  - resume
keywords:
  - implement
  - fix
  - refactor
  - feature
  - debug
  - build
  - lint
  - typecheck
  - deploy
  - 实现
  - 修复
  - 重构
  - 调试
  - 测试
  - 开发
parameters:
  task:
    type: string
    description: "The coding task description in natural language (Chinese or English)"
    required: true
    examples: ["fix the null pointer in src/utils.ts line 42", "add a logout button to the header", "refactor App.tsx to use React Router"]
  mode:
    type: string
    description: "Execution mode"
    enum: ["full", "plan_only", "implement_only", "verify_only"]
    default: "full"
  auto_fix:
    type: boolean
    description: "Automatically attempt to fix failures (up to 3 retries)"
    default: false
  max_files:
    type: integer
    description: "Maximum number of files to touch in one run"
    default: 5
    minimum: 1
    maximum: 20
  language:
    type: string
    description: "Force language detection (auto-detect if empty)"
    enum: ["", "python", "typescript", "javascript", "rust", "go"]
    default: ""
---

# Code Pipeline v1.0.0

The coding equivalent of `review_pipeline`. One call orchestrates the full coding workflow:

```
UNDERSTAND → PLAN → SNAPSHOT → IMPLEMENT → VERIFY → REPORT
    │           │        │           │          │         │
    │           │        │           │          │         └─ Summary + diff + quality gates
    │           │        │           │          └─ Lint → Typecheck → Test
    │           │        │           └─ Edit files (with .bak), validate each
    │           │        └─ Git stash snapshot (safety net)
    │           └─ Ordered edit plan with file list
    └─ Project fingerprint + search for relevant code
```

If any gate fails, the pipeline stops and reports what went wrong.
If `auto_fix: true`, it will attempt up to 3 retries with repair strategies.

## Actions

- **run** `<task>` [mode=full] [auto_fix=false] [max_files=5] [language=""]:
  Execute the full pipeline. Describes the task, the pipeline plans the edits,
  snapshots, implements, and verifies.

- **plan_only** `<task>` [max_files=5]:
  Run only the UNDERSTAND + PLAN phases. Returns an edit plan for review.

- **resume** `<plan_id>`:
  Resume a pipeline from a previously generated plan. Useful when you want to
  review the plan manually before implementing.

## Pipeline Phases

### 1. UNDERSTAND
- Fingerprint the project (stack, framework, test tools, source dirs)
- Search for files relevant to the task
- Read the relevant files
- Extract symbols from affected files

### 2. PLAN
- List all files that will be modified
- Order edits by dependency (definitions before uses)
- Identify test files to run after changes
- Estimate complexity and risk

### 3. SNAPSHOT
- Create git stash snapshot with descriptive message
- Record the snapshot ref for potential rollback

### 4. IMPLEMENT
- For each file in the plan:
  - Read the current content
  - Apply the edit (create .bak backup)
  - Validate the edit (syntax check if applicable)
- If any edit fails validation, stop and report

### 5. VERIFY
- Run linter (if available)
- Run type checker (if available)
- Run test suite
- Report pass/fail for each gate

### 6. REPORT
- Show git diff of all changes
- Summarize what was changed and why
- Report quality gate results
- If anything failed, suggest next steps

## Quality Gates

| Gate | Python | TypeScript | Rust | Go |
|------|--------|------------|------|-----|
| Lint | ruff check | eslint | cargo clippy | golangci-lint |
| Typecheck | mypy | tsc --noEmit | cargo check | go vet |
| Test | pytest -q | npm test | cargo test | go test |

## Safety Guarantees

- **Snapshot first**: Every run creates a git snapshot before touching any file.
- **Backup every edit**: Every file edit creates a `.bak` copy.
- **Rollback on failure**: If VERIFY fails, the snapshot ref is preserved for manual rollback.
- **Dry-run plan**: `plan_only` mode shows exactly what would change without touching files.
- **File limit**: `max_files` caps how many files can be modified in one run.

## Notes

- Requires git for snapshot/rollback safety.
- The pipeline runs locally — no code is sent to external services.
- For complex multi-file refactors, start with `plan_only` to review the plan first.
- The LLM will make the actual edit decisions — the pipeline provides the safety framework.
