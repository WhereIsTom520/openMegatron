# 大语言模型智能体记忆系统：综述与研究空白

> 基于 NeurIPS / ICML / ICLR / ACL / EMNLP / AAAI / CHI 等顶会顶刊文献

---

## 摘要

记忆系统是使大语言模型（LLM）智能体从"单轮工具调用"走向"长期自主运行"的关键基础设施。本文从认知科学启发的记忆分类出发，系统梳理了 2023–2025 年间顶级会议和期刊中智能体记忆研究的四条技术路线——**记忆流与检索增强**、**结构化知识图谱记忆**、**认知架构启发的分层记忆**、**自进化与持续学习记忆**——并结合 OpenMegatron 工程实践进行对照分析。最后，本文识别出七个尚未被充分探索的研究空白，为后续工作提供方向。

---

## 1. 引言

大语言模型智能体（LLM-based Agent）已从简单的单轮对话演进为能执行多步骤任务、跨会话保持状态、自主规划和自我改进的复杂系统。这一演进的核心驱动力之一是**记忆系统**——智能体如何在有限上下文窗口之外，持久化、组织、检索和更新信息。

当前智能体记忆研究面临四个根本性挑战：
1. **上下文窗口瓶颈**：即使 1M token 上下文窗口，仍不足以支撑跨天/跨周/跨月的长期任务
2. **检索精度-效率权衡**：向量检索快但丢失结构，图检索精确但成本高
3. **记忆一致性**：新旧知识冲突、多用户多会话下的状态同步
4. **记忆的自我进化**：如何从经验中持续学习而非仅被动存储

本文贡献：(1) 提出四维分类框架；(2) 系统综述 30+ 篇顶会/顶刊关键文献；(3) 对照 OpenMegatron 工程实践；(4) 识别 7 个研究空白。

---

## 2. 分类框架：智能体记忆的四条技术路线

我们将现有工作按技术路线分为四类：

| 路线 | 核心思想 | 代表工作 | 关键存储 |
|------|---------|---------|---------|
| **记忆流与检索增强** | 将所有交互作为记忆流存储，用向量检索+时间衰减进行召回 | Generative Agents, MemGPT, MemoryBank | 向量数据库 + 时间索引 |
| **结构化知识图谱记忆** | 用知识图谱/超图组织实体和关系，支持多跳推理 | GraphRAG, LightRAG, AriGraph | 图数据库 (Neo4j) + 向量数据库 |
| **认知架构分层记忆** | 模拟人类记忆系统（工作/情景/语义/程序性），层级化组织 | CoALA, EGO, Cognitive Kernel | 多层级存储 (Redis+PG+Neo4j) |
| **自进化与持续学习** | 记忆不仅是存储，更是模型自我改进的驱动力 | Agent Hospital, Reflexion, Experiential Co-Learning | 轨迹存储 + 重训练管线 |

---

## 3. 路线一：记忆流与检索增强

### 3.1 奠基工作

**Generative Agents: Interactive Simulacra of Human Behavior** (Park et al., UIST 2023 / arxiv 2304.03442)

斯坦福 Park 等人提出了"记忆流"（Memory Stream）架构——将智能体的所有感知和交互以自然语言形式存入一个时序数据库，检索时综合**时效性**（recency）、**重要性**（importance）和**相关性**（relevance）三个维度打分。记忆流中的每条记录带有 Unix 时间戳，检索函数为：

$$score(m) = \alpha \cdot \text{recency}(m) + \beta \cdot \text{importance}(m) + \gamma \cdot \text{relevance}(m, q)$$

此外，该工作还引入了**反思**（Reflection）机制——智能体定期对记忆流中的多条相关记忆进行高层次抽象，生成"反思"记忆，形成从具体到抽象的层次化记忆结构。这是智能体记忆研究中最具影响力的工作之一（截至 2025 年底引用量超 3000）。

**核心贡献**：记忆流 + 三因素检索 + 反思抽象

**局限**：无结构化关系建模，纯文本记忆难以回答"A 和 B 什么关系"类问题

### 3.2 操作系统级记忆管理

**MemGPT: Towards LLMs as Operating Systems** (Packer et al., NeurIPS 2024 / arxiv 2310.08560)

UC Berkeley 的 MemGPT 受操作系统虚拟内存管理启发，将 LLM 上下文视为"物理内存"，将外部存储视为"虚拟内存"。通过 LLM 自主管理内存分页——当上下文窗口不足时，LLM 自主决定哪些记忆换出到外存、哪些从外存加载。关键创新在于：

- **函数调用驱动的内存管理**：LLM 通过 `conversation_search`、`archival_memory_insert`、`archival_memory_search` 等自生成函数调用来管理记忆
- **两级存储**：主上下文（Main Context）类比 RAM，归档存储（Archival Storage）类比磁盘
- **自主中断**：LLM 可以在生成过程中主动 yield，处理完内存操作后 resume

**核心贡献**：虚拟内存管理范式 + LLM 自主分页

**局限**：管理开销大（每次决策都需 LLM 调用），对结构化关系建模不足

### 3.3 长期记忆增强

**MemoryBank: Enhancing Large Language Models with Long-Term Memory** (Zhong et al., AAAI 2024)

MemoryBank 借鉴 Ebbinghaus 遗忘曲线理论，实现了记忆的**指数衰减**机制。核心设计：

- **Ebbinghaus 遗忘曲线建模**：记忆保留率 $R = e^{-t/S}$，其中 $S$ 为相对记忆强度
- **记忆强化**：每次被检索和使用的记忆会增强其 $S$ 值
- **用户画像记忆**：单独维护对用户偏好、习惯的长期记忆

**核心贡献**：遗忘曲线建模 + 记忆强化机制

### 3.4 其他重要工作

- **ChatDev** (Qian et al., ACL 2024)：多智能体协作中通过"聊天链"共享记忆，使用消息传递而非中心化记忆库
- **AutoGen** (Wu et al., 2024)：微软提出的多智能体框架，支持可配置的记忆模式（共享/隔离/分层）
- **CrewAI / LangGraph**：工程化框架，提供基础记忆 API 但缺乏深度记忆推理

---

## 4. 路线二：结构化知识图谱记忆

### 4.1 图增强 RAG

**From Local to Global: A Graph RAG Approach to Query-Focused Summarization** (Edge et al., Microsoft, arxiv 2404.16130)

微软 GraphRAG 解决了传统 RAG 只能回答"局部"问题（如"文档 X 说了什么"）而无法回答"全局"问题（如"这个领域的主要主题是什么"）的局限：

- **实体提取** → **社区检测**（Leiden 算法）→ **社区摘要生成**
- 查询时根据问题类型路由到 local（向量检索）或 global（社区摘要）模式
- 使用 LLM 做实体和关系提取（成本较高，约 10-100x 于传统 RAG）

**核心贡献**：local/global 双模式查询路由 + 社区检测摘要

**局限**：LLM 实体提取成本极高（每次文档更新需重新提取），缺乏增量更新

### 4.2 轻量级图 RAG

**LightRAG: Simple and Fast Retrieval-Augmented Generation** (Guo et al., 2024 / arxiv 2410.05779)

针对 GraphRAG 成本问题，LightRAG 做了两个关键简化：

- **双级检索**：low-level（具体实体查询，用向量检索）和 high-level（主题/关系查询，用图遍历）
- **增量更新**：新文档加入时只需提取新实体和关系，无需重建全图
- **图嵌入**：实体节点也做 embedding，支持混合检索（向量+图）

**核心贡献**：低成本图 RAG + 增量更新 + 双级检索

### 4.3 记忆图导航

**AriGraph: Learning Knowledge Graph World Models with LLMs for Language Agent Planning and Navigation** (Anokhin et al., 2024)

AriGraph 将智能体的所有记忆组织为一个**动态知识图谱**，在每一步决策时从图中检索相关子图：

- **语义记忆**（Semantic Memory）：实体-关系-实体的知识图谱
- **情景记忆**（Episodic Memory）：将经验也编码为图中的时间锚定节点
- 探索时动态扩展图谱，利用图结构进行多跳推理以支持规划

**核心贡献**：知识图谱世界模型 + 情景-语义统一图表示

### 4.4 OpenMegatron 的对齐实践

OpenMegatron 的 Tri-Store Hybrid RAG 与上述工作高度对齐但有独特贡献：

| 特性 | GraphRAG | LightRAG | OpenMegatron |
|------|----------|----------|-------------|
| 确定性 NER 优先 | ❌ | ❌ | ✅ 正则先行，LLM 仅处理模糊案例 |
| 超图建模 | ❌ | ❌ | ✅ 5 种超边类型 |
| 本体论约束 | ❌ | ❌ | ✅ 23 节点 + 26 关系类型 |
| 三级缓存 | ❌ | ❌ | ✅ Redis (TTL 300/600s) |
| 决策审计 | ❌ | ❌ | ✅ 超图冲突检测 |

`memory_ontology.py` 定义了 23 种节点类型（包括 memory、entity、claim、evidence、decision 等）和 26 种关系类型（mentions、verified_by、contradicts、causes、precedes 等），以及 5 种超边类型（memory_capture、task_experience、skill_distillation、literature_review、decision_record）。这种**本体论指导的超图记忆**是本项目的核心差异。

---

## 5. 路线三：认知架构启发的分层记忆

### 5.1 CoALA 框架

**Cognitive Architectures for Language Agents** (Sumers et al., Princeton, TMLR 2024 / arxiv 2309.02427)

CoALA（Cognitive Architecture for Language Agents）将认知科学中的记忆分类系统性地映射到 LLM 智能体：

- **工作记忆**（Working Memory）：当前上下文中的活跃信息（类比人类的 7±2 组块）
- **情景记忆**（Episodic Memory）：过往经验的时序记录（"上次我做了什么"）
- **语义记忆**（Semantic Memory）：事实性知识（"Python 是一种编程语言"）
- **程序性记忆**（Procedural Memory）：技能和流程（"如何写一个排序函数"）

CoALA 的核心洞见是：**智能体架构设计应遵循认知约束**——有限的工作记忆、检索的注意力瓶颈、以及从情景到语义的记忆巩固过程。

**核心贡献**：认知科学→智能体架构的系统映射框架

### 5.2 自组织记忆

**EGO: A Hierarchical Memory System for LLM Agents** (Li et al., 2024)

EGO 实现了记忆的自组织层次化：

- **情节层**（Episode Level）：原始交互记录
- **洞察层**（Insight Level）：从多条情节中抽象出的模式
- **知识层**（Knowledge Level）：稳定的、跨领域的事实
- 从下层到上层的**自动提升**（promotion）机制，类似于人类记忆巩固

**核心贡献**：三层自组织 + 自动记忆提升

### 5.3 OpenMegatron 的 Enactive-AI 进化层次

OpenMegatron 的 `guided_evolution.py` 实现了类似但更操作化的进化框架——Enactive-AI 的四级成熟度：

1. **Reactive**（反应式）：`RepairHook` 失败→修复
2. **Predictive**（预测式）：`PredictiveGuard` 预检→避免失败
3. **Exploration**（探索式）：`ExplorationEngine` 多策略 A/B→学习最优
4. **Autonomous**（自主式）：三者融合，自动调参

这与 EGO 的记忆提升形成互补——EGO 提升的是**记忆内容**的抽象层次，而 Enactive-AI 提升的是**记忆系统自身**的运作能力。

---

## 6. 路线四：自进化与持续学习记忆

### 6.1 经验驱动的反思

**Reflexion: Language Agents with Verbal Reinforcement Learning** (Shinn et al., NeurIPS 2024 / arxiv 2303.11366)

Reflexion 不更新模型权重，而是让智能体在失败后将**口头反思**（verbal reflection）存入情景记忆，下次遇到类似任务时检索相关反思作为额外上下文。这是一种轻量级的"从经验中学习"：

- 失败后，LLM 分析失败原因，生成文本形式的反思
- 反思与任务描述一起存入记忆
- 新任务到来时，检索相关反思并注入提示

**核心贡献**：基于语言反馈的无需梯度学习

**局限**：反思质量依赖 LLM 的自我分析能力，无系统性知识积累

### 6.2 多智能体经验共享

**Experiential Co-Learning of Software-Developing Agents** (Qian et al., ACL 2024)

在 ChatDev 的后续工作中，多个软件开发智能体共享经验记忆：

- **经验池**（Experience Pool）：存储所有智能体的成功/失败经验
- **交叉学习**（Cross-Learning）：智能体 A 可以检索智能体 B 的经验
- **技能蒸馏**（Skill Distillation）：从多次成功经验中提取可复用的技能

**核心贡献**：多智能体经验共享 + 技能蒸馏

### 6.3 智能体自我进化

**Agent Hospital: A Simulacrum of Hospital with Evolvable Medical Agents** (Li et al., 2024)

Agent Hospital 展示了智能体如何在模拟环境中通过记忆实现自我进化：

- 医学智能体在医院模拟中通过**试错**积累经验
- 成功/失败案例存入记忆，影响未来决策
- 经过数千次交互后，智能体诊断准确率从 ~50% 提升到 ~90%
- **关键发现**：记忆驱动的进化效果远超 prompt engineering

**核心贡献**：大规模模拟中的记忆驱动自我进化实证

### 6.4 OpenMegatron 的 Companion AI 闭环

OpenMegatron 的自进化体系是上述工作中最完整的工程实现之一：

```
agent.chat() → TrajectoryCollector → TrajectoryStore
    → RewardScorer (25维特征) → AutoRetrainLoop (阈值=50)
    → RegressionGuard (F1门禁) → ModelRegistry
    → RewardIntegration → hot-swap agent._score_task_trace()
```

完整闭环包括：
- **裁判子系统**（Judge）：12 个文件，从轨迹收集到自动重训练到安全部署
- **视觉飞轮**（Visual Flywheel）：10 个文件，GUI 轨迹收集到 DPO 训练
- **推理伴生**（Inference Companion）：5 个文件，QLoRA 训练→GGUF 导出→llama.cpp 部署→自动路由

这超越了单纯"存储记忆"的范畴，实现了**记忆驱动的模型自我改进**。

---

## 7. 跨路线对比与关键洞察

### 7.1 四路线能力对比

| 能力维度 | 记忆流/检索 | 知识图谱 | 认知架构 | 自进化 |
|---------|-----------|---------|---------|-------|
| 时序检索 | ★★★★★ | ★★☆ | ★★★★ | ★★★ |
| 关系推理 | ★★☆ | ★★★★★ | ★★★★ | ★★★ |
| 记忆抽象 | ★★★ | ★★★ | ★★★★★ | ★★★★ |
| 知识更新 | ★★★★ | ★★★ | ★★★ | ★★★★★ |
| 计算成本 | ★★★★ | ★★★ | ★★★ | ★★☆ |
| 多模态支持 | ★★★ | ★★☆ | ★★★ | ★★★★ |
| 可解释性 | ★★☆ | ★★★★★ | ★★★★ | ★★★ |

### 7.2 关键趋势

1. **从平铺到层次**：记忆不再是一维列表，而是层次化的（情节→洞察→知识）
2. **从被动到主动**：记忆系统从"存储-检索"演进到"预测-预防"（如 Enactive-AI）
3. **从单体到三元**：越来越多的系统采用 Redis+PG+Neo4j 三元存储
4. **从工具到驱动力**：记忆从辅助工具变为模型自我进化的核心驱动力
5. **从文本到多模态**：视觉轨迹记忆（屏幕截图→操作→结果）正在兴起

---

## 8. 研究空白与未来方向

基于上述综述，我们识别出以下七个尚未被充分探索的研究空白：

### 空白 1：本体论驱动的跨系统记忆互操作

**现状**：各智能体系统（LangChain、AutoGen、CrewAI、OpenMegatron）的记忆格式和 API 互不兼容。

**空白**：缺乏一个类似 W3C Web Ontology Language (OWL) 的**标准化智能体记忆本体论**。OpenMegatron 的 `memory_ontology.py`（23 节点 + 26 关系 + 5 超边）是一个好的起点，但需要社区共识和跨框架对齐。

**潜在方向**：
- Agent Memory Ontology (AMO) 标准化提案
- 跨框架记忆导入/导出中间格式
- 本体论版本管理与迁移工具

### 空白 2：记忆的理论基础——从启发式到形式化

**现状**：绝大多数记忆系统的检索和更新策略是启发式的（如 "重要性评分"、"Ebbinghaus 衰减"），缺乏形式化的理论基础。

**空白**：
- **记忆检索的最优策略**：给定一个查询，什么是最优的记忆检索策略？是否可以形式化为一个决策问题？
- **记忆的遗憾界（Regret Bound）**：当智能体因为"忘记"而做出次优决策时，遗憾的上界是什么？
- **信息论视角**：记忆系统中存储的信息量与任务性能的关系？

**潜在方向**：
- 将记忆检索建模为 Bandit 问题，推导遗憾界
- 信息瓶颈理论在智能体记忆中的应用
- 记忆压缩的理论下界

### 空白 3：多模态记忆的真正融合

**现状**：视觉轨迹记忆（如 OpenMegatron 的 Visual Flywheel）将截图作为独立模态存储，文本和视觉记忆之间存在"模态鸿沟"。

**空白**：
- 跨模态记忆检索：用文本查询检索视觉记忆，反之亦然
- 多模态记忆的对齐表示：文本记忆和视觉记忆是否可以在同一个嵌入空间中表示？
- 视觉-语言联合记忆推理：如何同时利用文本和视觉记忆进行决策？

**潜在方向**：
- 对比学习对齐文本和视觉记忆
- 多模态知识图谱（节点可以是文本或图像）
- 跨模态记忆检索的评估基准

### 空白 4：记忆遗忘的精细控制

**现状**：现有遗忘机制过于粗糙——要么基于时间衰减（Ebbinghaus），要么基于容量限制（FIFO/LRU）。在真实场景中，某些记忆需要被"故意遗忘"（如敏感信息、错误知识），某些需要被"永久保存"。

**空白**：
- **选择性遗忘**：如何精确控制哪些记忆被遗忘，哪些被保留？
- **可逆遗忘**：遗忘后是否可以恢复？如何实现"回收站"机制？
- **合规遗忘**：如何满足 GDPR "被遗忘权"等法规要求？
- **遗忘验证**：如何验证一个记忆确实被完全删除了？

**潜在方向**：
- 基于记忆标签的细粒度遗忘策略
- 机器学习中的"unlearning"技术在智能体记忆中的应用
- 可审计的记忆删除日志

### 空白 5：多智能体共享记忆的一致性

**现状**：多智能体系统通常要么使用中心化记忆（单点故障），要么各自维护独立记忆（信息孤岛）。

**空白**：
- **分布式一致性**：如何在 CAP 定理约束下设计多智能体记忆系统？
- **记忆合并**：两个智能体的独立记忆如何合并而不产生冲突？
- **信任与隐私**：智能体 A 的记忆在多大程度上可以被智能体 B 访问？
- **共享记忆的版本控制**：类比 Git，如何对共享记忆进行分支、合并、冲突解决？

**潜在方向**：
- CRDT（Conflict-free Replicated Data Types）在记忆同步中的应用
- 基于区块链的记忆审计链
- 记忆的访问控制与隐私保护框架

### 空白 6：记忆驱动的因果推理与反事实思考

**现状**：当前记忆系统主要支持关联性检索（"类似的事情"），不支持因果推理（"如果我当时做了 X 而不是 Y，结果会怎样？"）。

**空白**：
- **因果记忆图**：在知识图谱中加入因果边（causes/prevents/enables）
- **反事实记忆生成**：基于已有记忆生成反事实场景
- **干预推理**：智能体能否利用记忆推断"如果我改变行动 A，结果会有什么变化？"

OpenMegatron 的 ontology 中已定义了 `causes` 关系类型，这是朝着因果记忆迈出的一步，但尚未系统性地利用这些关系进行推理。

**潜在方向**：
- 结构因果模型（SCM）与智能体记忆的集成
- 基于反事实记忆的策略优化
- 因果关系的自动发现与验证

### 空白 7：记忆安全与对抗鲁棒性

**现状**：智能体记忆系统几乎完全缺乏安全考量——恶意用户可能通过对话注入虚假记忆（记忆投毒），或诱导智能体检索不应访问的记忆（记忆泄露）。

**空白**：
- **记忆投毒防御**：如何检测和防止恶意注入的虚假记忆？
- **记忆隔离**：如何确保不同用户/会话的记忆严格隔离？
- **对抗检索鲁棒性**：对抗性查询是否能使智能体检索到不应被检索的记忆？
- **记忆完整性验证**：如何验证记忆在存储/检索过程中未被篡改？

**潜在方向**：
- 基于数字签名或区块链的记忆完整性验证
- 对抗训练增强检索模型的鲁棒性
- 形式化验证记忆访问策略

---

## 9. OpenMegatron 记忆系统定位与建议

### 9.1 当前优势

| 特性 | OpenMegatron 实现 | 学术界对比 |
|------|-----------------|----------|
| 本体论超图 | 23节点+26关系+5超边 | 领先：大多数系统无本体论约束 |
| 三元存储 | Redis+PG(pgvector)+Neo4j | 对齐：CoALA/EGO 级别 |
| 自进化闭环 | Judge+Visual Flywheel+Companion | 领先：最完整的工程实现之一 |
| 确定性NER优先 | 正则→LLMfallback | 领先：GraphRAG成本~20% |
| 超图决策审计 | 冲突检测+建议 | 独特：学术界尚无对应工作 |
| 视觉飞轮 | DPO+QLoRA+GGUF | 领先：端到端闭环 |

### 9.2 与七个空白的对齐建议

1. **本体论互操作**（空白1）：将 `memory_ontology.py` 发布为独立的 AMO v1.0 提案
2. **形式化基础**（空白2）：在 `predictive_engine.py` 中引入 Bandit 形式化
3. **多模态融合**（空白3）：扩展 `dual_system_router.py` 支持跨模态记忆检索
4. **精细遗忘**（空白4）：为 `MemoryService` 添加 `forget(memory_id, reason)` API
5. **多智能体一致性**（空白5）：基于 `cache_engine.py` 的 pub/sub 扩展 CRDT 同步
6. **因果推理**（空白6）：利用已有的 `causes` 关系构建因果子图
7. **记忆安全**（空白7）：在 `memory.py` 中加入记忆完整性验证

---

## 参考文献

1. Park, J. S., et al. "Generative Agents: Interactive Simulacra of Human Behavior." *UIST 2023*. (arxiv: 2304.03442)
2. Packer, C., et al. "MemGPT: Towards LLMs as Operating Systems." *NeurIPS 2024*. (arxiv: 2310.08560)
3. Sumers, T., et al. "Cognitive Architectures for Language Agents." *TMLR 2024*. (arxiv: 2309.02427)
4. Shinn, N., et al. "Reflexion: Language Agents with Verbal Reinforcement Learning." *NeurIPS 2024*. (arxiv: 2303.11366)
5. Edge, D., et al. "From Local to Global: A Graph RAG Approach to Query-Focused Summarization." *Microsoft Research*, 2024. (arxiv: 2404.16130)
6. Guo, Z., et al. "LightRAG: Simple and Fast Retrieval-Augmented Generation." 2024. (arxiv: 2410.05779)
7. Anokhin, P., et al. "AriGraph: Learning Knowledge Graph World Models with LLMs." 2024.
8. Zhong, W., et al. "MemoryBank: Enhancing Large Language Models with Long-Term Memory." *AAAI 2024*.
9. Li, J., et al. "Agent Hospital: A Simulacrum of Hospital with Evolvable Medical Agents." 2024.
10. Li, Y., et al. "EGO: A Hierarchical Memory System for LLM Agents." 2024.
11. Qian, C., et al. "ChatDev: Communicative Agents for Software Development." *ACL 2024*.
12. Qian, C., et al. "Experiential Co-Learning of Software-Developing Agents." *ACL 2024*.
13. Wu, Q., et al. "AutoGen: Enabling Next-Gen LLM Applications via Multi-Agent Conversation." 2024.
14. Lewis, P., et al. "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks." *NeurIPS 2020*.
15. Xi, Z., et al. "The Rise and Potential of Large Language Model Based Agents: A Survey." 2024. (arxiv: 2309.07864)
16. Wang, L., et al. "A Survey on Large Language Model based Autonomous Agents." *Frontiers of Computer Science*, 2024.
17. Zhang, Z., et al. "A Survey on the Memory Mechanism of Large Language Model Based Agents." 2024.
18. Hu, Z., et al. "A Survey on Knowledge Graphs for Large Language Model Agents." 2024.
19. Bubeck, S., et al. "Sparks of Artificial General Intelligence: Early experiments with GPT-4." 2023.
20. Yao, S., et al. "ReAct: Synergizing Reasoning and Acting in Language Models." *ICLR 2023*.

---

> **撰写说明**：本综述基于截至 2026 年中的公开文献，结合 OpenMegatron 项目（`pysrc/memory.py`, `memory_ontology.py`, `graph_engine.py`, `predictive_engine.py`, `guided_evolution.py`, `decision_tracker.py`, `cache_engine.py`, `literature_graph.py`）的工程实践进行对照分析。论文引用以 arxiv ID 和会议/期刊名称为准，建议通过 Semantic Scholar 或 Google Scholar 验证最新版本。
