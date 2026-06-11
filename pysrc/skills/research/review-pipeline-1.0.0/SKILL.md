---
name: review_pipeline
version: 1.2.0
description: Full research workflow with task blackboard, checkpoint/resume, strategy switching, and incremental update — top-venue search → paper reading → evidence matrix → gap analysis → review generation → citation verification → citation graph.
entry_function: main
parameters:
  type: object
  properties:
    action:
      type: string
      description: "run | update | status | list"
      enum: ["run", "update", "status", "list"]
    query:
      type: string
      description: Research topic query.
    year_start:
      type: integer
      description: Earliest paper year.
    limit:
      type: integer
      description: Candidate search limit. Default 100.
    top_n:
      type: integer
      description: Number of papers to keep. Default 8.
    generate_review:
      type: boolean
      description: Whether to call LLM for a Chinese review.
    review_type:
      type: string
      description: narrative or systematic.
    citation_style:
      type: string
      description: gbt7714, ieee, apa, or bibtex.
    domain:
      type: string
      description: Venue-policy domain (ai, nlp, cv, data, hci, cs, is, management, medicine).
    fill_abstracts:
      type: boolean
      description: Enrich missing abstracts. Default true.
    out:
      type: string
      description: Optional JSON output path.
    task_id:
      type: string
      description: Task ID for checkpoint/resume (auto-generated from query if not specified).
    use_blackboard:
      type: boolean
      description: Enable task blackboard with progress tracking. Default true.
      default: true
    resume:
      type: boolean
      description: Resume from last checkpoint if available.
    blackboard_dir:
      type: string
      description: Directory for checkpoint files. Default .blackboard.
    lang:
      type: string
      description: "Report language: zh | en"
      enum: ["zh", "en"]
      default: "zh"
    previous_out:
      type: string
      description: Path to previous pipeline output JSON (for update action).
    new_papers:
      type: array
      description: New papers to add (for update action).
  required:
    - action
    - query
keywords: [review pipeline, literature review, systematic review, evidence matrix, citation verification, research gap, innovation, update, incremental, blackboard, checkpoint, resume, strategy switching]
---

# Review Pipeline v1.2.0

## Actions

### `run` ★ Blackboard-enabled
Full pipeline with task blackboard. Each step tracks progress, supports checkpoint/resume, and automatically retries with alternative strategies on failure.

Pipeline steps:
1. **discover** — 文献发现（OpenAlex + venues.toml 白名单）
2. **read** — 论文阅读（PyPDF2 + OCR fallback）
3. **matrix** — 证据矩阵构建
4. **gaps** — 研究空白分析
5. **review** — 综述生成（LLM 或模板化）
6. **verify** — 引用验证 + 反幻觉检查
7. **graph** — 引用图谱可视化

Each step auto-retries with a fallback strategy on failure (max 2 retries per step).

### `status`
Check the progress of a running/completed task. Returns the blackboard state with per-step status.

### `list`
List all saved task checkpoints with progress info.

### `update`
Incremental update: add new papers to existing pipeline output without re-running full workflow.

## Task Blackboard & Checkpoint

Every `run` automatically creates a checkpoint in `.blackboard/`. Features:

- **Progress visualization**: Shows ⬜🔄✅❌ per step with durations and summaries
- **Crash recovery**: `action=run` with `resume=true` picks up where it left off
- **Strategy switching**: Failed steps automatically try an alternative approach
- **Multi-step tracking**: All 7 pipeline steps tracked independently

Example blackboard output:
```
📋 任务进度: review_agent_memory
   进度: 5/7 (71.4%)
   ✅ 完成: 5 | 🔄 进行中: 1 | ⬜ 待执行: 1 | ❌ 失败: 0

  ✅ 文献发现 (12.3s) — 检索到 45 篇论文（28 篇顶刊顶会）
  ✅ 论文阅读 (8.1s) — 完成 45 篇论文的结构化阅读
  ✅ 证据矩阵 (2.4s) — 构建 45 行证据矩阵
  ✅ 空白分析 (1.2s) — 发现 4 个潜在研究方向
  🔄 综述生成 [策略: LLM 合成 + 证据矩阵驱动]
  ⬜ 引用验证
  ⬜ 引用图谱
```
