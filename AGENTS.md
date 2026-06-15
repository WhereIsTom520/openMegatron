# AGENTS.md

OpenMegatron — modular, multi-model AI agent platform with skill routing, memory ontology, predictive engine, and literature knowledge graph.

## Project layout

```
openMegatron/
├── pysrc/                          # Python backend (primary codebase)
│   ├── agent.py                    # Core agent loop — orchestrates all engines
│   ├── memory.py                   # Memory subsystem — persistence + retrieval
│   ├── skill.py                    # Skill loader — discovers and loads skill packs
│   ├── api.py                      # FastAPI REST endpoints
│   ├── services.py                 # Service registry (singleton DI container)
│   ├── model_tier.py               # Model tier dispatch (lite/standard/advanced)
│   ├── skill_router.py             # Request-to-skill matching and routing
│   ├── memory_ontology.py          # Structured knowledge ontology (entities + relations)
│   ├── graph_engine.py             # Graph computation backed by networkx.DiGraph
│   ├── literature_graph.py         # Academic literature knowledge graph
│   ├── literature_graph_db.py      # SQLite persistence for literature graph
│   ├── predictive_engine.py        # Intent prediction from historical patterns
│   ├── guided_evolution.py         # Feedback-driven agent behavior optimization
│   ├── cross_category_learner.py   # Cross-domain knowledge transfer
│   ├── decision_tracker.py         # Decision audit trail and analysis
│   ├── cache_engine.py             # TTL+LRU cache backed by cachetools.TTLCache
│   ├── repair_hook.py              # Auto-retry with exponential backoff
│   └── skills/
│       ├── code/                   # Code engineering skill pack (5 skills)
│       │   ├── code-assistant-2.0.0/
│       │   ├── code-pipeline-1.0.0/
│       │   ├── code-refactor-1.0.0/
│       │   ├── code-review-1.0.0/
│       │   ├── code-test-1.0.0/
│       │   └── code_common.py
│       ├── research/               # Research skill pack (7 skills)
│       │   ├── citation-graph-1.0.0/
│       │   ├── citation-verifier-1.0.0/
│       │   ├── evidence-matrix-1.0.0/
│       │   ├── journal-matcher-1.0.0/
│       │   ├── paper-reader-1.0.0/
│       │   ├── review-pipeline-1.0.0/
│       │   ├── top_paper_search-1.0.0/
│       │   ├── research_common.py
│       │   └── config/venues.toml
│       ├── agent/                  # Agent orchestration skills
│       └── media/                  # AI storyboard generation
├── src/                            # React/TypeScript frontend
│   └── App.tsx
├── tests/                          # Python unit tests (pytest)
│   ├── test_new_engines.py         # Smoke tests for 10 core modules (26 tests)
│   ├── test_agent_guardrails.py
│   ├── test_research_common.py
│   └── test_top_paper_search.py
├── scripts/
│   └── runtime_setup.py            # Deps installer (pip install fastapi uvicorn pydantic cachetools networkx)
├── docs/research/                  # Research documentation
├── docker-compose.yml
├── start.bat                       # Windows startup script
├── package.json                    # Node/React dependencies
└── tsconfig.json
```

## Architecture

### Core agent loop (`agent.py`)
The central orchestrator. On each request:
1. `SkillRouter.match()` resolves the request to a skill + model tier
2. `ModelTier` dispatch picks the appropriate model (lite/standard/advanced) based on task complexity
3. `PredictiveEngine.predict()` suggests next actions from history
4. The skill executes with the chosen model
5. `DecisionTracker` logs the full reasoning path for audit
6. `RepairHook` catches failures and retries with backoff
7. Results flow into `Memory` for persistence

### Model tier system (`model_tier.py`)
- **LITE** — cheap, fast models for simple tasks (cost weight: 0.3)
- **STANDARD** — balanced (cost weight: 1.0)
- **ADVANCED** — most capable, expensive (cost weight: 3.0)
- `TIER_MODELS[tier]` maps each tier to a list of provider model IDs
- `TIER_COST[tier]` gives relative cost weights

### Skill system
Skills are versioned packs under `pysrc/skills/<category>/<name>-<version>/`. Each has:
- `SKILL.md` — metadata, description, and instructions
- `scripts/` — implementation (typically `main.py`)
- Category-level `*_common.py` for shared utilities

`skill.py` discovers and loads skill packs. `skill_router.py` matches requests to skills using feature extraction and confidence scoring, falling back to a `RouteResult(skill_name="default", confidence=0.0)`.

### Memory & knowledge graph
- `memory.py` — high-level memory API (store, recall, search)
- `memory_ontology.py` — `Entity` (with float `timestamp` from `time.time()`), `Relation`, `Ontology` with indexed relation lookup
- `graph_engine.py` — `networkx.DiGraph` wrapper: nodes, directed edges, BFS, shortest path, subgraph extraction
- `literature_graph.py` — domain model for papers, authors, citations
- `literature_graph_db.py` — SQLite persistence with all SQL as module-level constants

### Predictive & self-improving
- `predictive_engine.py` — records (context, action, outcome) tuples, pre-computes feature sets, scores via Jaccard similarity. Returns `[]` on empty history.
- `guided_evolution.py` — mutates prompts/strategies, evaluates fitness, loops until target or `max_generations`
- `cross_category_learner.py` — extracts patterns from one domain, transfers to another
- `decision_tracker.py` — logs every decision with context, reasoning, and outcome

### Infrastructure
- `cache_engine.py` — `cachetools.TTLCache` wrapper with hit/miss tracking and `get_or_set()`
- `repair_hook.py` — retries failing operations with exponential backoff (`2^attempt` seconds), max_retries limit
- `api.py` — FastAPI app with `error_response()` helper for consistent `{"error": ..., "status": ...}` responses
- `services.py` — `ServiceRegistry` singleton; register/get by class type

## Common commands

```bash
# Run all tests (34 tests)
python -m pytest tests/ -v

# Run only new engine smoke tests (26 tests)
python -m pytest tests/test_new_engines.py -v

# Run a single test
python -m pytest tests/test_new_engines.py::TestCacheEngine::test_set_and_get -v

# Install Python dependencies
python scripts/runtime_setup.py

# Start the app (Windows)
start.bat

# Docker
docker-compose up
```

## Key dependencies

| Package | Used in | Purpose |
|---|---|---|
| `fastapi` + `uvicorn` | `api.py` | REST API server |
| `pydantic` | `api.py` | Request/response validation |
| `cachetools` | `cache_engine.py` | TTL+LRU cache backend |
| `networkx` | `graph_engine.py` | Graph algorithms (BFS, shortest path, subgraph) |

## Code conventions

- Python files use 4-space indentation, type hints throughout
- All new core modules use `dataclass` for data containers
- Timestamps: internal storage as `float` (`time.time()`), serialization as ISO string via `.to_json()`
- SQL: all statements are module-level `SQL_*` constants in `literature_graph_db.py`
- Error responses: use `error_response(message, status)` from `api.py` — never inline dicts
- Relation lookups: use indexed `find_relations(source_id, relation_type=...)` for O(1) — only omit type filter when querying all relations
- Feature extraction: call `_extract_features()` once on `record()`, store in `_feature_vectors`, use in `_compute_similarity()` — never re-extract on every `predict()`
- Retries: always use `RepairHook.execute()` with exponential backoff — never tight-loop retry
- Tests: one test file per module group, `unittest.TestCase`, `setUp` for fixtures
- Mutations: iterate over `list(collection)` copy when removing elements inside the loop

## Git workflow

- Branch: `main` (protected — work in feature branches)
- Remote: `https://github.com/GodOn514/openMegatron`
- Commits are grouped by logical package (engines, skills, integration, fixes)
- PR reviews use `/code-review` with findings posted as inline comments
