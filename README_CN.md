# OpenMegatron

OpenMegatron 是一个本地 AI Agent 工作台：可以聊天、调用工具、管理长期记忆、检索资料、执行代码任务，并通过轨迹数据训练“伴生模型”来改进评分、路由和自动化能力。

最重要的一句话：**Windows 用户优先运行 `start.bat`，不要手动拆开启动。**

[English](README.md) | [中文](README_CN.md)

## 一键启动

### 1. 下载

从 GitHub Release 下载源码：

https://github.com/WhereIsTom520/openMegatron/releases/tag/v1.0.0

解压后进入 `openMegatron` 文件夹。

### 2. 配置模型

编辑：

```text
pysrc/model.toml
```

填入你的模型供应商、API Key、Base URL 和模型名。

### 3. 启动

双击：

```text
start.bat
```

或者在命令行运行：

```bat
start.bat
```

启动脚本会自动处理：

- Python 虚拟环境
- Python 依赖
- 前端依赖
- Docker 数据库
- 后端服务
- 前端页面
- 端口冲突检测

启动成功后打开：

```text
http://localhost:3000
```

如果 `3000` 被占用，脚本会自动换到 `3001`、`3002` 等端口，并在终端显示实际地址。

## 常用命令

```bat
start.bat             启动前端和后端
start.bat health      检查服务状态
start.bat stop        停止服务
start.bat install     重新安装依赖
start.bat test        运行测试
start.bat menu        打开菜单
```

高级参数：

```bat
start.bat -NoBrowser
start.bat -SkipDocker
start.bat -BackendPort 8001
start.bat -FrontendPort 3001
```

## 它能做什么

- **AI Agent 聊天工作台**：前端聊天界面 + 后端工具调用。
- **多模型接入**：支持 OpenAI 兼容接口、Claude/Opus 类接口、DeepSeek、本地 llama.cpp 等。
- **技能系统**：代码、科研、办公、媒体、Agent 编排等技能包。
- **长期记忆**：Redis + PostgreSQL/pgvector + Neo4j 的混合记忆系统。
- **RAG 检索**：文档入库、向量检索、图谱检索、引用式回答。
- **伴生模型闭环**：收集任务轨迹，训练奖励/评分模型，用于改进任务评分、路由和自动化。
- **GUI 自动化**：支持截图、点击、输入、滚动、拖拽等电脑操作能力。
- **外部日志投喂**：支持导入 Claude Code、Codex 兼容日志、OpenClaw/Hermes 轨迹。
- **评估与消融**：提供 RAG、记忆、伴生模型相关的实验脚手架。

## 伴生模型是什么

当前的伴生模型不是“直接替代 GPT/Opus 的完整大模型”，而是围绕 OpenMegatron 运行产生的数据建立本地学习闭环：

1. 收集任务轨迹、工具调用、成功/失败信号。
2. 训练奖励模型或评分器。
3. 用评分器辅助任务质量判断、模型路由、自动重训和回归检测。
4. 未来可继续接入 SFT/DPO/QLoRA 训练，把部分简单任务交给本地模型。

也就是说，它的价值是让系统越用越懂你的任务习惯，而不是一开始就宣称可以完全替代云端大模型。

## 项目结构

```text
openMegatron/
├── start.bat              Windows 一键启动入口
├── start.ps1              启动脚本实现
├── pysrc/                 Python 后端
│   ├── agent.py           核心 Agent 循环
│   ├── skill.py           工具与技能注册
│   ├── memory.py          长期记忆与 RAG 数据库
│   ├── reward_*.py        奖励模型与训练
│   ├── trajectory_*.py    轨迹采集与导入
│   └── skills/            技能包
├── src/                   React 前端
├── tests/                 Python 测试
└── docker-compose.yml     Redis/PostgreSQL/Neo4j
```

## 故障排查

查看服务状态：

```bat
start.bat health
```

查看日志：

```text
.runtime/
```

停止后重新启动：

```bat
start.bat stop
start.bat
```

## DOI

Zenodo DOI：

https://doi.org/10.5281/zenodo.20711569

