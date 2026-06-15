<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11+-blue" alt="Python">
  <img src="https://img.shields.io/badge/TypeScript-React-3178c6" alt="TypeScript">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
  <img src="https://img.shields.io/badge/tests-300+-brightgreen" alt="Tests">
</p>

OpenMegatron — 模块化多模型 AI Agent 平台。30+ 内置工具、三存储混合 RAG、伴生模型自我训练、视觉 Agent 飞轮、本体对齐的超图记忆。

[English](README.md) | [中文](README_CN.md)

## 快速开始

### 环境要求

- Python 3.11+
- Node.js 18+
- Redis（会话缓存）
- PostgreSQL + pgvector（向量存储）
- Neo4j（图数据库）

### 安装与启动

```bash
# 1. 克隆项目
git clone https://github.com/GodOn514/openMegatron.git
cd openMegatron

# 2. 安装 Python 依赖
python scripts/runtime_setup.py

# 3. 安装前端依赖
npm install

# 4. 配置模型（编辑 pysrc/model.toml，填入 API key）
# 默认使用 OpenAI，也支持 DeepSeek、本地 llama.cpp 等

# 5. 启动数据库（三选一）
docker-compose up -d          # Docker 一键启动 Redis + PostgreSQL + Neo4j
# 或者手动启动本地已安装的三个数据库

# 6. 一键启动
start.bat                     # Windows
# 或者分别启动：
python pysrc/agent.py --api   # 后端 :8000
npm run dev                   # 前端 :3000
```

浏览器打开 `http://localhost:3000`，无需登录。

## 能做什么

- **30+ 工具自由组合**：代码执行、子代理委托、记忆搜索、RAG 检索、GUI 自动化（截屏/点击/键入）、定时任务、技能市场、进化提案
- **任何模型即插即用**：自动探测模型能力（上下文窗口、工具调用、视觉支持），GPT-4、Claude、DeepSeek、本地 llama.cpp、Holo 3.1 全部兼容
- **三存储混合 RAG**：PostgreSQL/pgvector 存文档块，Neo4j 存实体图谱和多跳关系，Redis 做语义缓存
- **伴生模型自己训练自己**：每次对话自动收集轨迹 → 训练评分模型 → 每 50 条自动重训 → 自动部署
- **视觉 Agent 飞轮**：截屏 → VLM 看图决策 → 执行 GUI 动作 → 收集视觉轨迹 → 训练视觉评分模型 → DPO 微调本地小模型
- **本体对齐的记忆系统**：23 种节点 + 26 种关系 + 5 种超边。记忆、RAG、脱水器、决策追踪全部围绕统一本体论运作

## 架构速览

```
用户输入
    │
    ▼
agent.chat()
    ├── CompanionRouter  →  简单任务用本地模型，复杂任务用云端
    ├── SkillRouter      →  匹配技能 + 模型层级
    ├── 执行工具调用     →  30+ 工具可选
    ├── TrajectoryCollector → 持久化轨迹
    ├── Memory           →  Redis + PostgreSQL/pgvector + Neo4j
    └── RAG              →  文档入库 → 混合检索 → 带引用回答

训练闭环：
TrajectoryStore → reward_model → AutoRetrainLoop → 自动部署

视觉闭环：
截屏 → VLM → GUI 动作 → visual_trajectory → visual_reward → DPO → QLoRA → GGUF → 本地推理
```

## 项目结构

```
openMegatron/
├── pysrc/
│   ├── agent.py              # 核心 Agent 循环
│   ├── skill.py              # 30+ 工具
│   ├── api.py                # FastAPI + WebSocket
│   ├── model_tier.py         # 模型能力自动探测
│   ├── memory.py + ontology  # 三存储记忆 + 23 类型本体
│   ├── rag_*.py              # 混合 RAG (入库/检索/回答/同步)
│   ├── companion_*.py        # 伴生模型 (加载/路由)
│   ├── qlora_trainer.py      # QLoRA 微调
│   ├── gguf_exporter.py      # GGUF 导出
│   ├── reward_*.py           # 奖励评分 (裁判模型)
│   ├── trajectory_*.py       # 轨迹收集 + 存储
│   ├── visual_*.py           # 视觉 Agent 飞轮
│   └── skills/               # 版本化技能包
├── src/                      # React/TypeScript 前端
├── tests/                    # 300+ 测试
├── start.bat                 # 一键启动
└── docker-compose.yml
```

## 伴生模型训练（8 步闭环）

```bash
# 1. 日常使用自动收集轨迹（无需手动操作）

# 2. 导入外部日志扩充数据
python -m pysrc.openclaw_importer ~/.openclaw/sessions/

# 3. 构建 SFT + DPO 训练集
python -m pysrc.training_data_pipeline \
    --openclaw-dir ~/.openclaw/sessions/ \
    --output-dir .training_data

# 4. QLoRA 微调基座模型
python -m pysrc.qlora_trainer \
    --mode sft --dataset .training_data/sft_sharegpt.jsonl \
    --base-model Qwen/Qwen2.5-7B-Instruct \
    --output-dir .models/companion/checkpoint

# 5. 合并 LoRA + 导出 GGUF
python -m pysrc.gguf_exporter merge \
    --base-model Qwen/Qwen2.5-7B-Instruct \
    --lora-adapter .models/companion/checkpoint \
    --output .models/companion/model.gguf --quantize q4_k_m

# 6. 启动 llama.cpp
python -m pysrc.gguf_exporter launch --gguf-path .models/companion/model.gguf

# 7. 在 model.toml 启用
# [companion_model]
# enabled = true

# 8. 启动 OpenMegatron — 伴生模型自动接管简单任务
```

## 文档

- [完整技术文档 (CLAUDE.md)](CLAUDE.md)
- [英文文档](README.md)
- [GitHub](https://github.com/GodOn514/openMegatron)
