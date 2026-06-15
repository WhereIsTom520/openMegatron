"""Dual-system router: dispatches tasks between text and vision agent systems.

Analyzes incoming user requests and routes them to the appropriate subsystem:
  - TEXT system: API calls, code execution, database queries, research
  - VISION system: GUI automation, browser control, desktop interaction

The router also manages the visual trajectory collector lifecycle and
coordinates reward signals between both systems.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class TaskDomain(str, Enum):
    TEXT = "text"        # API, code, research, data processing
    VISION = "vision"    # GUI, browser, desktop automation
    HYBRID = "hybrid"    # Requires both (e.g., "scrape this page and analyze data")
    AUTO = "auto"        # Auto-detect from query


@dataclass
class DispatchDecision:
    domain: TaskDomain
    confidence: float
    reasoning: str
    suggested_model: str = ""       # e.g., "gpt-4o-mini" or "holo-3.1"
    suggested_tools: List[str] = field(default_factory=list)
    should_screenshot: bool = False


# ── Query Classification ─────────────────────────────────────────────────────

# Strong vision indicators
VISION_KEYWORDS = [
    # Chinese
    "点击", "输入", "截图", "屏幕", "桌面", "窗口", "浏览器",
    "打开网页", "搜索网页", "填写表单", "登录", "下载文件",
    "拖拽", "滚动", "鼠标", "键盘", "按钮", "菜单",
    # English
    "click", "screenshot", "screen", "desktop", "browser",
    "open browser", "search web", "fill form", "login",
    "drag", "scroll", "mouse", "keyboard", "button", "menu",
    "gui", "ui", "interface", "window", "dialog",
    "automate", "macro", "recording",
]

# Strong text indicators
TEXT_KEYWORDS = [
    # Chinese
    "分析数据", "写代码", "运行脚本", "查询数据库",
    "搜索论文", "文献综述", "翻译", "总结", "提取信息",
    "生成报告", "计算", "比较", "评估",
    # English
    "analyze", "write code", "run script", "query database",
    "search papers", "literature review", "translate", "summarize",
    "generate report", "calculate", "compare", "evaluate",
    "api", "json", "csv", "sql", "python", "code",
    "research", "review", "extract information",
]

# Hybrid indicators (both vision + text needed)
HYBRID_KEYWORDS = [
    "scrape and analyze", "extract from page and",
    "screenshot and extract", "capture and process",
    "截取并分析", "抓取并处理", "从网页提取并",
    "截图识别", "OCR", "ocr",
]


def classify_task(user_input: str) -> DispatchDecision:
    """Classify a user request as text, vision, or hybrid task.

    Uses fast keyword matching with confidence scoring.
    """
    text_lower = user_input.lower()

    vision_score = 0.0
    text_score = 0.0
    hybrid_score = 0.0

    # Count keyword matches
    for kw in VISION_KEYWORDS:
        if kw in text_lower:
            vision_score += 1.0
    for kw in TEXT_KEYWORDS:
        if kw in text_lower:
            text_score += 1.0
    for kw in HYBRID_KEYWORDS:
        if kw in text_lower:
            hybrid_score += 2.0

    # Normalize
    total = vision_score + text_score + hybrid_score + 0.001

    # Determine domain — hybrid has highest priority
    if hybrid_score >= 1.0:
        domain = TaskDomain.HYBRID
        confidence = min(0.95, hybrid_score / (hybrid_score + 1.0))
        reasoning = f"Hybrid task: {int(hybrid_score)} hybrid, {int(vision_score)} vision, {int(text_score)} text keywords"
        suggested_model = "holo-3.1"
        suggested_tools = ["screenshot", "execute_gui_action", "run_skill_script"]
        should_screenshot = True
    elif vision_score >= 2 and text_score >= 2:
        # Both vision and text keywords present = hybrid
        domain = TaskDomain.HYBRID
        confidence = 0.8
        reasoning = f"Mixed task: {int(vision_score)} vision + {int(text_score)} text keywords"
        suggested_model = "holo-3.1"
        suggested_tools = ["screenshot", "execute_gui_action", "run_skill_script"]
        should_screenshot = True
    elif vision_score > text_score and vision_score >= 1:
        domain = TaskDomain.VISION
        confidence = min(0.95, vision_score / total * 0.8 + 0.2)
        reasoning = f"Vision task: {int(vision_score)} vision vs {int(text_score)} text keywords"
        suggested_model = "holo-3.1"
        suggested_tools = ["screenshot", "execute_gui_action"]
        should_screenshot = True
    elif text_score > vision_score:
        domain = TaskDomain.TEXT
        confidence = min(0.95, text_score / total * 0.8 + 0.2)
        reasoning = f"Text task: {int(text_score)} text vs {int(vision_score)} vision keywords"
        suggested_model = "gpt-4o-mini"
        suggested_tools = ["search_long_term_memory", "run_skill_script", "rag_search"]
        should_screenshot = False
    else:
        # Default to text (safer, more capable)
        domain = TaskDomain.TEXT
        confidence = 0.5
        reasoning = "No strong signal, defaulting to text"
        suggested_model = "gpt-4o-mini"
        suggested_tools = ["search_long_term_memory", "run_skill_script"]
        should_screenshot = False

    return DispatchDecision(
        domain=domain,
        confidence=round(confidence, 4),
        reasoning=reasoning,
        suggested_model=suggested_model,
        suggested_tools=suggested_tools,
        should_screenshot=should_screenshot,
    )


# ── Dual-System Coordinator ──────────────────────────────────────────────────


class DualSystemCoordinator:
    """Coordinates between text and vision agent subsystems.

    Manages:
      - Task classification and routing
      - Visual trajectory collector lifecycle
      - Cross-system reward signal aggregation
      - Model switching based on task domain

    Usage:
        coord = DualSystemCoordinator(agent, visual_store)
        decision = coord.classify(user_input)

        if decision.domain == TaskDomain.VISION:
            coord.start_visual_session()
            result = await agent.chat_with_vision(user_input, decision)
            coord.end_visual_session(success=True)
        else:
            result = await agent.chat(user_input)
    """

    def __init__(self, agent=None, visual_store=None,
                 visual_collector=None):
        self._agent = agent
        self._visual_store = visual_store
        self._visual_collector = visual_collector
        self._active_visual_session = False
        self._session_stats: Dict[str, Any] = {}

    def classify(self, user_input: str) -> DispatchDecision:
        """Classify a user request."""
        return classify_task(user_input)

    def start_visual_session(self, session_id: str, user_goal: str):
        """Begin a visual automation session with trajectory collection."""
        if self._visual_collector is None:
            from visual_trajectory_collector import VisualTrajectoryCollector
            self._visual_collector = VisualTrajectoryCollector()

        traj_id = self._visual_collector.start_task(session_id, user_goal)
        self._active_visual_session = True
        self._session_stats = {
            "trajectory_id": traj_id,
            "session_id": session_id,
            "user_goal": user_goal,
            "started_at": __import__('time').time(),
        }
        return traj_id

    def record_visual_step(self, action: str, params: dict = None) -> dict:
        """Capture screenshot and record a visual step."""
        if not self._active_visual_session or self._visual_collector is None:
            return {"error": "No active visual session"}

        return self._visual_collector.capture_and_record(action, params)

    def complete_visual_step(self, step_index: int, result: dict = None,
                             elapsed_ms: float = 0.0):
        """Complete a visual step with result data."""
        if self._visual_collector is None:
            return
        from screen_capture import capture_fullscreen
        screenshot = capture_fullscreen()
        self._visual_collector.complete_step(
            step_index,
            screenshot_after=str(screenshot.get("path", "")),
            result=result,
            elapsed_ms=elapsed_ms,
        )

    def end_visual_session(self, success: bool = False,
                           final_answer: str = "") -> Optional[dict]:
        """End the visual session and persist the trajectory."""
        if self._visual_collector is None:
            return None

        # Score with visual reward model
        trajectory = self._visual_collector.finish_task(
            success=success, final_answer=final_answer,
        )
        if trajectory is None:
            return None

        # Score with visual reward model
        from visual_reward_model import VisualRewardScorer
        scorer = VisualRewardScorer()
        reward = scorer.score_trajectory(
            self._visual_collector.to_dict(trajectory)
        )

        # Also score with text reward model if available
        text_reward = {"reward": 0.5, "confidence": 0.0}
        if self._agent is not None:
            try:
                text_reward = self._agent._score_task_trace({
                    "tool_calls": [
                        {"tool": s.action, "duration_ms": s.elapsed_ms,
                         "parsed_output": s.action_result}
                        for s in trajectory.steps
                    ],
                    "success": success,
                    "final_answer": final_answer,
                })
            except Exception:
                pass

        # Blend rewards
        blended_reward = 0.6 * reward["reward"] + 0.4 * text_reward.get("reward", 0.5)
        trajectory.metadata["reward"] = round(blended_reward, 4)
        trajectory.metadata["reward_confidence"] = max(
            reward.get("confidence", 0), text_reward.get("confidence", 0),
        )
        trajectory.metadata["reward_source"] = "blended"
        trajectory.metadata["visual_reward"] = reward
        trajectory.metadata["text_reward"] = text_reward

        # Persist
        if self._visual_store is not None:
            try:
                self._visual_store.store(
                    self._visual_collector.to_dict(trajectory)
                )
            except Exception as e:
                logger.warning(f"Visual trajectory store failed: {e}")

        self._active_visual_session = False
        return self._visual_collector.to_dict(trajectory)

    def get_visual_stats(self) -> dict:
        """Get statistics from the visual subsystem."""
        if self._visual_store is None:
            return {"error": "No visual store configured"}
        return self._visual_store.stats()

    def should_retrain_visual(self, threshold: int = 50) -> bool:
        """Check if enough visual data has accumulated for retraining."""
        if self._visual_store is None:
            return False
        return self._visual_store.count() >= threshold
