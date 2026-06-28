# OpenMegatron

**最新版本：1.2.0**

OpenMegatron 是一个本地优先的 AI Agent 工作台，用于长期运行智能体、技能路由、混合记忆、工具调用评估、浏览器/GUI 自动化，以及基于轨迹数据的伴生模型学习。

它适合研究者、开发者和产品团队：你可以看到智能体如何调用工具、如何记住上下文、如何检索知识、如何把成功任务沉淀成技能，以及如何从运行轨迹里持续改进。

[English](README.md) | [中文](README_CN.md)

## 1.2.0 更新重点

- **技能生命周期治理**：保存、晋升、工作流沉淀出来的技能会带 `skill_contract.json`，记录权限、风险、输入输出 schema 和晋升门禁。
- **技能健康度**：基于历史轨迹统计每个技能的成功率、安全信号、退化率、确认负担、平均延迟和健康状态。
- **回放验证门禁**：`/skills/replay_verify` 可以检查某个技能是否已有足够成功轨迹，避免未经验证就自动复用。
- **脱敏发布流程**：新增 `scripts/scrub_config.py`，用于生成脱敏后的 TOML 配置副本。
- **README 刷新**：启动、配置、脱敏、生命周期 API 和常用命令按 1.2.0 流程重新整理。

## 快速开始

### 1. 下载

从项目主页获取最新源码：

https://github.com/WhereIsTom520/openMegatron

下载或克隆后，打开 `openMegatron` 项目目录。

### 2. 安全配置凭据

真实密钥只应该保存在本机，不应该进入仓库。

推荐做法：

- 使用环境变量，例如 `OPENAI_API_KEY`。
- 或者把密钥放在 `pysrc/model.toml`，该文件已被 `.gitignore` 忽略。
- 使用 `pysrc/model.example.toml` 作为安全模板。
- 不要提交 API Key、访问令牌、本地运行状态、日志、截图、cookie 或机器专属路径。

在分享配置、发 release 包、贴日志前，先生成脱敏副本：

```bat
venv\Scripts\python.exe scripts\scrub_config.py --input pysrc\model.toml --output .runtime\model.redacted.toml
```

Linux/macOS：

```bash
python scripts/scrub_config.py --input pysrc/model.toml --output .runtime/model.redacted.toml
```

这个脚本不会覆盖你的真实配置，只会把常见敏感字段替换成 `<redacted>` 后写到输出文件。

### 3. Windows 启动

双击：

```text
start.bat
```

或在命令行运行：

```bat
start.bat
```

### 4. Linux/macOS 启动

```bash
bash start.sh
```

启动脚本会处理环境检查、依赖安装、后端/前端启动、Docker 数据库检查和端口冲突。启动成功后打开脚本打印的地址，通常是：

```text
http://localhost:3000
```

## 常用命令

Windows：

```bat
start.bat             启动后端和前端
start.bat health      检查服务状态
start.bat stop        停止服务
start.bat install     重新安装依赖
start.bat test        运行测试
start.bat menu        打开菜单
```

Linux/macOS：

```bash
bash start.sh
bash start.sh health
bash start.sh stop
bash start.sh install
```

Windows 高级参数：

```bat
start.bat -NoBrowser
start.bat -SkipDocker
start.bat -BackendPort 8001
start.bat -FrontendPort 3001
```

## 主要能力

- **智能体轨迹分析**：记录任务轨迹、工具调用、结果、耗时、置信度和反馈信号。
- **混合记忆**：结合缓存、向量检索、关系型存储、图谱记忆和本体论记录。
- **理论无限上下文工作流**：prompt 中只放短上下文，通过可检索对话历史和长期记忆召回更早内容。
- **工具调用评估**：让工具行为、失败、修复和结果可审计。
- **知识检索增强**：支持文档入库、向量搜索、图搜索、引用式回答和证据边界。
- **技能生命周期治理**：记录技能契约、健康度、回放验证和自动晋升元数据。
- **伴生模型闭环**：把交互历史转成评分、路由、回归检查和后续训练数据。
- **GUI/浏览器自动化**：支持截图、点击、输入、滚动、拖拽、网页导航和本地预览。
- **外部轨迹导入**：导入兼容的 JSONL/text 轨迹和自定义框架数据。

## 技能生命周期治理

OpenMegatron 的技能位于 `pysrc/skills/`。从 1.2.0 开始，生成或晋升出来的技能会带一个 `skill_contract.json`：

```text
pysrc/skills/generated/<skill_name>/
+-- SKILL.md
+-- skill_contract.json
+-- scripts/main.py
```

契约会记录：

- 输入和输出 schema；
- 允许访问的路径、是否允许联网、是否允许写入、是否允许命令执行；
- 风险等级；
- 最少回放案例数、通过率阈值等生命周期门禁；
- 本体论标签和晋升来源信息。

常用 API：

```text
GET  /skills/list
GET  /skills/lifecycle
POST /skills/replay_verify
```

回放验证示例：

```bat
curl -X POST http://127.0.0.1:8000/skills/replay_verify ^
  -H "Content-Type: application/json" ^
  -d "{\"skill_name\":\"browser_control\"}"
```

如果后端自动换了端口，请以 `start.bat health` 打印的端口为准。

## 伴生模型是什么

当前的伴生模型不是“直接替代云端大模型”，而是围绕 OpenMegatron 运行数据建立的本地学习闭环：

1. 收集任务轨迹、工具调用和成功/失败信号；
2. 训练奖励模型或评分器；
3. 用评分器辅助任务质量判断、模型路由、自动重训和回归检查；
4. 后续可以继续接入 SFT/DPO/QLoRA，让部分简单任务交给本地模型。

它的价值是让系统越来越理解你的任务习惯，而不是一开始就宣称完全替代大模型。

## 项目结构

```text
openMegatron/
+-- start.bat              Windows 一键启动入口
+-- start.ps1              启动脚本实现
+-- start.sh               Linux/macOS 启动脚本
+-- package.json           前端包元数据，版本 1.2.0
+-- pysrc/                 Python 后端
|   +-- agent.py           核心 Agent 循环和 FastAPI 接口
|   +-- skill.py           工具与技能注册
|   +-- memory.py          长期记忆和 RAG 存储
|   +-- reward_*.py        奖励模型与训练
|   +-- trajectory_*.py    轨迹采集与导入
|   +-- skills/            技能包
+-- scripts/
|   +-- scrub_config.py    TOML 配置脱敏工具
+-- src/                   React 前端
+-- tests/                 Python 测试
+-- docker-compose.yml     Redis/PostgreSQL/Neo4j
```

## 故障排查

查看服务状态：

```bat
start.bat health
```

查看日志：

```text
.runtime/
log/
```

停止后重启：

```bat
start.bat stop
start.bat
```

## 发布前脱敏检查

发布或分享支持包前建议：

1. 使用 `start.bat stop` 停止本地服务。
2. 使用 `scripts/scrub_config.py` 生成脱敏配置副本。
3. 确认没有包含 `pysrc/model.toml`、`.env*`、`.runtime/`、`.trajectory/`、`log/`、cookie 和本地截图。
4. 运行和本次改动相关的测试。

常用检查：

```bat
venv\Scripts\python.exe -m py_compile pysrc\agent.py pysrc\skill.py scripts\scrub_config.py
venv\Scripts\python.exe -m pytest tests\test_scrub_config.py -q
npm run lint
```

## DOI

Zenodo DOI：

https://doi.org/10.5281/zenodo.20711989
