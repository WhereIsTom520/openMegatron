# Release Notes

## OpenMegatron 1.0

OpenMegatron 1.0 is intended to be easy to try on Windows:

```bat
start.bat
```

The launcher checks dependencies, starts the backend and frontend, prepares Docker databases when available, handles port conflicts, and prints the URL to open.

## Quick Start

1. Download the release archive.
2. Unzip it.
3. Edit `pysrc/model.toml`.
4. Run `start.bat`.
5. Open the URL printed by the launcher.

## Common Commands

```bat
start.bat             Start everything
start.bat health      Check status
start.bat stop        Stop services
start.bat install     Reinstall dependencies
start.bat test        Run tests
```

## What Is Included

- Web chat interface.
- Python agent backend.
- Versioned skill packs.
- Long-term memory with Redis, PostgreSQL/pgvector, and Neo4j.
- RAG ingestion and retrieval.
- Companion-model reward/scoring loop.
- GUI automation tools.
- External trajectory import.
- Evaluation and ablation scaffolding.

## Verification

```bash
python -m py_compile pysrc/agent.py scripts/data_admin.py scripts/runtime_setup.py
python -m pytest tests -q
npm run lint
npm run build
```

Current verified test result: `113 passed`.

## DOI

https://doi.org/10.5281/zenodo.20711569

