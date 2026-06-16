"""Skill routing logic extracted from YuanGeAgent.
SkillRouter wraps skill selection, ranking, and tool call repair."""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
import logging
import platform
import re
import json
import time
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class RouteResult:
    skill_name: str
    confidence: float
    tier: str = "standard"


class _StandaloneRouterAgent:
    loaded_skills = {
        "code_assistant": {
            "description": "Help write, debug, and explain code.",
            "keywords": ["code", "python", "javascript", "write", "debug"],
            "category": "code",
        },
        "default": {
            "description": "Default conversational route.",
            "keywords": [],
            "category": "general",
        },
    }
    skill_embeddings = {}
    skill_docs = {}
    memory_engine = None
    domain_meta = {}
    max_prompt_skills = 8
    top_k_skills = 5
    max_expert_opinions = 0
    expert_debate_mode = "auto"
    expert_debate_min_chars = 180

    def get_clinical_rules(self) -> str:
        return ""


class SkillRouter:
    """Routes user intents to installed skills."""

    def __init__(self, agent=None):
        self._agent = agent or _StandaloneRouterAgent()

    def match(self, user_input: str) -> RouteResult:
        """Return a lightweight route result for callers that only need routing."""
        ranked = self._rank_skills(user_input or "")
        if not ranked:
            return RouteResult(skill_name="default", confidence=0.0)
        score, name, _info = ranked[0]
        confidence = max(0.0, min(1.0, float(score)))
        return RouteResult(skill_name=name, confidence=confidence)

    @property
    def loaded_skills(self):
        return self._agent.loaded_skills

    @property
    def memory_engine(self):
        return self._agent.memory_engine

    @property
    def skill_embeddings(self) -> dict:
        return getattr(self._agent, "skill_embeddings", {})

    @property
    def skill_docs(self) -> dict:
        return getattr(self._agent, "skill_docs", {})

    @property
    def _static_system_prompt_cache(self) -> dict:
        return getattr(self._agent, "_static_system_prompt_cache", {})

    @property
    def domain_meta(self) -> dict:
        return getattr(self._agent, "domain_meta", {})

    @property
    def max_prompt_skills(self) -> int:
        return getattr(self._agent, "max_prompt_skills", 8)

    @property
    def top_k_skills(self) -> int:
        return getattr(self._agent, "top_k_skills", 5)

    @property
    def max_expert_opinions(self) -> int:
        return getattr(self._agent, "max_expert_opinions", 0)

    @property
    def expert_debate_mode(self) -> str:
        return getattr(self._agent, "expert_debate_mode", "auto")

    @property
    def expert_debate_min_chars(self) -> int:
        return getattr(self._agent, "expert_debate_min_chars", 180)

    @property
    def ctx(self):
        return self._agent.ctx

    @property
    def client(self):
        return self._agent.client

    @property
    def model(self) -> str:
        return self._agent.model

    @property
    def extra_params(self) -> dict:
        return getattr(self._agent, "extra_params", {})

    @property
    def broadcast_event(self):
        return getattr(self._agent, "broadcast_event", None)

    @property
    def dehydrator(self):
        return self._agent.dehydrator

    def get_clinical_rules(self) -> str:
        return self._agent.get_clinical_rules()

    def _task_complexity_signals(self, user_input: str) -> dict:
        text = user_input or ""
        lowered = self._normalize_text(text)
        intent = self._detect_task_intent(text)
        category = self._detect_skill_category(text) or "general"
        score = 0
        markers = []

        def add(points: int, marker: str):
            nonlocal score
            score += points
            markers.append(marker)

        if len(text) >= 180:
            add(1, "long_query")
        if len(text) >= 600:
            add(1, "very_long_query")
        if "\n" in text:
            add(1, "multi_line")
        if "traceback" in lowered or "error" in lowered or "报错" in lowered or "exception" in lowered:
            add(2, "debug_or_error")
        if "```" in text or re.search(r"\b(class|def|async|import|select|from|function)\b", lowered):
            add(1, "code_or_structured_text")
        if intent.get("needs_input_completion"):
            add(1, "needs_input_completion")
        if intent.get("wants_discovery") and intent.get("wants_transformation"):
            add(1, "discover_then_transform")
        if category in {"research", "code"}:
            add(1, f"domain_{category}")
        complex_words = [
            "架构", "方案", "优化", "权衡", "安全", "评估", "综述", "研究现状",
            "全链路", "测试覆盖", "性能", "噪音", "长期", "记忆", "对标", "生产环境"
        ]
        if any(word in lowered for word in complex_words):
            add(1, "complex_domain_terms")
        simple_words = ["是什么", "怎么", "列出", "现在可以", "状态", "时间"]
        if len(text) < 80 and any(word in lowered for word in simple_words) and not intent.get("wants_action"):
            score = max(0, score - 1)
            markers.append("simple_question")
        return {"score": score, "markers": markers, "intent": intent, "category": category}

    def _should_use_expert_debate(self, user_input: str, domain: str, experts: List[dict]) -> bool:
        if not experts or self.max_expert_opinions <= 0:
            return False
        mode = getattr(self, "expert_debate_mode", "auto")
        if mode == "off":
            return False
        if mode == "on":
            return True
        lowered = self._normalize_text(user_input)
        explicit_markers = [
            "专家", "辩论", "仲裁", "多视角", "评审", "review",
            "architecture", "安全", "科研方案", "研究方案", "复杂", "权衡"
        ]
        if any(marker in lowered for marker in explicit_markers):
            return True
        signals = self._task_complexity_signals(user_input)
        if domain != "general" and signals["score"] >= 2:
            return True
        if len(user_input) >= self.expert_debate_min_chars and signals["score"] >= 3:
            return True
        return signals["score"] >= 4

    def _normalize_text(self, text: str) -> str:
        return (text or "").strip().lower()

    def _tokenize_for_routing(self, text: str) -> set:
        normalized = self._normalize_text(text)
        tokens = set(re.findall(r'[a-zA-Z0-9_\-\.\/]+|[\u4e00-\u9fff]{1,4}', normalized))
        return {token for token in tokens if token.strip()}

    def _skill_search_text(self, name: str, info: dict) -> str:
        params = json.dumps(info.get("parameters", {}), ensure_ascii=False)
        keywords = " ".join([str(x) for x in info.get("keywords", []) or []])
        manifest = json.dumps({
            "capabilities": info.get("capabilities", []),
            "consumes": info.get("consumes", {}),
            "produces": info.get("produces", {}),
            "side_effects": info.get("side_effects", []),
            "risk": info.get("risk", "")
        }, ensure_ascii=False)
        doc = self.skill_docs.get(name, "")
        return f"{name}\n{info.get('description', '')}\n{params}\n{keywords}\n{manifest}\n{doc[:2400]}".lower()

    def _infer_skill_capabilities(self, name: str, info: dict) -> set:
        text = self._skill_search_text(name, info)
        caps = set()
        raw_caps = info.get("capabilities", []) or []
        if isinstance(raw_caps, str):
            raw_caps = [raw_caps]
        for cap in raw_caps:
            cap_norm = str(cap).strip().lower().replace("-", "_")
            if cap_norm in {"produce", "producer", "search", "lookup", "read", "list", "discover"}:
                caps.add("producer")
            if cap_norm in {"consume", "consumer", "download", "save", "write", "execute", "send", "create", "update", "delete"}:
                caps.add("consumer")
            if cap_norm in {"transform", "transformer", "convert", "extract", "analyze", "summarize", "transcribe"}:
                caps.add("transformer")
            caps.add(cap_norm)
        if info.get("produces"):
            caps.update({"producer", "resource_output", "input_provider"})
        if info.get("consumes") or info.get("parameters"):
            caps.update({"resource_input"})
        if info.get("consumes"):
            caps.update({"consumer", "input_consumer"})
        capability_markers = {
            "producer": [
                "search", "query", "lookup", "find", "list", "discover", "resolve", "inspect", "read",
                "检索", "搜索", "查找", "查询", "列出", "发现", "解析", "读取"
            ],
            "consumer": [
                "download", "save", "fetch", "obtain", "convert", "extract", "transcribe", "summarize",
                "create", "update", "delete", "send", "execute", "run",
                "下载", "保存", "抓取", "获取", "转换", "提取", "转写", "总结", "创建", "更新", "删除", "发送", "执行"
            ],
            "resource_input": [
                "url", "link", "uri", "id", "path", "file", "query", "keyword", "name",
                "链接", "网址", "路径", "文件", "编号", "关键词", "名称"
            ],
            "resource_output": [
                "return", "candidate", "result", "url", "link", "id", "path", "file", "list",
                "返回", "候选", "结果", "链接", "路径", "文件", "列表"
            ],
            "transformer": [
                "convert", "extract", "transcribe", "summarize", "parse", "analyze",
                "转换", "提取", "转写", "解析", "分析", "总结"
            ]
        }
        capability_markers["producer"].extend(["检索", "搜索", "查找", "查询", "列出", "发现", "解析", "读取"])
        capability_markers["consumer"].extend(["下载", "保存", "抓取", "获取", "转换", "提取", "转写", "总结", "创建", "更新", "删除", "发送", "执行"])
        capability_markers["resource_input"].extend(["链接", "网址", "路径", "文件", "编号", "关键词", "名称"])
        capability_markers["resource_output"].extend(["返回", "候选", "结果", "链接", "路径", "文件", "列表"])
        capability_markers["transformer"].extend(["转换", "提取", "转写", "解析", "分析", "总结", "综述", "归纳", "验证"])
        for cap, markers in capability_markers.items():
            if any(marker in text for marker in markers):
                caps.add(cap)
        if "producer" in caps and "resource_output" in caps:
            caps.add("input_provider")
        if "consumer" in caps and "resource_input" in caps:
            caps.add("input_consumer")
        return caps

    def _detect_task_intent(self, user_input: str) -> dict:
        text = user_input or ""
        lower = self._normalize_text(text)
        has_direct_resource = bool(re.search(
            r'https?://|www\.|[a-zA-Z]:[\\/]|/[^ \n\t]+|\b[A-Z]{1,10}[a-zA-Z0-9_-]{6,}\b|\b\d{8,}\b|10\.\d{4,9}/[-._;()/:A-Z0-9]+',
            text,
            re.I
        ))
        action_words = [
            "下载", "保存", "获取", "抓取", "下到", "存到", "导出", "搜索", "查找", "检索", "查询",
            "转换", "提取", "转写", "总结", "分析", "发送", "创建", "更新", "删除", "执行",
            "\u5199\u4ee3\u7801", "\u5b9e\u73b0", "\u5f00\u53d1", "\u4fee\u590d", "\u8c03\u8bd5", "\u91cd\u6784", "\u6d4b\u8bd5",
            "download", "save", "fetch", "get", "obtain", "export", "search", "find", "lookup", "query",
            "convert", "extract", "transcribe", "summarize", "analyze", "send", "create", "update", "delete", "run", "execute",
            "implement", "fix", "debug", "refactor", "patch", "build", "lint", "typecheck"
        ]
        acquisition_words = [
            "下载", "保存", "获取", "抓取", "下到", "存到", "导出",
            "download", "save", "fetch", "get", "obtain", "export"
        ]
        discovery_words = [
            "搜索", "查找", "检索", "查询", "找", "搜",
            "search", "find", "lookup", "query", "discover"
        ]
        transformation_words = [
            "转换", "提取", "转写", "总结", "分析", "convert", "extract", "transcribe", "summarize", "analyze"
        ]
        action_words.extend(["下载", "保存", "获取", "抓取", "下到", "存到", "导出", "搜索", "查找", "检索", "查询", "转换", "提取", "转写", "总结", "分析", "发送", "创建", "更新", "删除", "执行", "综述", "写作", "引用", "验证", "监控", "订阅", "追踪"])
        acquisition_words.extend(["下载", "保存", "获取", "抓取", "下到", "存到", "导出"])
        discovery_words.extend(["搜索", "查找", "检索", "查询", "找", "搜"])
        transformation_words.extend(["转换", "提取", "转写", "总结", "分析", "综述", "归纳", "验证"])
        action_words.extend(["下载", "保存", "获取", "抓取", "搜索", "查找", "检索", "查询", "找一下", "查一下", "总结", "分析", "给出", "生成", "写"])
        acquisition_words.extend(["下载", "保存", "获取", "抓取", "导出"])
        discovery_words.extend(["搜索", "查找", "检索", "查询", "找一下", "查一下", "给出"])
        transformation_words.extend(["转换", "提取", "转写", "总结", "分析", "综述", "归纳", "验证"])
        wants_action = any(word in lower for word in action_words)
        wants_acquisition = any(word in lower for word in acquisition_words)
        wants_discovery = any(word in lower for word in discovery_words)
        wants_transformation = any(word in lower for word in transformation_words)
        needs_input_completion = wants_action and not has_direct_resource
        return {
            "has_direct_resource": has_direct_resource,
            "wants_action": wants_action,
            "wants_acquisition": wants_acquisition,
            "wants_discovery": wants_discovery,
            "wants_transformation": wants_transformation,
            "needs_input_completion": needs_input_completion,
            "normalized_text": lower
        }

    def _lexical_skill_score(self, user_input: str, name: str, info: dict) -> float:
        query_terms = self._tokenize_for_routing(user_input)
        skill_terms = self._tokenize_for_routing(self._skill_search_text(name, info))
        if not query_terms or not skill_terms:
            return 0.0
        overlap = len(query_terms & skill_terms)
        score = overlap / max(len(query_terms), 1)
        lowered = self._normalize_text(user_input)
        if name.lower() in lowered:
            score += 1.0
        for keyword in [str(x).lower() for x in info.get("keywords", []) or []]:
            if keyword and keyword in lowered:
                score += 1.0
        intent = self._detect_task_intent(user_input)
        caps = self._infer_skill_capabilities(name, info)
        if intent["needs_input_completion"] and "input_provider" in caps:
            score += 0.5
        if intent["wants_action"] and ("input_consumer" in caps or "consumer" in caps):
            score += 0.4
        if intent["wants_discovery"] and "producer" in caps:
            score += 0.4
        if intent["wants_transformation"] and "transformer" in caps:
            score += 0.4
        return score

    def _rank_skills(self, user_input: str) -> list:
        ranked = []
        if not self.loaded_skills:
            return ranked
        active_category = self._detect_skill_category(user_input)
        if hasattr(self, "memory_engine") and getattr(self.memory_engine, "emb_model", None) is not None and self.skill_embeddings:
            try:
                query_text = self._normalize_text(user_input)
                query_emb = np.array(self.memory_engine.emb_model.encode([query_text])[0])
                norm_query = np.linalg.norm(query_emb)
                for name, emb in self.skill_embeddings.items():
                    info = self.loaded_skills.get(name)
                    if not info:
                        continue
                    norm_skill = np.linalg.norm(emb)
                    sim = float(np.dot(query_emb, emb) / (norm_query * norm_skill)) if norm_query and norm_skill else 0.0
                    ranked.append((sim + self._lexical_skill_score(user_input, name, info) + self._category_skill_score(active_category, info), name, info))
            except Exception:
                ranked = []
        if not ranked:
            for name, info in self.loaded_skills.items():
                ranked.append((self._lexical_skill_score(user_input, name, info) + self._category_skill_score(active_category, info), name, info))
        if active_category:
            same_category = [item for item in ranked if item[2].get("category", "general") in (active_category, "general")]
            if same_category:
                ranked = same_category
        ranked.sort(key=lambda item: item[0], reverse=True)
        return ranked

    def _detect_skill_category(self, user_input: str) -> str:
        lowered = self._normalize_text(user_input)
        repaired = self._repair_mojibake_text(str(user_input or "")).lower()
        if self._looks_like_research_discussion(repaired):
            return "research"
        category_keywords = {
            "research": [
                "科研", "论文", "文献", "综述", "引用", "citation", "bibtex", "zotero",
                "paper", "literature", "review", "doi", "journal", "conference", "evidence"
            ],
            "media": [
                "视频", "下载", "剪辑", "转码", "字幕", "音频", "b站", "bilibili",
                "video", "download", "youtube", "mp4", "mp3", "transcode", "subtitle"
            ],
            "monitoring": [
                "监控", "订阅", "博客", "watch", "blog", "rss", "提醒", "定时"
            ],
            "office": [
                "ppt", "pptx", "powerpoint", "presentation", "slide", "slides", "deck",
                "excel", "word", "docx", "xlsx", "csv", "pdf", "office",
                "演示文稿", "幻灯片", "课件", "可编辑ppt", "表格", "文档"
            ],
            "code": [
                "\u4ee3\u7801", "\u7f16\u7a0b", "\u7801\u519c", "\u7a0b\u5e8f", "\u5f00\u53d1",
                "\u4fee\u590d", "\u8c03\u8bd5", "\u62a5\u9519", "\u91cd\u6784", "\u5355\u5143\u6d4b\u8bd5",
                "code", "coding", "program", "programmer", "bug", "debug", "fix", "refactor",
                "implement", "patch", "test", "pytest", "lint", "build", "typescript", "javascript",
                "python", "frontend", "backend", "external_agent code", "externalagentjsonl"
            ],
        }
        category_keywords["research"].extend([
            "科研", "论文", "文献", "综述", "引用", "参考文献", "顶刊", "顶会", "期刊", "会议",
            "研究现状", "国内外研究现状", "研究空白", "创新点", "证据矩阵"
        ])
        category_keywords["media"].extend(["视频", "下载", "剪辑", "转码", "字幕", "音频", "b站"])
        category_keywords["monitoring"].extend(["监控", "订阅", "博客", "追踪", "提醒", "定时"])
        category_keywords["research"].extend([
            "科研", "论文", "文献", "综述", "引用", "参考文献", "顶刊", "顶会", "期刊", "会议",
            "研究成果", "研究现状", "研究方向", "研究空白", "证据矩阵", "人机协同"
        ])
        category_keywords["media"].extend(["视频", "下载", "剪辑", "转码", "字幕", "音频"])
        category_keywords["monitoring"].extend(["监控", "订阅", "博客", "追踪", "提醒", "定时"])
        category_keywords["office"].extend(["ppt", "pptx", "powerpoint", "presentation", "幻灯片", "演示文稿", "课件", "可编辑ppt"])
        scores = {}
        for category, keywords in category_keywords.items():
            scores[category] = sum(1 for keyword in keywords if keyword and keyword.lower() in lowered)
        best, score = max(scores.items(), key=lambda item: item[1])
        return best if score > 0 else ""

    def _skill_allowed_for_request(self, skill_name: str, user_input: str) -> bool:
        name = str(skill_name or "").lower()
        lowered = self._normalize_text(user_input)
        repaired = self._repair_mojibake_text(str(user_input or "")).lower()
        if self._looks_like_research_discussion(repaired):
            if any(marker in name for marker in ("bilibili", "video", "download", "media", "subtitle", "audio")):
                return False
            if name in {"code_assistant", "write_and_execute_script"} and not self._has_explicit_code_intent(repaired):
                return False
        if name == "zotero_manager":
            explicit_zotero_terms = (
                "zotero", "bibtex", "文献库", "本地文献", "导出引用",
                "导出 bib", "参考文献库", "zotero desktop",
            )
            return any(term in lowered for term in explicit_zotero_terms)
        return True

    def _looks_like_research_discussion(self, text: str) -> bool:
        text = self._repair_mojibake_text(str(text or "")).lower()
        research_markers = (
            "科研", "论文", "文献", "综述", "顶刊", "顶会", "期刊", "会议",
            "研究空白", "研究方向", "研究问题", "创新点", "参考文献",
            "智能体记忆", "agent memory", "llm agent", "llm-based agent",
            "多智能体", "共享记忆", "社会记忆", "记忆冲突", "记忆拓扑",
        )
        advice_markers = (
            "从哪下手", "如何下手", "怎么下手", "切入", "合理", "方向",
            "选题", "课题", "研究", "方案", "实验设计", "发表",
        )
        return any(marker in text for marker in research_markers) and (
            any(marker in text for marker in advice_markers)
            or any(marker in text for marker in ("论文", "文献", "综述", "顶刊", "顶会", "研究空白"))
        )

    def _has_explicit_code_intent(self, text: str) -> bool:
        text = self._repair_mojibake_text(str(text or "")).lower()
        code_markers = (
            "代码", "编程", "程序", "开发", "实现", "修复", "调试", "报错",
            "重构", "单元测试", "接口", "后端", "前端", "typescript", "javascript",
            "python", "code", "implement", "debug", "bug", "fix", "refactor",
        )
        return any(marker in text for marker in code_markers)

    def _category_skill_score(self, active_category: str, info: dict) -> float:
        if not active_category:
            return 0.0
        category = info.get("category", "general")
        if category == active_category:
            return 2.0
        if category == "general":
            return 0.2
        return -0.3

    def _expand_skills_by_task(self, user_input: str, selected: dict) -> dict:
        intent = self._detect_task_intent(user_input)
        if not intent["wants_action"]:
            return selected
        active_category = self._detect_skill_category(user_input)
        producer_pool = []
        consumer_pool = []
        transformer_pool = []
        for name, info in self.loaded_skills.items():
            if active_category and info.get("category", "general") not in (active_category, "general"):
                continue
            caps = self._infer_skill_capabilities(name, info)
            score = self._lexical_skill_score(user_input, name, info)
            if "input_provider" in caps or "producer" in caps:
                producer_pool.append((score, name, info))
            if "input_consumer" in caps or "consumer" in caps:
                consumer_pool.append((score, name, info))
            if "transformer" in caps:
                transformer_pool.append((score, name, info))
        producer_pool.sort(key=lambda item: item[0], reverse=True)
        consumer_pool.sort(key=lambda item: item[0], reverse=True)
        transformer_pool.sort(key=lambda item: item[0], reverse=True)
        if intent["needs_input_completion"]:
            for score, name, info in producer_pool[:2]:
                if score > 0:
                    selected[name] = info
        if intent["wants_action"]:
            for score, name, info in consumer_pool[:3]:
                if score > 0:
                    selected[name] = info
        if intent["wants_transformation"]:
            for score, name, info in transformer_pool[:2]:
                if score > 0:
                    selected[name] = info
        return selected

    def _build_plan_hint(self, user_input: str, selected_skills: dict) -> str:
        intent = self._detect_task_intent(user_input)
        providers = []
        consumers = []
        transformers = []
        for name, info in selected_skills.items():
            caps = self._infer_skill_capabilities(name, info)
            if "input_provider" in caps or "producer" in caps:
                providers.append(name)
            if "input_consumer" in caps or "consumer" in caps:
                consumers.append(name)
            if "transformer" in caps:
                transformers.append(name)

        # Base hint
        hint = ""
        if intent["needs_input_completion"] and providers and consumers:
            hint = (
                "Planner Signal: The requested goal may not be directly executable because one or more required inputs are missing. "
                "Decompose the goal into tool steps. Use an available input-producing skill to obtain missing arguments, then pass the produced values into an input-consuming skill. "
                "Ask the user only if available tools cannot produce the missing input or the produced candidates are genuinely ambiguous."
            )
        elif intent["has_direct_resource"] and consumers:
            hint = (
                "Planner Signal: The user supplied a direct resource or identifier. Prefer the smallest executable tool chain and pass that resource directly into the matching skill."
            )
        elif intent["wants_transformation"] and transformers:
            hint = (
                "Planner Signal: The task appears to transform, extract, analyze, or summarize an input. If the input itself is missing, first obtain it with an input-producing skill; otherwise run the transformer directly."
            )
        elif intent["wants_action"]:
            hint = (
                "Planner Signal: Treat the request as an objective with required inputs and outputs. If a single tool lacks required inputs, compose tools so that earlier outputs satisfy later inputs."
            )
        else:
            hint = "Planner Signal: No special decomposition signal. Use the minimum necessary tool calls."

        # ── Small-model sub-task guidance ──
        tier_config = getattr(self._agent, 'tier_config', None)
        if tier_config and tier_config.auto_decompose:
            from model_tier import suggest_subtasks
            subtasks = suggest_subtasks(user_input, tier_config)
            if subtasks:
                hint += (
                    "\n\nSmall Model Guidance: You are running on a small model. "
                    "Break this task into sequential steps. Complete each step before moving to the next. "
                    "One tool call per response. Do not try to do everything at once.\n"
                    "Suggested breakdown:\n" + "\n".join(subtasks)
                )

        return hint

    def _skill_budget_for_task(self, user_input: str) -> tuple[int, int]:
        if getattr(self, "skill_budget_mode", "auto") in {"off", "fixed"}:
            return self.top_k_skills, self.max_prompt_skills
        signals = self._task_complexity_signals(user_input)
        score = int(signals.get("score", 0))
        intent = signals.get("intent", {})
        category = signals.get("category", "general")
        if intent.get("has_direct_resource") and intent.get("wants_action"):
            top_k, max_prompt = 3, 5
        elif score >= 4:
            top_k, max_prompt = 5, 8
        elif score >= 2 or category in {"research", "code", "media"}:
            top_k, max_prompt = 4, 6
        elif intent.get("wants_action"):
            top_k, max_prompt = 3, 5
        else:
            top_k, max_prompt = 2, 3
        top_k = max(1, min(top_k, self.max_prompt_skills))
        max_prompt = max(top_k, min(max_prompt, self.max_prompt_skills))
        return top_k, max_prompt

    async def _llm_select_skills(self, user_input: str, candidates: list) -> list[str]:
        """Ask the LLM to select the most relevant skills for a user request.

        Args:
            user_input: The user's message.
            candidates: List of (name, description) tuples.

        Returns:
            List of skill names the LLM recommends.
        """
        if not hasattr(self._agent, 'client') or not self._agent.client:
            return []
        if len(candidates) <= 3:
            return [c[0] for c in candidates]

        skill_list = "\n".join(
            f"- {name}: {desc[:200]}" for name, desc in candidates[:15]
        )
        prompt = (
            "You are a skill router. Given a user request and available skills, "
            "select which skills are needed. Return JSON only.\n\n"
            f"User request: {user_input[:500]}\n\n"
            f"Available skills:\n{skill_list}\n\n"
            "Return JSON: {\"skills\": [\"skill_name_1\", \"skill_name_2\"], \"rationale\": \"...\"}\n"
            "Only include skills that are actually needed. Be conservative — fewer is better."
        )
        try:
            res = await self._agent.client.chat.completions.create(
                model=self._agent.model,
                messages=[
                    {"role": "system", "content": "You are a precise skill router. Return JSON only."},
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
                **getattr(self._agent, "extra_params", {}),
            )
            data = json.loads(res.choices[0].message.content)
            return data.get("skills", [])
        except Exception:
            return []

    def _select_skills_for_prompt(self, user_input: str, ranked: list = None, budget: tuple[int, int] = None) -> dict:
        selected = {}
        ranked = ranked if ranked is not None else self._rank_skills(user_input)
        active_category = self._detect_skill_category(user_input)
        top_k, max_prompt = budget or self._skill_budget_for_task(user_input)

        # Layer 1: Embedding-ranked top-K (replaces brittle keyword matching)
        for _, name, info in ranked[:top_k]:
            if not self._skill_allowed_for_request(name, user_input):
                continue
            selected[name] = info

        # Layer 2: Lexical fallback (lightweight, covers exact matches)
        lowered = self._normalize_text(user_input)
        for name, info in self.loaded_skills.items():
            if not self._skill_allowed_for_request(name, user_input):
                continue
            if active_category and info.get("category", "general") not in (active_category, "general"):
                continue
            lex_score = self._lexical_skill_score(user_input, name, info)
            if lex_score >= 0.75:
                selected[name] = info
            elif name.lower() in lowered:
                selected[name] = info

        selected = self._expand_skills_by_task(user_input, selected)
        selected = {
            name: info
            for name, info in selected.items()
            if self._skill_allowed_for_request(name, user_input)
        }

        # Layer 3: Trim to budget using capability-aware scoring
        if len(selected) > max_prompt:
            intent = self._detect_task_intent(user_input)
            def priority(item):
                name, info = item
                score = self._lexical_skill_score(user_input, name, info) + self._category_skill_score(active_category, info)
                caps = self._infer_skill_capabilities(name, info)
                if intent["needs_input_completion"] and ("input_provider" in caps or "producer" in caps):
                    score += 2.0
                if intent["wants_action"] and ("input_consumer" in caps or "consumer" in caps):
                    score += 2.0
                if intent["wants_transformation"] and "transformer" in caps:
                    score += 1.0
                return score
            selected = dict(sorted(selected.items(), key=priority, reverse=True)[:max_prompt])
        return selected

    def _build_skill_route_trace(self, user_input: str, ranked: list, selected_skills: dict, budget: tuple[int, int] = None) -> dict:
        intent = self._detect_task_intent(user_input).copy()
        intent.pop("normalized_text", None)
        candidates = []
        top_k, max_prompt = budget or self._skill_budget_for_task(user_input)
        for score, name, info in ranked[:max_prompt]:
            candidates.append({
                "skill": name,
                "score": round(float(score), 4),
                "category": info.get("category", "general"),
                "capabilities": sorted(list(self._infer_skill_capabilities(name, info)))[:12],
                "selected": name in selected_skills
            })
        return {
            "intent": intent,
            "category": self._detect_skill_category(user_input) or "general",
            "complexity": self._task_complexity_signals(user_input),
            "top_k": top_k,
            "max_prompt_skills": max_prompt,
            "budget_mode": getattr(self, "skill_budget_mode", "auto"),
            "selected_skills": list(selected_skills.keys()),
            "candidates": candidates
        }

    def _build_skill_instructions(self, selected_skills: dict) -> str:
        lines = []
        for name, info in selected_skills.items():
            params = json.dumps(info.get("parameters", {}), ensure_ascii=False) if info.get("parameters") else "None"
            consumes = json.dumps(info.get("consumes", {}), ensure_ascii=False) if info.get("consumes") else "None"
            produces = json.dumps(info.get("produces", {}), ensure_ascii=False) if info.get("produces") else "None"
            caps = sorted(list(self._infer_skill_capabilities(name, info)))
            caps_text = ", ".join(caps) if caps else "general"
            category = info.get("category", "general")
            lines.append(
                f"- Skill Name: `{name}`\n"
                f"  Category: {category}\n"
                f"  Capabilities: {caps_text}\n"
                f"  Description: {info.get('description', '')}\n"
                f"  Expected Parameters: {params}\n"
                f"  Consumes: {consumes}\n"
                f"  Produces: {produces}"
            )
        return "\n".join(lines) if lines else "No dynamically loaded single-file skills available."

    def _available_skills_can_cover_task(self, user_input: str, selected_skills: dict) -> bool:
        intent = self._detect_task_intent(user_input)
        if not intent["wants_action"] or not selected_skills:
            return False
        for _, info in selected_skills.items():
            caps = self._infer_skill_capabilities("", info)
            if "consumer" in caps or "input_consumer" in caps or "producer" in caps or "input_provider" in caps or "transformer" in caps:
                return True
        return False

    def _should_prefer_installed_skill_path(self, user_input: str, selected_skills: dict) -> bool:
        if self._available_skills_can_cover_task(user_input, selected_skills):
            return True
        active_category = self._detect_skill_category(user_input)
        if not active_category or not selected_skills:
            return False
        return any(info.get("category", "general") == active_category for info in selected_skills.values())

    def _blocked_script_tool_output(self, tool_name: str = "extension_tool") -> str:
        return json.dumps({
            "status": "error",
            "message": f"Blocked {tool_name} because installed skills are available for this objective. Use run_skill_script with complete JSON parameters, or summarize the current skill result and its evidence boundary.",
            "completed": False
        }, ensure_ascii=False)

    def _trace_has_installed_skill_call(self, task_trace: dict) -> bool:
        for call in task_trace.get("tool_calls", []) or []:
            if call.get("tool") == "run_skill_script":
                return True
        return False

    def _repair_mojibake_text(self, value: str) -> str:
        text = str(value or "")
        if not text:
            return text

        def cjk_count(item: str) -> int:
            return len(re.findall(r"[\u4e00-\u9fff]", item or ""))

        def badness(item: str) -> int:
            return len(re.findall(r"[ÃÂÄÅÆÇÈÉÊËÌÍÎÏÐÑÒÓÔÕÖ×ØÙÚÛÜÝÞßàáâãäåæçèéêëìíîïðñòóôõöøùúûüýþÿ�\x80-\x9f]", item or ""))

        def semantic_score(item: str) -> int:
            markers = (
                "人机", "协同", "合作", "信息系统", "信管", "信息管理",
                "智能体", "记忆", "论文", "文献", "综述",
            )
            return sum(1 for marker in markers if marker in (item or ""))

        candidates = [text]
        for source_encoding in ("latin1", "cp1252"):
            try:
                raw = text.encode(source_encoding)
            except UnicodeError:
                continue
            for target_encoding in ("utf-8", "gbk"):
                try:
                    decoded = raw.decode(target_encoding)
                except UnicodeError:
                    continue
                if decoded not in candidates:
                    candidates.append(decoded)
        return max(candidates, key=lambda item: (semantic_score(item), cjk_count(item), -badness(item), len(item)))

    def _is_simple_research_lookup(self, user_input: str) -> bool:
        text = self._repair_mojibake_text(str(user_input or "")).lower()
        lookup_terms = ("论文", "文献", "paper", "papers", "literature", "doi", "链接", "link", "links")
        deep_terms = (
            "综述", "review", "证据矩阵", "evidence matrix", "matrix", "全文", "read",
            "paper_reader", "citation_graph", "引用图", "引文图", "citation graph",
            "验证", "verify", "研究空白", "future direction", "未来方向",
        )
        return any(term in text for term in lookup_terms) and not any(term in text for term in deep_terms)

    def _skill_name_from_tool_arguments(self, arguments: str) -> str:
        try:
            outer = json.loads(arguments or "{}")
            return str(outer.get("skill_name") or "")
        except Exception:
            return ""

    def _trace_has_successful_paper_fetch(self, task_trace: dict) -> bool:
        for call in task_trace.get("tool_calls", []) or []:
            if call.get("tool") != "run_skill_script":
                continue
            skill_name = self._skill_name_from_tool_arguments(str(call.get("arguments") or ""))
            if skill_name != "paper_fetch_review":
                continue
            parsed = call.get("parsed_output")
            if not isinstance(parsed, dict) or parsed.get("status") == "error":
                continue
            payload = parsed
            raw_output = parsed.get("output")
            if isinstance(raw_output, str):
                try:
                    decoded_output = json.loads(raw_output)
                    if isinstance(decoded_output, dict):
                        payload = decoded_output
                except Exception:
                    pass
            papers = payload.get("papers") or []
            valid_count = payload.get("valid_count")
            if (isinstance(valid_count, int) and valid_count > 0) or papers:
                return True
        return False

    def _blocked_extra_research_tool_output(self, skill_name: str) -> str:
        return json.dumps({
            "status": "success",
            "message": (
                f"No further research skill call is needed for '{skill_name}': paper_fetch_review already returned usable papers "
                "for this lookup request. Provide the final answer from the existing result and state the evidence boundary."
            ),
            "completed": True,
        }, ensure_ascii=False)

    def _repair_tool_arguments_for_context(self, tool_name: str, arguments: str, routing_input: str) -> str:
        if tool_name != "run_skill_script":
            return arguments
        try:
            outer = json.loads(arguments or "{}")
            skill_name = str(outer.get("skill_name") or "")
            if skill_name not in {"paper_fetch_review", "review_pipeline"}:
                return arguments
            inner = json.loads(outer.get("args_string") or outer.get("params_json") or "{}")
            query = self._repair_mojibake_text(str(inner.get("query") or "")).strip()
            if query:
                inner["query"] = query
            routing = self._repair_mojibake_text(str(routing_input or "")).strip()
            if not routing:
                return arguments
            routing_lower = routing.lower()
            query_lower = query.lower()
            important_terms = [
                "\u4eba\u673a\u534f\u540c",
                "\u4eba\u673a\u5408\u4f5c",
                "\u4eba\u673a\u4ea4\u4e92",
                "\u4fe1\u606f\u7cfb\u7edf",
                "\u4fe1\u7ba1",
                "\u4fe1\u606f\u7ba1\u7406",
                "人机协同",
                "人机合作",
                "人机交互",
                "human-ai collaboration",
                "human ai collaboration",
                "human-machine collaboration",
                "human machine collaboration",
                "information systems",
                "信息系统",
                "信管",
                "信息管理",
            ]
            missing_terms = [
                term for term in important_terms
                if term.lower() in routing_lower and term.lower() not in query_lower
            ]
            if missing_terms:
                inner["query"] = " ".join([query or routing, *missing_terms]).strip()
            if "year_start" not in inner and re.search(r"202[4-9]|203\d", routing):
                year_match = re.search(r"202[4-9]|203\d", routing)
                if year_match:
                    inner["year_start"] = int(year_match.group(0))
            outer["args_string"] = json.dumps(inner, ensure_ascii=False)
            return json.dumps(outer, ensure_ascii=False)
        except Exception:
            return arguments

    def _build_static_system_prompt(self, domain: str) -> str:
        cache_key = f"{platform.system()}::{domain}"
        if cache_key in self._static_system_prompt_cache:
            return self._static_system_prompt_cache[cache_key]
        os_name = platform.system()
        if os_name == "Windows":
            os_policy = "0. Identity (Windows): Use CMD commands (dir, type, copy). No PowerShell. When reading SKILL.md files, translate any Unix commands (python3, pip3, chmod, ./script.sh, /usr/bin/) to Windows equivalents before generating code."
        elif os_name == "Linux":
            os_policy = "0. Identity (Linux): Use bash commands (ls, cat, cp). When reading SKILL.md, note that commands may assume Unix."
        else:
            os_policy = f"0. Identity ({os_name}): Use standard shell commands."
        rules = (
            "0. FAILURE HANDLING: If a tool or skill returns an error, read the error message, repair the inputs once, and retry only when the repair is clear. If the same operation fails again, stop and report the error.\n"
            "1. OBJECTIVE DECOMPOSITION: Treat the user request as an objective with required inputs, intermediate outputs, and final outputs. If one tool cannot complete the objective because required inputs are missing, split the objective into smaller tool steps.\n"
            "2. INPUT COMPLETION: Before asking the user for missing information, inspect Available Skills to see whether another skill can produce or infer the missing input. Use earlier tool outputs as later tool inputs.\n"
            "3. DIRECT EXECUTION: If the user already provided all required inputs for a matching skill, call that skill directly and avoid unnecessary discovery steps.\n"
            "4. TOOL CONTRACT: For run_skill_script, args_string must be exactly one valid JSON object string containing the skill parameters. Never pass plain text, markdown, command-line flags, or an empty object when required fields are known.\n"
            "5. PARAMETER REPAIR: If a skill fails because of malformed arguments, correct the JSON in the very next call. Preserve user constraints such as output path, format, language, resolution, time range, and mode.\n"
            "6. AMBIGUITY: If tool-produced candidates are clearly ranked or one candidate best matches the request, choose it. Ask the user only when candidates are genuinely ambiguous or when no available skill can resolve the missing input.\n"
            "7. TOOL MINIMALITY: Use the smallest reliable chain of tools. Do not install new skills, write replacement scripts, or switch strategies when existing loaded skills can satisfy the objective.\n"
            "7a. RESEARCH TOOL MINIMALITY: For simple paper lookup or link requests, call paper_fetch_review once with the user's topic, year range, and inferred domain, then answer from that result. Use review_pipeline for literature reviews, evidence_matrix for matrix requests, and paper_reader/citation_graph only when the user explicitly asks for full-paper reading, citation graph, verification, or deeper expansion.\n"
            "8. SKILL COMPOUNDING: When write_and_execute_script succeeds and the user confirms the script is generally useful, call promote_script_to_skill instead of leaving the solution as a one-off script.\n"
            "9. SAFETY AND FILESYSTEM: Use available tools within the configured safety policy. The runtime enforces configured write-path restrictions before execution.\n"
            "10. TURN BOUNDARIES: Tool calls must serve the current user request. If the latest message is a follow-up, anchor it to the immediate previous user request instead of resuming older goals.\n"
            "11. TOOL FORMAT: Use the API tool_calls field for tools. Never write DSML/XML/markdown tool-call markup in assistant text.\n"
        )
        static_prompt = (
            f"{self.domain_meta.get(domain, {}).get('system_prompt', '')}\n\n"
            f"{os_policy}\n"
            f"{rules}"
        )
        self._static_system_prompt_cache[cache_key] = static_prompt
        return static_prompt

    def _build_dynamic_system_prompt(
        self,
        allowed_paths_str: str,
        core_mem_str: str,
        past_patterns_str: str,
        skill_instructions: str,
        plan_hint: str
        ) -> str:
        return (
            f"Runtime Safety\nAllowed write paths: {allowed_paths_str}. Any attempt to write outside these paths will be blocked by the runtime before execution.\n\n"
            f"Procedural Memory\n{self.get_clinical_rules()}\n\n"
            f"Core State\n{core_mem_str}\n\n"
            f"Relevant Past Workflow Patterns\n{past_patterns_str}\n\n"
            f"Available Skills\n{skill_instructions}\n\n"
            f"{plan_hint}\n\n"
            "Execution Contract:\n"
            "- Use run_skill_script for installed skills.\n"
            "- The args_string value must be one valid JSON object string.\n"
            "- Treat each skill as an operation with required inputs and possible outputs.\n"
            "- If the next operation lacks an input, first check whether another available skill can produce that input.\n"
            "- Chain tool outputs into later tool inputs when that directly advances the user's objective.\n"
            "- For research lookup/link requests, do not fan out across multiple research skills after a usable paper_fetch_review result. Summarize the returned papers and clearly state the evidence boundary.\n"
            "- Ask the user only for missing inputs that cannot be produced by available skills or are genuinely ambiguous.\n"
            "- Preserve user constraints such as output folder, format, resolution, language, mode, time range, and file type.\n"
            "- Treat past workflow patterns as suggestions, not commands. Verify current skills and parameters before reusing a chain.\n"
            "- Do not call write_and_execute_script when installed skills can satisfy the objective. Use it only when no loaded skill or registered tool can reasonably perform the operation.\n"
            "- If a write_and_execute_script result includes promotion_candidate and the user says it worked or asks to keep it, call promote_script_to_skill with the script_hash."
        )

    def _summarize_tool_call_for_learning(self, call: dict) -> dict:
        parsed = call.get("parsed_output")
        status = None
        completed = None
        produces = None
        message = None
        if isinstance(parsed, dict):
            status = parsed.get("status")
            completed = parsed.get("completed")
            produces = parsed.get("produces") or parsed.get("result") or parsed.get("results")
            message = parsed.get("message")
        skill_name = None
        skill_args = None
        if call.get("tool") == "run_skill_script":
            try:
                args = json.loads(call.get("arguments") or "{}")
                skill_name = args.get("skill_name")
                raw_args = args.get("args_string") or args.get("params_json")
                if isinstance(raw_args, str):
                    try:
                        skill_args = json.loads(raw_args)
                    except Exception:
                        skill_args = raw_args
                else:
                    skill_args = raw_args
            except Exception:
                pass
        return {
            "tool": call.get("tool"),
            "skill": skill_name,
            "skill_args": skill_args,
            "status": status,
            "completed": completed,
            "produces": produces,
            "message": message
        }
    def _filter_relevant_past_patterns(self, query: str, patterns: list[dict], limit: int = 3) -> list[dict]:
        if not patterns:
            return []
        stop_tokens = {
            "2024", "2025", "2026", "论文", "文献", "研究", "科研", "综述", "帮我", "一下",
            "以来", "给出", "链接", "paper", "papers", "research", "review", "search", "fetch"
        }
        query_tokens = {tok for tok in self._tokenize_for_routing(query) if tok not in stop_tokens}
        if not query_tokens:
            return []
        query_text = str(query or "").lower()
        marker_groups = [
            ("人机", "human"),
            ("协同", "collaboration"),
            ("智能体", "agent"),
            ("记忆", "memory"),
            ("信息管理", "information management"),
            ("信息系统", "information systems"),
            ("信管", "mis"),
        ]
        scored = []
        for pattern in patterns:
            if not isinstance(pattern, dict):
                continue
            text = " ".join([
                str(pattern.get("goal_type", "")),
                str(pattern.get("notes", "")),
                " ".join([str(x) for x in pattern.get("preconditions", []) or []]),
                " ".join([str(x) for x in pattern.get("successful_chain", []) or []]),
                " ".join([str(x) for x in pattern.get("example_user_goals", []) or []]),
            ]).lower()
            if any((zh in query_text or en in query_text) and not (zh in text or en in text) for zh, en in marker_groups):
                continue
            pattern_tokens = {tok for tok in self._tokenize_for_routing(text) if tok not in stop_tokens}
            overlap = len(query_tokens & pattern_tokens)
            ratio = overlap / max(1, min(len(query_tokens), len(pattern_tokens)))
            if overlap >= 2 and ratio >= 0.25:
                scored.append((overlap + ratio, pattern))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [pattern for _, pattern in scored[:limit]]

    def _score_task_trace(self, trace: dict) -> dict:
        tool_calls = trace.get("tool_calls", []) or []
        if not tool_calls:
            return {
                "reward": 0.2 if trace.get("final_answer") else 0.0,
                "confidence": 0.35 if trace.get("final_answer") else 0.0,
                "dimensions": {
                    "success": bool(trace.get("final_answer")),
                    "stability": 1.0,
                    "speed": 1.0,
                    "efficiency": 1.0,
                    "tool_count": 0,
                    "duration_ms": 0.0
                }
            }
        failures = 0
        successes = 0
        total_duration_ms = 0.0
        for call in tool_calls:
            parsed = call.get("parsed_output") if isinstance(call.get("parsed_output"), dict) else {}
            status = str(parsed.get("status") or "").lower()
            failed = status in {"error", "denied"} or bool(parsed.get("error"))
            failures += 1 if failed else 0
            successes += 0 if failed else 1
            total_duration_ms += float(call.get("duration_ms") or 0.0)
        tool_count = len(tool_calls)
        stability = successes / tool_count if tool_count else 1.0
        speed = 1.0 / (1.0 + (total_duration_ms / 60000.0))
        efficiency = 1.0 / (1.0 + max(0, tool_count - 3) * 0.18)
        completion = 1.0 if trace.get("success") else 0.0
        reward = max(0.0, min(1.0, 0.45 * completion + 0.25 * stability + 0.20 * speed + 0.10 * efficiency))
        confidence = max(0.0, min(1.0, 0.50 * stability + 0.25 * speed + 0.25 * completion))
        return {
            "reward": round(reward, 4),
            "confidence": round(confidence, 4),
            "dimensions": {
                "success": bool(trace.get("success")),
                "stability": round(stability, 4),
                "speed": round(speed, 4),
                "efficiency": round(efficiency, 4),
                "tool_count": tool_count,
                "failures": failures,
                "duration_ms": round(total_duration_ms, 2)
            }
        }

    async def _learn_from_task_trace(self, trace: dict):
        if not trace or not trace.get("tool_calls"):
            return
        reward_profile = self._score_task_trace(trace)
        trace["reward_profile"] = reward_profile
        try:
            await self.ctx.record_tool_reward_stats(trace.get("tool_calls", []), reward_profile)
        except Exception as e:
            logger.debug(f"Tool reward stats write skipped: {e}")
        summarized_calls = [self._summarize_tool_call_for_learning(call) for call in trace.get("tool_calls", [])]
        successful_chain = []
        for call in summarized_calls:
            status = call.get("status")
            if status == "error":
                continue
            name = call.get("skill") or call.get("tool")
            if name and name not in successful_chain:
                successful_chain.append(name)
        if not successful_chain:
            return
        compact_trace = {
            "user_goal": trace.get("user_goal"),
            "success": bool(trace.get("success")),
            "reward_profile": reward_profile,
            "selected_skills": trace.get("selected_skills", []),
            "tool_calls": summarized_calls[-10:]
        }
        prompt = (
            "Return one JSON object describing a reusable workflow pattern from this agent task. "
            "Do not create platform-specific rules unless they are explicitly represented by tool names or tool metadata. "
            "Prefer general preconditions and reusable tool-chain logic. "
            "Include whether this workflow should be reused or demoted based on reward_profile. "
            "Schema: {\"goal_type\": string, \"preconditions\": string[], \"successful_chain\": string[], \"notes\": string, \"should_create_skill\": boolean, \"reuse_confidence\": number}.\n"
            f"Trace: {json.dumps(compact_trace, ensure_ascii=False)}"
        )
        try:
            res = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You extract reusable workflow patterns for an autonomous tool-using agent. Return JSON only."},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"},
                **self.extra_params
            )
            pattern = json.loads(res.choices[0].message.content)
        except Exception:
            pattern = {
                "goal_type": "tool_chain_task",
                "preconditions": ["A similar user goal required multiple tool calls"],
                "successful_chain": successful_chain,
                "notes": "Reuse this chain only when current available skills and required parameters still match.",
                "should_create_skill": False
            }
        if not isinstance(pattern, dict):
            return
        chain = pattern.get("successful_chain")
        if not isinstance(chain, list) or not chain:
            pattern["successful_chain"] = successful_chain
        pattern["success_count"] = 1 if trace.get("success") else 0
        pattern["failure_count"] = 0 if trace.get("success") else 1
        pattern["example_user_goals"] = [str(trace.get("user_goal", ""))[:300]]
        pattern["metadata"] = {
            "selected_skills": trace.get("selected_skills", []),
            "tool_count": len(trace.get("tool_calls", [])),
            "reward_profile": reward_profile,
            "started_at": trace.get("started_at")
        }
        pattern["last_seen"] = time.time()
        try:
            await self.ctx.record_task_pattern(pattern)
        except Exception:
            pass
        try:
            workflow_id = await self.memory_engine.add_workflow_pattern(pattern)
            pattern["workflow_pattern_id"] = workflow_id
            if self.broadcast_event:
                await self.broadcast_event("workflow_pattern_learned", {
                    "id": workflow_id,
                    "goal_type": pattern.get("goal_type"),
                    "chain": pattern.get("successful_chain", [])
                })
        except Exception as e:
            logger.warning(f"Workflow pattern database persistence skipped: {e}")
        try:
            await self.dehydrator.process(trace.get("session_id", "default"), "Reusable Task Pattern:\n" + self._json_dumps_safe(pattern))
        except Exception:
            pass


