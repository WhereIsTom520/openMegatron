# Release Notes

## OpenMegatron 1.2.0

OpenMegatron 1.2.0 focuses on safer release hygiene and skill lifecycle governance.

### Highlights

- Generated, promoted, and workflow-derived skills can include `skill_contract.json`.
- `/skills/lifecycle` summarizes contract, health, and replay status for loaded skills.
- `/skills/replay_verify` checks whether a skill has enough successful historical trajectories before automatic reuse.
- `scripts/scrub_config.py` writes a redacted TOML copy for support logs, examples, and release bundles.
- README and README_CN have been refreshed for the 1.2.0 workflow.

### Upgrade Notes

1. Pull or download the latest source.
2. Keep real credentials in environment variables or ignored local files such as `pysrc/model.toml`.
3. Run `start.bat install` if dependencies changed.
4. Start with `start.bat`.
5. Use `start.bat health` to confirm the actual backend/frontend ports.

### Redacted Release Workflow

Before sharing a config or release archive:

```bat
venv\Scripts\python.exe scripts\scrub_config.py --input pysrc\model.toml --output .runtime\model.redacted.toml
```

Check that the bundle does not include:

- `pysrc/model.toml`
- `.env*`
- `.runtime/`
- `.trajectory/`
- `log/` or `logs/`
- browser cookies, screenshots, or local process files

### Verification

Recommended checks for this release:

```bat
venv\Scripts\python.exe -m py_compile pysrc\agent.py pysrc\skill.py pysrc\skill_lifecycle.py scripts\scrub_config.py
venv\Scripts\python.exe -m pytest tests\test_skill_lifecycle.py tests\test_scrub_config.py -q
npm run lint
```

## OpenMegatron 1.1.0

OpenMegatron 1.1.0 improved the local frontend/backend workflow, browser preview behavior, trajectory handling, context recall, and memory-related UI operations.

## OpenMegatron 1.0.0

OpenMegatron 1.0.0 established the Windows-first one-click launcher:

```bat
start.bat
```

The launcher checks dependencies, starts the backend and frontend, prepares Docker databases when available, handles port conflicts, and prints the URL to open.

## DOI

https://doi.org/10.5281/zenodo.20711989
