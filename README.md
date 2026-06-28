# OpenMegatron

**Latest version: 1.2.0**

OpenMegatron is a local-first AI agent workbench for long-running agent research, skill routing, hybrid memory, tool-use evaluation, browser/GUI automation, and trajectory-driven companion-model learning.

It is designed for researchers and builders who need to inspect how an agent uses tools, remembers context, retrieves knowledge, promotes reusable skills, and learns from execution traces under local control.

[English](README.md) | [Chinese](README_CN.md)

## What's New In 1.2.0

- **Skill lifecycle governance**: saved, promoted, and workflow-derived skills can now carry a `skill_contract.json` describing permissions, risk level, schemas, and promotion gates.
- **Skill health scoring**: historical trajectories are summarized into per-skill success rate, safety signal rate, regression rate, confirmation burden, latency, and health status.
- **Replay verification gate**: `/skills/replay_verify` checks whether a skill has enough successful historical runs before it should be trusted for automatic reuse.
- **Safer release workflow**: `scripts/scrub_config.py` creates a redacted TOML copy before sharing support logs, examples, or release archives.
- **README refresh**: startup, credential handling, lifecycle APIs, and common commands are documented for the 1.2.0 workflow.

## Quick Start

### 1. Download

Use the repository homepage for the latest source code:

https://github.com/WhereIsTom520/openMegatron

Clone or download the repository, then open the `openMegatron` folder.

### 2. Configure Credentials Safely

Private credentials should stay local. The tracked repository should only contain examples and redacted files.

Recommended options:

- Put provider keys in local environment variables such as `OPENAI_API_KEY`.
- Or keep them in `pysrc/model.toml`, which is ignored by git.
- Use `pysrc/model.example.toml` as a safe template.
- Never commit API keys, access tokens, local runtime state, logs, screenshots, cookies, or machine-specific paths.

Before sharing a config file or release bundle, create a redacted copy:

```bat
venv\Scripts\python.exe scripts\scrub_config.py --input pysrc\model.toml --output .runtime\model.redacted.toml
```

Linux/macOS:

```bash
python scripts/scrub_config.py --input pysrc/model.toml --output .runtime/model.redacted.toml
```

The scrubber does not modify your real config. It writes a copy with common secret fields replaced by `<redacted>`.

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

The launcher handles environment checks, dependency setup, backend and frontend startup, Docker database checks, and port conflict detection. Open the URL printed by the launcher, usually:

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
- **Hybrid memory**: combines cache, vector retrieval, relational persistence, graph memory, and ontology-backed memory records.
- **Theoretical long-context workflow**: keeps short prompt context small while using searchable conversation history and long-term memory recall for older material.
- **Tool-use evaluation**: records and scores tool behavior so agent workflows can be inspected rather than treated as a black box.
- **Retrieval-enhanced knowledge work**: supports document ingestion, vector search, graph search, citation-style answers, and evidence boundaries.
- **Skill lifecycle governance**: records skill contracts, health, replay gates, and generated-skill promotion metadata.
- **Companion-model learning loop**: turns interaction history into scoring, routing, regression checks, and future fine-tuning data.
- **GUI/browser automation layer**: screenshot, click, type, scroll, drag, browser navigation, and controlled local preview actions.
- **External trajectory ingestion**: imports compatible JSONL/text traces and custom framework data.
- **Evaluation scaffolding**: ablation experiments for retrieval, memory, routing, and companion-model components.

## Skill Lifecycle Governance

OpenMegatron skills are loaded from `pysrc/skills/`. In 1.2.0, generated or promoted skills also get a `skill_contract.json` file:

```text
pysrc/skills/generated/<skill_name>/
+-- SKILL.md
+-- skill_contract.json
+-- scripts/main.py
```

The contract records:

- input and output schemas;
- allowed paths, network use, write permission, and command permission;
- risk level;
- lifecycle thresholds such as minimum replay cases and pass rate;
- ontology tags and promotion metadata.

Useful API endpoints:

```text
GET  /skills/list
GET  /skills/lifecycle
POST /skills/replay_verify
```

Replay verification example:

```bash
curl -X POST http://127.0.0.1:8000/skills/replay_verify ^
  -H "Content-Type: application/json" ^
  -d "{\"skill_name\":\"browser_control\"}"
```

If your backend is running on an auto-selected port, use the port printed by `start.bat health`.

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
+-- start.bat              Windows one-click launcher
+-- start.ps1              launcher implementation
+-- start.sh               Linux/macOS launcher
+-- package.json           frontend package metadata, version 1.2.0
+-- pysrc/                 Python backend
|   +-- agent.py           core agent loop and FastAPI endpoints
|   +-- skill.py           tool and skill registry
|   +-- skill_lifecycle.py skill contracts, health, replay gates
|   +-- memory.py          long-term memory and RAG storage
|   +-- reward_*.py        reward model and training
|   +-- trajectory_*.py    trace collection and import
|   +-- skills/            skill packs
+-- scripts/
|   +-- scrub_config.py    redacted TOML copy generator
+-- src/                   React frontend
+-- tests/                 Python tests
+-- docker-compose.yml     Redis/PostgreSQL/Neo4j
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
log/
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

## Release Hygiene

Before publishing or sharing a support bundle:

1. Stop local services with `start.bat stop`.
2. Generate a redacted config copy with `scripts/scrub_config.py`.
3. Check that `pysrc/model.toml`, `.env*`, `.runtime/`, `.trajectory/`, `log/`, cookies, and local screenshots are not included.
4. Run tests relevant to the change.

Useful checks:

```bat
venv\Scripts\python.exe -m py_compile pysrc\agent.py pysrc\skill.py scripts\scrub_config.py
venv\Scripts\python.exe -m pytest tests\test_skill_lifecycle.py tests\test_scrub_config.py -q
npm run lint
```

## DOI

Zenodo DOI:

https://doi.org/10.5281/zenodo.20711989
