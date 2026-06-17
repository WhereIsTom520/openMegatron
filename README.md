# OpenMegatron

OpenMegatron is an independent local research platform for agent trajectory analysis, hybrid memory, tool-use evaluation, retrieval-enhanced knowledge work, and trajectory-driven companion-model learning.

The project is designed for researchers, builders, and product teams who need to inspect how agent systems use tools, remember context, retrieve knowledge, and improve from task traces under local control.

[English](README.md) | [Chinese](README_CN.md)

## Quick Start

### 1. Download

Use the repository homepage for the latest source code:

https://github.com/WhereIsTom520/openMegatron

Clone or download the repository, then open the `openMegatron` folder.

### 2. Configure Credentials Safely

Use local environment variables or a non-tracked `.env` file for private credentials. `pysrc/model.toml` should store only non-sensitive routing and endpoint settings.

Do not commit API keys, private tokens, local runtime state, logs, or machine-specific paths.

### 3. Start On Windows

Double-click:

```text
start.bat
```

Or run:

```bat
start.bat
```

### 4. Start On Linux/macOS

```bash
bash start.sh
```

The launcher handles environment checks, dependency setup, backend and frontend startup, and port conflict detection. Open the URL printed by the launcher, usually:

```text
http://localhost:3000
```

## Common Commands

Windows:

```bat
start.bat             Start backend and frontend
start.bat health      Check service status
start.bat stop        Stop services
start.bat install     Reinstall dependencies
start.bat test        Run tests
start.bat menu        Open menu
```

Linux/macOS:

```bash
bash start.sh
bash start.sh health
bash start.sh stop
bash start.sh install
```

Advanced Windows options:

```bat
start.bat -NoBrowser
start.bat -SkipDocker
start.bat -BackendPort 8001
start.bat -FrontendPort 3001
```

## Operating Modes

**Full mode**

Full hybrid-memory experiments require Docker services: Redis, PostgreSQL/pgvector, and Neo4j. This mode is intended for memory experiments, retrieval studies, graph-backed knowledge work, and end-to-end agent evaluation.

**Reduced mode**

Reduced mode is intended for interface checks, configuration checks, lightweight trajectory inspection, documentation review, and basic backend/frontend validation when the full database stack is not available.

## What It Does

- **Agent trajectory analysis**: captures task traces, tool calls, outcomes, timing, confidence, and feedback signals for later evaluation.
- **Hybrid memory**: combines cache, vector retrieval, relational persistence, and graph memory for long-running knowledge work.
- **Tool-use evaluation**: records and scores tool behavior so agent workflows can be inspected rather than treated as a black box.
- **Retrieval-enhanced knowledge work**: supports document ingestion, vector search, graph search, and citation-style answers.
- **Companion-model learning loop**: turns interaction history into scoring, routing, regression checks, and future fine-tuning data.
- **Skill system**: versioned skill packs for code, research, office, media, and agent-orchestration workflows.
- **GUI automation layer**: screenshot, click, type, scroll, drag, and related computer-control actions.
- **External trajectory ingestion**: imports compatible JSONL/text traces and custom framework data.
- **Evaluation scaffolding**: ablation experiments for retrieval, memory, routing, and companion-model components.

## Why Reviewers May Care

OpenMegatron is positioned as a research artifact rather than only an application demo. It exposes the intermediate data that is often hidden in agent systems:

- task trajectories and tool-call traces;
- memory writes, retrieval paths, and graph relations;
- reward/scoring signals for post-hoc evaluation;
- ablation-friendly components for comparing memory, retrieval, routing, and companion-learning choices;
- local-first execution that makes experiments easier to reproduce and inspect.

The platform can support studies of agent reliability, tool-use behavior, memory architecture, retrieval quality, human feedback signals, and lightweight companion-model training loops.

## Why Product Teams May Care

OpenMegatron also has practical commercial signals:

- **Local governance**: private credentials and runtime data can stay outside the tracked repository.
- **Observable automation**: tool use, failures, retries, and outcomes can be logged and reviewed.
- **Adaptation from usage**: task traces can become evaluation data and training data instead of disposable logs.
- **Modular deployment path**: full mode supports database-backed experiments, while reduced mode supports demos and configuration checks.
- **Model-agnostic routing**: endpoint and routing settings are separated from private credentials.

This makes it useful for teams evaluating internal agent workflows before committing to a larger production architecture.

## Companion Model Scope

The companion model is not presented as a full replacement for cloud models. In the current release, it is a local learning loop around OpenMegatron:

1. collect task traces and tool-call outcomes;
2. train reward/scoring models;
3. use those scores for task evaluation, routing, retraining, and regression checks;
4. optionally extend toward SFT/DPO/QLoRA workflows for local models.

Its value is that OpenMegatron can learn from task history instead of relying only on fixed rules.

## Project Layout

```text
openMegatron/
├── start.bat              Windows one-click launcher
├── start.ps1              launcher implementation
├── start.sh               Linux/macOS launcher
├── pysrc/                 Python backend
│   ├── agent.py           core agent loop
│   ├── skill.py           tool and skill registry
│   ├── memory.py          long-term memory and RAG storage
│   ├── reward_*.py        reward model and training
│   ├── trajectory_*.py    trace collection and import
│   └── skills/            skill packs
├── src/                   React frontend
├── tests/                 Python tests
└── docker-compose.yml     Redis/PostgreSQL/Neo4j
```

## Troubleshooting

Check status:

```bat
start.bat health
```

```bash
bash start.sh health
```

Read logs:

```text
.runtime/
```

Restart:

```bat
start.bat stop
start.bat
```

```bash
bash start.sh stop
bash start.sh
```

## DOI

Zenodo DOI:

https://doi.org/10.5281/zenodo.20711989
