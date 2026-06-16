# OpenMegatron

OpenMegatron is a local AI agent workbench for chat, tool use, long-term memory, research retrieval, code tasks, GUI automation, and trajectory-driven companion-model learning.

The main rule is simple: **on Windows, start with `start.bat`. Do not manually start every component unless you are debugging.**

[English](README.md) | [中文](README_CN.md)

## One-Click Start

### 1. Download

Download the source code from the GitHub Release:

https://github.com/WhereIsTom520/openMegatron/releases/tag/v1.0.0

Unzip it and open the `openMegatron` folder.

### 2. Configure a Model

Edit:

```text
pysrc/model.toml
```

Add your provider, API key, base URL, and model name.

### 3. Start

Double-click:

```text
start.bat
```

Or run:

```bat
start.bat
```

The launcher handles:

- Python virtual environment
- Python dependencies
- frontend dependencies
- Docker databases
- backend server
- frontend server
- port conflict detection

Open the URL printed by the launcher, usually:

```text
http://localhost:3000
```

If port `3000` is busy, the launcher automatically tries `3001`, `3002`, and so on.

## Common Commands

```bat
start.bat             Start backend and frontend
start.bat health      Check service status
start.bat stop        Stop services
start.bat install     Reinstall dependencies
start.bat test        Run tests
start.bat menu        Open menu
```

Advanced options:

```bat
start.bat -NoBrowser
start.bat -SkipDocker
start.bat -BackendPort 8001
start.bat -FrontendPort 3001
```

## What It Does

- **Agent chat workbench**: web UI plus backend tool execution.
- **Multi-model support**: OpenAI-compatible APIs, DeepSeek, local llama.cpp, and similar endpoints.
- **Skill system**: code, research, office, media, and agent-orchestration skill packs.
- **Long-term memory**: Redis + PostgreSQL/pgvector + Neo4j.
- **RAG retrieval**: document ingestion, vector search, graph search, and citation-style answers.
- **Companion-model loop**: collect task traces, train reward/scoring models, improve routing and quality checks.
- **GUI automation**: screenshot, click, type, scroll, drag, and related computer-control actions.
- **External log ingestion**: External Agent JSONL, external text-agent compatible logs, OpenClaw/Hermes trajectories.
- **Evaluation scaffolding**: ablation experiments for RAG, memory, and companion AI components.

## What The Companion Model Means

The companion model is not presented as a full replacement for cloud models. In the current 1.0 release, it is a local learning loop around OpenMegatron:

1. collect task traces and tool-call outcomes;
2. train reward/scoring models;
3. use those scores for task evaluation, routing, retraining, and regression checks;
4. optionally extend toward SFT/DPO/QLoRA workflows for local models.

Its value is that OpenMegatron can learn from your own task history instead of relying only on fixed rules.

## Project Layout

```text
openMegatron/
├── start.bat              Windows one-click launcher
├── start.ps1              launcher implementation
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

Read logs:

```text
.runtime/
```

Restart:

```bat
start.bat stop
start.bat
```

## DOI

Zenodo DOI:

https://doi.org/10.5281/zenodo.20711569

