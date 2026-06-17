# OpenMegatron

OpenMegatron 是一个本地 AI Agent 工作台：可以聊天、调用工具、管理长期记忆、检索资料、执行代码任务、做 GUI 自动化，并通过任务轨迹训练“伴生模型”来改进评分、路由和自动化能力。

**Windows 用户直接运行 `start.bat`。** 启动器会自动处理 Python 环境、前端依赖、Docker 数据库、后端 API、前端页面和端口冲突。

[English](README.md) | [中文](README_CN.md)

## Quickly Start

### Windows 一键启动

```bat
start.bat
```

启动后打开终端里显示的地址，通常是：

```text
http://localhost:3000
```

如果 `3000` 被占用，启动器会自动尝试 `3001`、`3002` 和后续端口。

### 第一次配置模型

首次启动时，如果缺少 `pysrc/model.toml`，`start.ps1` 会从 `pysrc/model.example.toml` 自动创建一份。然后编辑：

```text
pysrc/model.toml
```

填入 active provider、API Key、Base URL 和模型名。配置文件支持 OpenAI 兼容接口、DeepSeek、Qwen、Moonshot、智谱、MiniMax、阶跃星辰、硅基流动、OpenRouter 以及本地兼容接口。

### 常用命令

```bat
start.bat             启动前端和后端
start.bat health      检查服务状态
start.bat stop        停止服务
start.bat install     重新安装依赖
start.bat test        运行测试
start.bat menu        打开启动菜单
```

常用参数：

```bat
start.bat -NoBrowser
start.bat -SkipDocker
start.bat -BackendPort 8001
start.bat -FrontendPort 3001
```

## 六个子系统

OpenMegatron 当前由六个主要子系统组成：

1. **交互与 API 子系统**：React/TypeScript 前端、FastAPI HTTP 接口、WebSocket/事件流、运行状态面板和外部渠道请求入口。
2. **Agent 编排与模型调度子系统**：核心 Agent 循环、服务注册表、请求规划、模型供应商配置，以及 lite/standard/advanced 三层模型路由。
3. **技能执行子系统**：`pysrc/skills/` 下的版本化技能包，覆盖代码、科研、办公、媒体、监控和 Agent 编排任务。
4. **记忆、RAG 与知识图谱子系统**：Redis 聊天历史、PostgreSQL/pgvector 检索、Neo4j 图记忆、记忆本体、文献图谱和引用式检索回答。
5. **伴生学习与评估子系统**：任务轨迹采集/导入、奖励/评分模型、自动重训钩子、回归保护、消融实验和学习仪表盘。
6. **自动化与外部集成子系统**：GUI 操作、屏幕捕获、桌面/浏览器自动化技能、飞书/企业微信类适配器、日志投喂和运行时安全检查。

## 项目结构

```text
openMegatron/
├── start.bat              Windows 一键启动入口
├── start.ps1              启动器实现
├── docker-compose.yml     Redis、PostgreSQL/pgvector、Neo4j
├── pysrc/                 Python 后端
│   ├── api.py             FastAPI API 与渠道网关
│   ├── agent.py           核心 Agent 循环
│   ├── model_tier.py      模型层级调度
│   ├── skill.py           技能发现与加载
│   ├── skill_router.py    请求到技能路由
│   ├── memory.py          记忆与 RAG 存储
│   ├── graph_engine.py    图算法
│   ├── literature_graph.py
│   ├── trajectory_*.py    轨迹采集与导入
│   ├── reward_*.py        奖励模型与训练
│   └── skills/            版本化技能包
├── src/                   React 前端
├── tests/                 Python 测试
└── docs/                  文档与图表
```

## 开发

安装运行依赖：

```bat
python scripts/runtime_setup.py --toml pysrc/model.toml
```

运行测试：

```bat
python -m pytest tests/ -v
```

构建前端：

```bat
npm run build
```

## 故障排查

查看服务状态：

```bat
start.bat health
```

查看运行日志：

```text
.runtime/
```

停止后重新启动：

```bat
start.bat stop
start.bat
```

## 许可证与署名

OpenMegatron 可免费用于个人、学术和商业用途，也允许修改和再分发，**但必须注明出处**。完整条款见 [LICENSE.txt](LICENSE.txt)。

推荐署名格式：

```text
Built with OpenMegatron by WhereIsTom520.
https://github.com/WhereIsTom520/openMegatron
DOI: https://doi.org/10.5281/zenodo.20711569
```

## DOI

Zenodo DOI：

https://doi.org/10.5281/zenodo.20711569
