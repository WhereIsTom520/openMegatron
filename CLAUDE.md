# OpenMegatron

[English](#openmegatron) | [中文](#openmegatron-中文)

OpenMegatron — modular, multi-model AI agent platform with skill routing, memory ontology, predictive engine, literature knowledge graph, Tri-Store Hybrid RAG, companion model system, and visual agent flywheel.

## Project layout

```
openMegatron/
├── pysrc/                                    # Python backend (primary codebase)
│   ├── agent.py                              # Core agent loop — orchestrates all engines
│   ├── memory.py                             # Memory subsystem — persistence + retrieval
│   ├── skill.py                              # Skill loader — discovers and loads skill packs
│   ├── api.py                                # FastAPI REST + WebSocket endpoints
│   ├── services.py                           # Service facades (Memory, Runtime, Context, Skills...)
│   ├── model_tier.py                         # Capability-based model tier dispatch
│   ├── skill_router.py                       # Request-to-skill matching and routing
│   │
│   ├── # ── Memory & Ontology ──
│   ├── memory_ontology.py                    # Ontology: 23 node types + 26 relation types + 5 hyperedge types
│   ├── graph_engine.py                       # Neo4j graph operations (upsert, BFS, PageRank, community)
│   ├── literature_graph.py                   # Academic literature knowledge graph
│   ├── literature_graph_db.py                # SQLite persistence for literature graph
│   ├── cache_engine.py                       # Redis caching (graph queries, vector search, pub/sub)
│   │
│   ├── # ── Tri-Store Hybrid RAG ──
│   ├── rag_ingest.py                         # Document parsing (PDF/Office/HTML/Code) → chunk → embed → store
│   ├── rag_retrieval.py                      # Hybrid retrieval: local (PG vector+fulltext) + global (Neo4j graph) + fused
│   ├── rag_answer.py                         # Answer generation with citations
│   ├── rag_sync.py                           # Incremental Neo4j sync + community detection + cache invalidation
│   │
│   ├── # ── Self-Evolution ──
│   ├── predictive_engine.py                  # Pre-flight validation + multi-strategy exploration
│   ├── guided_evolution.py                   # Enactive-AI: Reactive→Predictive→Exploration→Autonomous
│   ├── cross_category_learner.py             # Cross-domain knowledge transfer
│   ├── decision_tracker.py                   # Ontology-guided hypergraph decision audit
│   ├── repair_hook.py                        # Auto-retry with exponential backoff
│   │
│   ├── # ── Companion AI System ──
│   ├── # Judge Subsystem (Reward Scoring)
│   ├── trajectory_collector.py               # Text trajectory collection (hooks agent.chat())
│   ├── trajectory_store.py                   # SQLite text trajectory persistence
│   ├── trajectory_importer.py                # Multi-source import (Codex/OpenMegatron/generic)
│   ├── claude_code_parser.py                 # Claude Code JSONL transcript parser
│   ├── reward_model.py                       # Reward scorer: RandomForest (25 features) + Torch MLP
│   ├── reward_trainer.py                     # Training pipeline: K-fold CV + baseline comparison
│   ├── reward_integration.py                 # Hot-swap agent._score_task_trace() with learned model
│   ├── auto_retrain.py                       # Online continuous learning daemon (threshold=50 trajectories)
│   ├── model_registry.py                     # SQLite model version tracking
│   ├── regression_guard.py                   # Safety checks before model deployment (F1 gate + holdout + edge cases)
│   ├── feedback_collector.py                 # Implicit feedback extraction from user messages
│   ├── eval_ab.py                            # A/B comparison: learned model vs rule-based scoring
│   ├── learning_dashboard.py                 # Learning curve tracking + milestone estimation
│   │
│   ├── # Visual Flywheel Subsystem
│   ├── screen_capture.py                     # Screenshot capture (mss/PIL/pyautogui backends)
│   ├── gui_actions.py                        # GUI automation: click/type/scroll/drag/hotkey/press/move/sleep
│   ├── visual_trajectory_collector.py        # Visual trajectory collection (screenshot pairs + actions)
│   ├── visual_trajectory_store.py            # SQLite visual trajectory + preference pair storage
│   ├── visual_reward_model.py                # Visual reward scorer: 18 features + RandomForest / ResNet18
│   ├── visual_dpo_pipeline.py                # DPO training: preference pairs → JSONL → QLoRA
│   ├── dual_system_router.py                 # Text vs Vision task classification + coordinator
│   ├── openclaw_importer.py                  # OpenClaw/Hermes log importer (text + visual trajectories)
│   ├── training_data_pipeline.py             # SFT (ShareGPT) + DPO dataset builder from 4 sources
│   │
│   ├── # Inference Companion Subsystem
│   ├── companion_model.py                    # Load/run small models for inference (llama.cpp/transformers/vLLM)
│   ├── qlora_trainer.py                      # QLoRA fine-tuning: SFT + DPO training executor
│   ├── gguf_exporter.py                      # Convert trained model to GGUF for llama.cpp
│   ├── companion_router.py                   # Auto-switch between cloud and companion models
│   │
│   ├── # ── Infrastructure ──
│   ├── task_queue.py                         # Async task queue with PostgreSQL persistence
│   ├── validator_orchestrator.py             # Unified validator system with capability matrix
│   │
│   └── skills/
│       ├── code/                             # Code engineering skill pack (5 skills)
│       ├── research/                         # Research skill pack (7 skills)
│       ├── agent/                            # Agent orchestration skills
│       │   ├── api-client-1.0.0/             # HTTP API client
│       │   ├── api-relay-1.0.0/              # one-api/new-api relay gateway client
│       │   ├── browser-automation-1.0.0/     # Browser automation
│       │   ├── desktop-control-1.0.0/        # Desktop control
│       │   ├── gui-automation-1.0.0/         # GUI automation (click/type/screenshot)
│       │   ├── file-watcher-1.0.0/           # File system watcher
│       │   ├── notification-1.0.0/           # System notifications
│       │   └── shell-session-1.0.0/          # Shell session management
│       ├── media/                            # AI storyboard generation
│       └── office/                           # Office utility skills (chart/data/file/image/text)
│
├── src/                                      # React/TypeScript frontend
│   ├── App.tsx
│   ├── components/
│   ├── types.ts
│   └── utils.tsx
│
├── tests/                                    # Python unit tests (pytest)
│   ├── test_reward_model.py                  # 32 tests — feature extraction + sklearn/torch training
│   ├── test_auto_retrain.py                  # 20 tests — model registry + retrain loop
│   ├── test_trajectory.py                    # 34 tests — trajectory store + collector + parser
│   ├── test_eval.py                          # 24 tests — feedback + A/B + dashboard + regression guard
│   ├── test_new_engines.py                   # 26 tests — smoke tests for core engines
│   ├── test_agent_guardrails.py
│   ├── test_research_common.py
│   └── test_top_paper_search.py
│
├── scripts/
│   ├── migrate_ontology.py                   # Ontology data migration (Neo4j alignment)
│   ├── eval_harness.py                       # Standardized coding benchmark suite
│   ├── validate_config.py                    # Pre-startup config validator
│   ├── runtime_setup.py                      # Deps installer
│   └── data_admin.py                         # CLI admin for memory data clearing
│
├── .models/                                  # Trained model storage (created at runtime)
│   ├── reward/                               # Judge reward model checkpoints (.pkl / .pt)
│   └── companion/                            # Companion model checkpoints + GGUF files
│
├── .trajectory/                              # Trajectory storage (created at runtime)
│   ├── trajectories.db                       # Text trajectory SQLite database
│   ├── visual_trajectories.db                # Visual trajectory SQLite database
│   └── screenshots/                          # Saved screenshots from GUI automation
│
├── .training_data/                           # Exported training datasets (created at runtime)
│   ├── sft_sharegpt.jsonl                    # SFT data for LLaMA-Factory / Unsloth
│   └── dpo_pairs.jsonl                       # DPO data for trl / Unsloth
│
├── .blackboard/                              # Task blackboard persistence
├── docs/research/                            # Research documentation
├── docker-compose.yml
├── start.bat                                 # Windows startup script
├── package.json                              # Node/React dependencies
└── tsconfig.json
```

## Architecture

### Core agent loop (`agent.py`)
The central orchestrator. On each request:
1. `SkillRouter` resolves the request to a skill + model tier
2. `ModelTier` dispatch picks the appropriate model based on capability probing
3. `PredictiveEngine.predict()` suggests next actions from history
4. The skill executes with the chosen model
5. `DecisionTracker` logs the full reasoning path for audit
6. `RepairHook` catches failures and retries with backoff
7. Results flow into `Memory` for persistence
8. `TrajectoryCollector` persists the task trace for companion model training
9. `CompanionRouter` checks if a local companion model can handle the request first

### Model tier system (`model_tier.py`)
- **Capability-probed**: unknown models are tested for context window, JSON mode, and tool calling accuracy — no hardcoded model lists
- **LITE** — cheap, fast models for simple tasks
- **STANDARD** — balanced
- **ADVANCED** — most capable, expensive
- **Vision support**: `_has_vision_support()` auto-detects vision-capable models (Holo, GPT-4V, Qwen-VL) and injects screenshots

### Skill system
Skills are versioned packs under `pysrc/skills/<category>/<name>-<version>/`. Each has:
- `SKILL.md` — metadata, description, and instructions
- `scripts/` — implementation (typically `main.py`)
- Category-level `*_common.py` for shared utilities

30+ built-in tools including: memory search, code execution, subagent delegation, GUI automation, RAG search, skill marketplace, cron scheduling, evolution proposals.

### Memory & knowledge graph
- **Tri-store architecture**: Redis (session/cache) + PostgreSQL/pgvector (vector search) + Neo4j (graph traversals)
- `memory_ontology.py` — Canonical ontology: 23 node types, 26 relation types, 5 hyperedge types. All systems aligned via `kind:sha1_12` ID format
- `graph_engine.py` — Neo4j operations: upsert, BFS, shortest path, PageRank, community detection
- `memory.py` — High-level API: episodic memory, workflow patterns, ontology nodes, hyperedges, RAG tables

### Tri-Store Hybrid RAG
Combines the best of traditional RAG, Microsoft GraphRAG, and LightRAG:
- **PostgreSQL**: document chunks with pgvector HNSW + full-text search + ACL filtering
- **Neo4j**: entity graph + community detection + multi-hop traversal
- **Redis**: semantic query cache for sub-ms repeated queries
- **Query router**: auto-classifies local (fact lookup) vs global (relation/synthesis) vs fused queries
- **Deterministic NER first**: regex entities → LLM only for ambiguous cases (cuts GraphRAG token cost ~80%)

## Companion AI System

The companion AI system trains a local small model to gradually replace the cloud model for simple tasks, reducing cost and latency while maintaining quality through continuous learning.

### Architecture Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                  Companion AI System Architecture                 │
├─────────────────┬──────────────────┬─────────────────────────────┤
│  Judge System    │  Visual Flywheel  │  Inference Companion       │
│  (Scoring+Retrain)│ (GUI+Vision)     │  (Train+Deploy+Route)     │
├─────────────────┼──────────────────┼─────────────────────────────┤
│ 12 files ~120KB │ 10 files ~80KB   │ 5 files ~80KB              │
└─────────────────┴──────────────────┴─────────────────────────────┘
```

### Judge Subsystem — Reward Scoring Pipeline

Learns to evaluate agent performance from trajectory data. Continuously retrains and auto-deploys.

```
agent.chat() → TrajectoryCollector → TrajectoryStore (SQLite)
    → extract_features (25-dim) → RewardScorer (RandomForest/MLP)
    → AutoRetrainLoop (threshold=50) → RegressionGuard → ModelRegistry
    → RewardIntegration → hot-swap agent._score_task_trace()
```

| File | Lines | Purpose |
|---|---|---|
| `trajectory_collector.py` | ~150 | Collects (state, action, reward) triples from agent.chat() |
| `trajectory_store.py` | ~250 | SQLite persistence for text trajectories |
| `trajectory_importer.py` | ~400 | Multi-source import (Codex, OpenMegatron, custom formats) |
| `claude_code_parser.py` | ~340 | Parses Claude Code JSONL transcripts into trajectories |
| `reward_model.py` | ~500 | 25-dim features → RandomForest (100 trees) / Torch MLP → [0,1] score |
| `reward_trainer.py` | ~240 | K-fold CV + baseline comparison + CLI |
| `reward_integration.py` | ~200 | Hot-swaps agent._score_task_trace() with trained model |
| `auto_retrain.py` | ~450 | Daemon: every 50 new trajectories → retrain → evaluate → deploy |
| `model_registry.py` | ~150 | SQLite version tracking + auto best-model selection |
| `regression_guard.py` | ~200 | F1 gate + holdout test + edge case checks before deployment |
| `feedback_collector.py` | ~250 | Implicit feedback: regex patterns detect corrections/thanks/retries |
| `eval_ab.py` | ~280 | Statistical comparison: model vs rule-based scoring |
| `learning_dashboard.py` | ~240 | Learning curve tracking + milestone estimation |

### Visual Flywheel Subsystem — GUI Automation + Vision Scoring

Enables the agent to see screens and control GUI. Collects visual trajectories to train vision reward models.

```
screen_capture.py → screenshot (base64 data URI)
gui_actions.py    → click/type/scroll/drag/hotkey/press/move/sleep
    │
    ▼
agent.py (multimodal: _has_vision_support, _inject_screenshot)
skill.py (ScreenshotTool + ExecuteGUIActionTool)
    │
    ▼
visual_trajectory_collector.py → (screenshot_before, action, screenshot_after) triples
    │
    ▼
visual_trajectory_store.py → SQLite + preference pair builder
    │
    ▼
visual_reward_model.py → 18-dim features + RandomForest / ResNet18 → score
    │
    ▼
visual_dpo_pipeline.py → DPO JSONL export + QLoRA training launcher
    │
    ▼
dual_system_router.py → classifies task as text/vision/hybrid + coordinates both systems
```

| File | Lines | Purpose |
|---|---|---|
| `screen_capture.py` | ~130 | Screenshot: mss/PIL/pyautogui backends, fullscreen/region, base64 output |
| `gui_actions.py` | ~210 | 8 GUI actions: click/type/scroll/drag/hotkey/press/move/sleep + sequence |
| `visual_trajectory_collector.py` | ~200 | Records (screenshot_before, action, screenshot_after) per step |
| `visual_trajectory_store.py` | ~320 | 3 SQLite tables: trajectories/steps/preference_pairs, DPO export |
| `visual_reward_model.py` | ~320 | Lightweight: 18 features+RandomForest. Vision: ResNet18+classifier head |
| `visual_dpo_pipeline.py` | ~250 | Builds DPO pairs → exports JSONL → launches QLoRA training |
| `dual_system_router.py` | ~230 | Keyword-based text/vision/hybrid task classification + coordinator |
| `openclaw_importer.py` | ~380 | Parses OpenClaw/Hermes JSONL + plain-text logs → text + visual trajectories |
| `training_data_pipeline.py` | ~380 | Builds SFT (ShareGPT) + DPO datasets from 4 sources |
| gui-automation skill | ~6 KB | Skill pack: SKILL.md manifest + main.py entry point |

### Inference Companion Subsystem — Train → Deploy → Replace

Trains a real small language model (Qwen, Llama, Holo) on agent trajectory data. Deploys it as a local inference server. Auto-switches between cloud and companion models.

```
training_data_pipeline.py → SFT (ShareGPT) + DPO JSONL
    │
    ▼
qlora_trainer.py → 4-bit QLoRA fine-tuning (SFT or DPO mode)
    │
    ▼
gguf_exporter.py → merge LoRA adapter → quantize → export GGUF
    │
    ▼
llama.cpp → llama-server -m model.gguf --port 1234
    │
    ▼
companion_model.py → load model (llama.cpp/transformers/vLLM backends)
    │
    ▼
companion_router.py → task complexity → routing decision
    │   simple task → companion (save cost)
    │   complex task → cloud (ensure quality)
    │   companion fails → auto fallback to cloud
    │
    ▼
agent.py chat() → companion-first routing integrated
```

| File | Lines | Key Capabilities |
|---|---|---|
| `companion_model.py` | ~350 | `CompanionModelLoader`: auto-discover checkpoints, load (llama.cpp/transformers/vLLM), generate, unload |
| `qlora_trainer.py` | ~420 | `QLoRATrainer`: train_sft, train_dpo, continue_training, evaluate. 4-bit QLoRA, LoRA merge |
| `gguf_exporter.py` | ~380 | `GGUFExporter`: HF→GGUF conversion, LoRA merge+export, quantization (q4_0/q4_k_m/q5_k_m/q8_0/f16), launch script generator |
| `companion_router.py` | ~360 | `CompanionRouter`: task complexity estimation, cloud/companion routing, companion-first strategy, auto-fallback, cost tracking |
| `agent.py` (integration) | ~30 lines | `_init_companion_model()`, companion-first check in chat() loop |

### Full Closed Loop

```
agent.chat() ←────────────────────────────────────────┐
    │                                                   │
    ├── simple task → companion_router → local model    │
    ├── complex task → cloud model (GPT-4/Claude)       │
    └── all tasks → trajectory collection               │
    │                                                   │
    ▼                                                   │
TrajectoryStore (text) + VisualTrajectoryStore (visual) │
    │                                                   │
    ▼                                                   │
training_data_pipeline → SFT + DPO datasets             │
    │                                                   │
    ▼                                                   │
qlora_trainer → QLoRA fine-tuning                       │
    │                                                   │
    ▼                                                   │
gguf_exporter → GGUF for llama.cpp                      │
    │                                                   │
    ▼                                                   │
companion_model → deploy to agent.chat() ───────────────┘
```

### Companion AI Quick Start

```bash
# Step 1: Accumulate training data (automatic — agent collects every chat)

# Step 2: Import external logs for more data
python -m pysrc.openclaw_importer ~/.openclaw/sessions/

# Step 3: Build training datasets
python -m pysrc.training_data_pipeline \
    --openclaw-dir ~/.openclaw/sessions/ \
    --claude-code-dir ~/.claude/projects/ \
    --output-dir .training_data

# Step 4: QLoRA fine-tune a base model
python -m pysrc.qlora_trainer \
    --mode sft \
    --dataset .training_data/sft_sharegpt.jsonl \
    --base-model Qwen/Qwen2.5-7B-Instruct \
    --output-dir .models/companion/checkpoint

# Step 5: Export to GGUF for llama.cpp
python -m pysrc.gguf_exporter merge \
    --base-model Qwen/Qwen2.5-7B-Instruct \
    --lora-adapter .models/companion/checkpoint \
    --output .models/companion/model.gguf \
    --quantize q4_k_m

# Step 6: Generate and run launch script
python -m pysrc.gguf_exporter launch --gguf-path .models/companion/model.gguf
./start_companion_model.sh   # or start_companion_model.bat on Windows

# Step 7: Enable in model.toml
# [companion_model]
# enabled = true

# Step 8: Start OpenMegatron — companion model auto-takes simple tasks
```

### Self-improving
- `predictive_engine.py` — Pre-flight validation before skill execution
- `guided_evolution.py` — Enactive-AI: Reactive→Predictive→Exploration→Autonomous per skill category
- `cross_category_learner.py` — Propagates failure patterns across categories
- `decision_tracker.py` — Ontology-aligned hypergraph decision audit

### Infrastructure
- `api.py` — FastAPI: `/chat`, `/rag/*`, `/memory/*`, `/skills/*`, `/evolution/*`, WebSocket for telemetry
- `services.py` — Service facades: MemoryService, RuntimeService, ContextService, SkillsService, etc.
- `cache_engine.py` — Redis: graph query cache (TTL 300s), vector search cache (TTL 600s), pub/sub, rate limiting
- `repair_hook.py` — Retries failing operations with exponential backoff
- `task_queue.py` — Async task queue with PostgreSQL persistence, priority scheduling, concurrency control

## Common commands

```bash
# Run all tests
python -m pytest tests/ -v --ignore=tests/test_runtime_setup.py

# Run companion model tests
python -m pytest tests/test_reward_model.py tests/test_auto_retrain.py tests/test_trajectory.py tests/test_eval.py -v

# Import OpenClaw logs as training data
python -m pysrc.openclaw_importer ~/.openclaw/sessions/

# Build SFT + DPO training datasets
python -m pysrc.training_data_pipeline --openclaw-dir ~/.openclaw/sessions/ --output-dir .training_data

# Train a companion model
python -m pysrc.qlora_trainer --mode sft --dataset .training_data/sft_sharegpt.jsonl --base-model Qwen/Qwen2.5-7B-Instruct

# Export to GGUF
python -m pysrc.gguf_exporter merge --base-model Qwen/Qwen2.5-7B-Instruct --lora-adapter .models/companion/checkpoint --output model.gguf

# Run ontology migration (Neo4j data alignment)
python scripts/migrate_ontology.py --apply

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
| `redis` | `services.py`, `cache_engine.py` | Session state, caching, pub/sub |
| `asyncpg` + `pgvector` | `memory.py`, `rag_*.py` | PostgreSQL vector search |
| `neo4j` | `graph_engine.py`, `memory.py` | Graph traversals, entity storage |
| `sentence-transformers` | `memory.py`, `rag_ingest.py` | Embedding generation |
| `scikit-learn` | `reward_model.py`, `visual_reward_model.py` | Reward model training |
| `torch` | `reward_model.py`, `visual_reward_model.py`, `qlora_trainer.py` | MLP + ResNet + QLoRA training |
| `openai` | `agent.py`, `rag_answer.py`, `companion_model.py` | LLM API client |
| `numpy` | All ML modules | Feature computation |
| `pypdf`, `python-pptx`, `openpyxl`, `pandas`, `beautifulsoup4` | `rag_ingest.py` | Document parsing |
| `apscheduler` | `agent.py` | Cron task scheduling |
| `peft`, `bitsandbytes`, `datasets`, `accelerate` | `qlora_trainer.py` | QLoRA fine-tuning (optional) |
| `trl` | `qlora_trainer.py` | DPO training (optional) |

## Code conventions

- Python files use 4-space indentation, type hints throughout
- All new core modules use `dataclass` for data containers
- Timestamps: internal storage as `float` (`time.time()`), serialization as ISO string
- SQL: all statements are inline or module-level constants
- Error responses: use `error_response(message, status)` from `api.py`
- Relation lookups: use indexed `find_relations(source_id, relation_type=...)` for O(1)
- Feature extraction: call `_extract_features()` once, store in `_feature_vectors`, use in `_compute_similarity()`
- Retries: always use `RepairHook.execute()` with exponential backoff
- Tests: one test file per module group, `unittest.TestCase`, `setUp` for fixtures
- Mutations: iterate over `list(collection)` copy when removing elements inside the loop
- Ontology: all node kinds and relation types must be defined in `memory_ontology.py`; writes validate against ontology (warn on unknown)
- ID format: `kind:sha1_12` throughout — `memory_ontology.ontology_node_id()` is the single source of truth

## Git workflow

- Branch: `main` (protected — work in feature branches)
- Remote: `https://github.com/GodOn514/openMegatron`
- Commits are grouped by logical package (engines, skills, integration, fixes)
- PR reviews use `/code-review` with findings posted as inline comments

---

# OpenMegatron 中文

[English](#openmegatron) | [中文](#openmegatron-中文)

OpenMegatron — 模块化多模型 AI Agent 平台，集成技能路由、记忆本体论、预测引擎、文献知识图谱、三存储混合 RAG、伴生 AI 系统、视觉 Agent 飞轮。

## 项目布局

参见上方 [Project layout](#project-layout)。

## 架构

### 核心 Agent 循环 (`agent.py`)
中央编排器。每次请求：
1. `SkillRouter` 将请求解析为技能 + 模型层级
2. `ModelTier` 基于能力探测选择合适模型
3. `PredictiveEngine.predict()` 从历史中建议下一步行动
4. 技能使用选定模型执行
5. `DecisionTracker` 记录完整推理路径用于审计
6. `RepairHook` 捕获失败并以指数退避重试
7. 结果流入 `Memory` 持久化
8. `TrajectoryCollector` 持久化任务轨迹用于伴生模型训练
9. `CompanionRouter` 检查本地伴生模型是否能先处理请求

### 模型分层系统 (`model_tier.py`)
- **能力探测式**：未知模型通过测试上下文窗口、JSON 模式、工具调用准确率自动分级——不硬编码模型列表
- **LITE** — 廉价快速模型，适合简单任务
- **STANDARD** — 平衡型
- **ADVANCED** — 最强能力，最贵
- **视觉支持**：`_has_vision_support()` 自动检测视觉模型（Holo、GPT-4V、Qwen-VL）并注入截图

### 技能系统
技能是 `pysrc/skills/<category>/<name>-<version>/` 下的版本化包。每个包含：
- `SKILL.md` — 元数据、描述和指令
- `scripts/` — 实现（通常为 `main.py`）
- 类别级 `*_common.py` 共享工具

30+ 内置工具：记忆搜索、代码执行、子代理委托、GUI 自动化、RAG 搜索、技能市场、定时任务、进化提案。

### 记忆与知识图谱
- **三存储架构**：Redis（会话/缓存）+ PostgreSQL/pgvector（向量搜索）+ Neo4j（图遍历）
- `memory_ontology.py` — 规范本体：23 种节点类型 + 26 种关系类型 + 5 种超边类型。所有系统通过 `kind:sha1_12` ID 格式对齐
- `graph_engine.py` — Neo4j 操作：upsert、BFS、最短路径、PageRank、社区检测
- `memory.py` — 高层 API：情景记忆、工作流模式、本体节点、超边、RAG 表

### 三存储混合 RAG
结合传统 RAG、Microsoft GraphRAG 和 LightRAG 的优点：
- **PostgreSQL**：文档分块 + pgvector HNSW + 全文搜索 + ACL 过滤
- **Neo4j**：实体图谱 + 社区检测 + 多跳遍历
- **Redis**：语义查询缓存（亚毫秒重复查询响应）
- **查询路由器**：自动分类 local（事实查找）/ global（关系综合）/ fused 查询
- **确定性 NER 优先**：正则实体提取 → 仅对模糊案例使用 LLM（降低 GraphRAG token 成本 ~80%）

## 伴生 AI 系统

伴生 AI 系统训练本地小模型逐步替代云端大模型处理简单任务，降低成本和延迟，同时通过持续学习保持质量。

### 架构总览

```
┌──────────────────────────────────────────────────────────────────┐
│                    伴生 AI 系统架构                               │
├─────────────────┬──────────────────┬─────────────────────────────┤
│  裁判系统        │  视觉飞轮         │  推理伴生模型                │
│  (评分+重训)     │  (GUI+视觉评分)   │  (训练+部署+路由+切换)       │
├─────────────────┼──────────────────┼─────────────────────────────┤
│ 12 文件 ~120KB  │ 10 文件 ~80KB    │ 5 文件 ~80KB               │
└─────────────────┴──────────────────┴─────────────────────────────┘
```

### 裁判子系统 — 奖励评分管线

从轨迹数据中学习评估 Agent 表现。持续重训练并自动部署。

```
agent.chat() → TrajectoryCollector → TrajectoryStore (SQLite)
    → extract_features (25维) → RewardScorer (RandomForest/MLP)
    → AutoRetrainLoop (阈值=50) → RegressionGuard → ModelRegistry
    → RewardIntegration → 热替换 agent._score_task_trace()
```

| 文件 | 行数 | 功能 |
|---|---|---|
| `trajectory_collector.py` | ~150 | 从 agent.chat() 收集 (state, action, reward) 三元组 |
| `trajectory_store.py` | ~250 | 文本轨迹 SQLite 持久化 |
| `trajectory_importer.py` | ~400 | 多源导入（Codex、OpenMegatron、自定义格式） |
| `claude_code_parser.py` | ~340 | 解析 Claude Code JSONL 转录为轨迹 |
| `reward_model.py` | ~500 | 25维特征 → RandomForest (100棵树) / Torch MLP → [0,1] 评分 |
| `reward_trainer.py` | ~240 | K-fold 交叉验证 + 基线对比 + CLI |
| `reward_integration.py` | ~200 | 用训练好的模型热替换 agent._score_task_trace() |
| `auto_retrain.py` | ~450 | 守护进程：每累积50条新轨迹 → 重训练 → 评估 → 部署 |
| `model_registry.py` | ~150 | SQLite 版本追踪 + 自动选最优模型 |
| `regression_guard.py` | ~200 | F1 门禁 + holdout 测试 + 边缘案例检查 |
| `feedback_collector.py` | ~250 | 隐式反馈：正则匹配检测纠正/感谢/重试 |
| `eval_ab.py` | ~280 | 统计对比：学习模型 vs 规则评分 |
| `learning_dashboard.py` | ~240 | 学习曲线追踪 + 里程碑预估 |

### 视觉飞轮子系统 — GUI 自动化 + 视觉评分

让 Agent 能看屏幕、操作 GUI。收集视觉轨迹训练视觉评分模型。

```
screen_capture.py → 截屏 (base64 data URI)
gui_actions.py    → click/type/scroll/drag/hotkey/press/move/sleep
    │
    ▼
agent.py (多模态: _has_vision_support, _inject_screenshot)
skill.py (ScreenshotTool + ExecuteGUIActionTool)
    │
    ▼
visual_trajectory_collector.py → (截图前, 动作, 截图后) 三元组
    │
    ▼
visual_trajectory_store.py → SQLite + 偏好对构建
    │
    ▼
visual_reward_model.py → 18维特征 + RandomForest / ResNet18 → 评分
    │
    ▼
visual_dpo_pipeline.py → DPO JSONL 导出 + QLoRA 训练启动器
    │
    ▼
dual_system_router.py → 文本/视觉/混合任务分类 + 双系统协调器
```

| 文件 | 行数 | 功能 |
|---|---|---|
| `screen_capture.py` | ~130 | 截屏：mss/PIL/pyautogui 三后端，全屏/区域，base64 输出 |
| `gui_actions.py` | ~210 | 8种 GUI 动作：click/type/scroll/drag/hotkey/press/move/sleep + 序列 |
| `visual_trajectory_collector.py` | ~200 | 每步记录 (截图前, 动作, 截图后) |
| `visual_trajectory_store.py` | ~320 | 3张 SQLite 表：trajectories/steps/preference_pairs，DPO 导出 |
| `visual_reward_model.py` | ~320 | 轻量级：18特征+RandomForest。视觉：ResNet18+分类头 |
| `visual_dpo_pipeline.py` | ~250 | 构建 DPO 偏好对 → 导出 JSONL → 启动 QLoRA 训练 |
| `dual_system_router.py` | ~230 | 关键词文本/视觉/混合任务分类 + 协调器 |
| `openclaw_importer.py` | ~380 | 解析 OpenClaw/Hermes JSONL + 纯文本日志 → 文本+视觉轨迹 |
| `training_data_pipeline.py` | ~380 | 从4源构建 SFT (ShareGPT) + DPO 数据集 |
| gui-automation skill | ~6 KB | 技能包：SKILL.md 清单 + main.py 入口 |

### 推理伴生子系统 — 训练 → 部署 → 替代

在 Agent 轨迹数据上训练真正的小语言模型（Qwen、Llama、Holo）。部署为本地推理服务。云端和伴生模型之间自动切换。

```
training_data_pipeline.py → SFT (ShareGPT) + DPO JSONL
    │
    ▼
qlora_trainer.py → 4-bit QLoRA 微调 (SFT 或 DPO 模式)
    │
    ▼
gguf_exporter.py → 合并 LoRA 适配器 → 量化 → 导出 GGUF
    │
    ▼
llama.cpp → llama-server -m model.gguf --port 1234
    │
    ▼
companion_model.py → 加载模型 (llama.cpp/transformers/vLLM 三种后端)
    │
    ▼
companion_router.py → 任务复杂度 → 路由决策
    │   简单任务 → 伴生模型 (省钱)
    │   复杂任务 → 云端模型 (保质量)
    │   伴生失败 → 自动 fallback 到云端
    │
    ▼
agent.py chat() → companion-first 路由已集成
```

| 文件 | 行数 | 核心能力 |
|---|---|---|
| `companion_model.py` | ~350 | `CompanionModelLoader`：自动发现检查点，加载(llama.cpp/transformers/vLLM)，生成，卸载 |
| `qlora_trainer.py` | ~420 | `QLoRATrainer`：train_sft, train_dpo, continue_training, evaluate。4-bit QLoRA，LoRA 合并 |
| `gguf_exporter.py` | ~380 | `GGUFExporter`：HF→GGUF 转换，LoRA 合并+导出，量化(q4_0/q4_k_m/q5_k_m/q8_0/f16)，启动脚本生成 |
| `companion_router.py` | ~360 | `CompanionRouter`：任务复杂度评估，云/伴生路由，companion-first 策略，自动 fallback，成本追踪 |
| `agent.py` (集成) | ~30行 | `_init_companion_model()`，chat() 循环中的 companion-first 检查 |

### 完整闭环

```
agent.chat() ←────────────────────────────────────────┐
    │                                                   │
    ├── 简单任务 → companion_router → 本地模型           │
    ├── 复杂任务 → 云端模型 (GPT-4/Claude)               │
    └── 所有任务 → 轨迹收集                              │
    │                                                   │
    ▼                                                   │
TrajectoryStore (文本) + VisualTrajectoryStore (视觉)    │
    │                                                   │
    ▼                                                   │
training_data_pipeline → SFT + DPO 数据集                │
    │                                                   │
    ▼                                                   │
qlora_trainer → QLoRA 微调                              │
    │                                                   │
    ▼                                                   │
gguf_exporter → llama.cpp 可用的 GGUF                   │
    │                                                   │
    ▼                                                   │
companion_model → 部署到 agent.chat() ──────────────────┘
```

### 伴生 AI 快速开始

```bash
# 步骤 1: 积累训练数据（自动进行——agent 每次对话都会收集轨迹）

# 步骤 2: 导入外部日志获取更多数据
python -m pysrc.openclaw_importer ~/.openclaw/sessions/

# 步骤 3: 构建训练数据集
python -m pysrc.training_data_pipeline \
    --openclaw-dir ~/.openclaw/sessions/ \
    --claude-code-dir ~/.claude/projects/ \
    --output-dir .training_data

# 步骤 4: QLoRA 微调基座模型
python -m pysrc.qlora_trainer \
    --mode sft \
    --dataset .training_data/sft_sharegpt.jsonl \
    --base-model Qwen/Qwen2.5-7B-Instruct \
    --output-dir .models/companion/checkpoint

# 步骤 5: 导出 GGUF 给 llama.cpp 使用
python -m pysrc.gguf_exporter merge \
    --base-model Qwen/Qwen2.5-7B-Instruct \
    --lora-adapter .models/companion/checkpoint \
    --output .models/companion/model.gguf \
    --quantize q4_k_m

# 步骤 6: 生成并运行启动脚本
python -m pysrc.gguf_exporter launch --gguf-path .models/companion/model.gguf
./start_companion_model.sh   # Windows 上运行 start_companion_model.bat

# 步骤 7: 在 model.toml 中启用
# [companion_model]
# enabled = true

# 步骤 8: 启动 OpenMegatron — 伴生模型自动接管简单任务
```

### 自进化
- `predictive_engine.py` — 技能执行前的预检验证
- `guided_evolution.py` — Enactive-AI：反应式→预测式→探索式→自主式，每个技能类别独立进化
- `cross_category_learner.py` — 跨类别传播失败模式
- `decision_tracker.py` — 本体对齐的超图决策审计

### 基础设施
- `api.py` — FastAPI：`/chat`、`/rag/*`、`/memory/*`、`/skills/*`、`/evolution/*`，WebSocket 遥测
- `services.py` — 服务门面：MemoryService、RuntimeService、ContextService、SkillsService 等
- `cache_engine.py` — Redis：图查询缓存（TTL 300s）、向量搜索缓存（TTL 600s）、pub/sub、速率限制
- `repair_hook.py` — 指数退避重试失败操作
- `task_queue.py` — 异步任务队列，PostgreSQL 持久化，优先级调度，并发控制

## 常用命令

```bash
# 运行全部测试
python -m pytest tests/ -v --ignore=tests/test_runtime_setup.py

# 运行伴生模型测试
python -m pytest tests/test_reward_model.py tests/test_auto_retrain.py tests/test_trajectory.py tests/test_eval.py -v

# 导入 OpenClaw 日志作为训练数据
python -m pysrc.openclaw_importer ~/.openclaw/sessions/

# 构建 SFT + DPO 训练数据集
python -m pysrc.training_data_pipeline --openclaw-dir ~/.openclaw/sessions/ --output-dir .training_data

# 训练伴生模型
python -m pysrc.qlora_trainer --mode sft --dataset .training_data/sft_sharegpt.jsonl --base-model Qwen/Qwen2.5-7B-Instruct

# 导出 GGUF
python -m pysrc.gguf_exporter merge --base-model Qwen/Qwen2.5-7B-Instruct --lora-adapter .models/companion/checkpoint --output model.gguf

# 运行本体迁移（Neo4j 数据对齐）
python scripts/migrate_ontology.py --apply

# 安装 Python 依赖
python scripts/runtime_setup.py

# 启动应用 (Windows)
start.bat

# Docker
docker-compose up
```

## 关键依赖

| 包 | 使用位置 | 用途 |
|---|---|---|
| `fastapi` + `uvicorn` | `api.py` | REST API 服务器 |
| `pydantic` | `api.py` | 请求/响应验证 |
| `redis` | `services.py`, `cache_engine.py` | 会话状态、缓存、pub/sub |
| `asyncpg` + `pgvector` | `memory.py`, `rag_*.py` | PostgreSQL 向量搜索 |
| `neo4j` | `graph_engine.py`, `memory.py` | 图遍历、实体存储 |
| `sentence-transformers` | `memory.py`, `rag_ingest.py` | 嵌入生成 |
| `scikit-learn` | `reward_model.py`, `visual_reward_model.py` | 奖励模型训练 |
| `torch` | `reward_model.py`, `visual_reward_model.py`, `qlora_trainer.py` | MLP + ResNet + QLoRA 训练 |
| `openai` | `agent.py`, `rag_answer.py`, `companion_model.py` | LLM API 客户端 |
| `numpy` | 所有 ML 模块 | 特征计算 |
| `pypdf`, `python-pptx`, `openpyxl`, `pandas`, `beautifulsoup4` | `rag_ingest.py` | 文档解析 |
| `apscheduler` | `agent.py` | 定时任务调度 |
| `peft`, `bitsandbytes`, `datasets`, `accelerate` | `qlora_trainer.py` | QLoRA 微调（可选） |
| `trl` | `qlora_trainer.py` | DPO 训练（可选） |

## 代码规范

- Python 文件使用 4 空格缩进，全类型标注
- 所有新核心模块使用 `dataclass` 作为数据容器
- 时间戳：内部存储为 `float`（`time.time()`），序列化为 ISO 字符串
- SQL：所有语句内联或模块级常量
- 错误响应：使用 `api.py` 的 `error_response(message, status)`
- 关系查找：使用索引的 `find_relations(source_id, relation_type=...)` 实现 O(1)
- 特征提取：调用一次 `_extract_features()`，存储在 `_feature_vectors` 中，在 `_compute_similarity()` 中使用
- 重试：始终使用 `RepairHook.execute()` 配合指数退避
- 测试：每个模块组一个测试文件，`unittest.TestCase`，`setUp` 用于夹具
- 变更：在循环内移除元素时迭代 `list(collection)` 副本
- 本体：所有节点类型和关系类型必须在 `memory_ontology.py` 中定义；写入时验证本体（未知类型 warn）
- ID 格式：全局统一 `kind:sha1_12` — `memory_ontology.ontology_node_id()` 是唯一来源

## Git 工作流

- 分支：`main`（受保护 — 在功能分支中工作）
- 远程：`https://github.com/GodOn514/openMegatron`
- 提交按逻辑包分组（引擎、技能、集成、修复）
- PR 审查使用 `/code-review`，结果以行内评论形式发布
