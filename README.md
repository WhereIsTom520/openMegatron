# OpenMegatron

OpenMegatron is a local AI agent workbench for chat, tool use, long-term memory, research retrieval, code tasks, GUI automation, and trajectory-driven companion-model learning.

**Start on Windows with `start.bat`.** The launcher prepares the Python environment, frontend dependencies, Docker services, backend API, frontend dev server, and port fallback automatically.

[English](README.md) | [中文](README_CN.md)

## Quick Start

### Windows one-click start

```bat
start.bat
```

Then open the URL printed by the launcher, usually:

```text
http://localhost:3000
```

If port `3000` is busy, the launcher tries `3001`, `3002`, and later ports automatically.

### First model configuration

On first launch, `start.ps1` creates `pysrc/model.toml` from `pysrc/model.example.toml` when needed. Edit:

```text
pysrc/model.toml
```

Set your active provider, API key, base URL, and model name. OpenAI-compatible providers, DeepSeek, Qwen, Moonshot, Zhipu, MiniMax, Stepfun, SiliconFlow, OpenRouter, and local compatible endpoints can be configured there.

### Common commands

```bat
start.bat             Start backend and frontend
start.bat health      Check service status
start.bat stop        Stop services
start.bat install     Reinstall dependencies
start.bat test        Run tests
start.bat menu        Open the launcher menu
```

Useful options:

```bat
start.bat -NoBrowser
start.bat -SkipDocker
start.bat -BackendPort 8001
start.bat -FrontendPort 3001
```

## Six Subsystems

OpenMegatron is organized around six major subsystems:

1. **Interaction and API subsystem**: React/TypeScript frontend, FastAPI HTTP endpoints, WebSocket/event streaming, runtime status, and channel-facing request handling.
2. **Agent orchestration and model dispatch subsystem**: the core agent loop, service registry, request planning, model provider configuration, and lite/standard/advanced model-tier routing.
3. **Skill execution subsystem**: versioned skill packs under `pysrc/skills/` for code, research, office, media, monitoring, and agent orchestration tasks.
4. **Memory, RAG, and knowledge graph subsystem**: Redis chat history, PostgreSQL/pgvector retrieval, Neo4j-backed graph memory, ontology entities, literature graphs, and citation-style retrieval.
5. **Companion learning and evaluation subsystem**: trajectory collection/import, reward/scoring models, auto-retraining hooks, regression guards, ablation experiments, and learning dashboard support.
6. **Automation and external integration subsystem**: GUI actions, screen capture, desktop/browser automation skills, Feishu/WeCom-style adapters, log ingestion, and runtime safety checks.

## Project Layout

```text
openMegatron/
├── start.bat              Windows one-click launcher
├── start.ps1              launcher implementation
├── docker-compose.yml     Redis, PostgreSQL/pgvector, Neo4j
├── pysrc/                 Python backend
│   ├── api.py             FastAPI API and channel gateway
│   ├── agent.py           core agent loop
│   ├── model_tier.py      model tier dispatch
│   ├── skill.py           skill discovery and loading
│   ├── skill_router.py    request-to-skill routing
│   ├── memory.py          memory and RAG storage
│   ├── graph_engine.py    graph algorithms
│   ├── literature_graph.py
│   ├── trajectory_*.py    trace collection and import
│   ├── reward_*.py        reward model and training
│   └── skills/            versioned skill packs
├── src/                   React frontend
├── tests/                 Python tests
└── docs/                  documentation and figures
```

## Development

Install runtime dependencies:

```bat
python scripts/runtime_setup.py --toml pysrc/model.toml
```

Run tests:

```bat
python -m pytest tests/ -v
```

Build the frontend:

```bat
npm run build
```

## Troubleshooting

Check status:

```bat
start.bat health
```

Read runtime logs:

```text
.runtime/
```

Restart cleanly:

```bat
start.bat stop
start.bat
```

## License and Attribution

OpenMegatron is free for personal, academic, and commercial use, including modification and redistribution, **with attribution required**. See [LICENSE.txt](LICENSE.txt) for the full terms.

Suggested attribution:

```text
Built with OpenMegatron by WhereIsTom520.
https://github.com/WhereIsTom520/openMegatron
DOI: https://doi.org/10.5281/zenodo.20711569
```

## DOI

Zenodo DOI:

https://doi.org/10.5281/zenodo.20711569
