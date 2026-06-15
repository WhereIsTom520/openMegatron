"""Companion Model Router — auto-switch between cloud and companion models.

Integrates with agent.py to dynamically route requests between:
  1. Cloud model (GPT-4, Claude) — complex tasks, first attempt
  2. Companion model (local) — simple tasks, retries, cost-saving

Decision logic:
  - If companion model is available AND meets quality gate → use companion
  - If companion model fails or returns low confidence → fall back to cloud
  - Task complexity scoring determines which model to try first
  - Failed cloud requests can be retried on companion (saves cost)

The router also tracks companion model performance for continuous improvement.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ModelTarget(str, Enum):
    CLOUD = "cloud"           # Use the main cloud model (GPT-4, Claude)
    COMPANION = "companion"    # Use the local companion model
    AUTO = "auto"             # Auto-detect best target


@dataclass
class RoutingDecision:
    target: ModelTarget
    reason: str
    companion_available: bool = False
    companion_model_id: str = ""
    complexity_score: float = 0.5
    estimated_cost_saved: float = 0.0


@dataclass
class CompanionStats:
    """Performance tracking for the companion model."""
    total_attempts: int = 0
    successes: int = 0
    failures: int = 0
    fallbacks_to_cloud: int = 0
    avg_latency_ms: float = 0.0
    total_cost_saved: float = 0.0  # Estimated USD saved

    @property
    def success_rate(self) -> float:
        return self.successes / max(1, self.total_attempts)


class CompanionRouter:
    """Routes agent requests between cloud and companion models.

    Usage:
        router = CompanionRouter(agent)
        decision = router.decide(user_input, task_complexity=0.5)

        if decision.target == ModelTarget.COMPANION:
            response = router.call_companion(messages, tools)
            if router.is_response_acceptable(response):
                return response
            # Fall through to cloud
        return router.call_cloud(messages, tools)
    """

    def __init__(self, agent=None, companion_loader=None):
        self._agent = agent
        self._loader = companion_loader
        self._stats = CompanionStats()
        self._last_decision: Optional[RoutingDecision] = None

        # Thresholds
        self.min_companion_f1 = 0.6      # Minimum F1 to use companion
        self.max_complexity_for_companion = 0.7  # Route to cloud if complexity > this
        self.min_confidence_threshold = 0.3  # Fall back if companion confidence < this

    # ── Decision Logic ─────────────────────────────────────────────────────

    def decide(self, user_input: str,
               task_complexity: float = 0.5,
               prefer: ModelTarget = ModelTarget.AUTO) -> RoutingDecision:
        """Decide whether to use cloud or companion model.

        Args:
            user_input: The user's request.
            task_complexity: Estimated complexity (0.0=trivial, 1.0=very complex).
            prefer: Preferred target.

        Returns:
            RoutingDecision with target and reasoning.
        """
        # Ensure loader is initialized
        if self._loader is None:
            from companion_model import CompanionModelLoader
            self._loader = CompanionModelLoader()

        # Check if companion model is available
        companion_info = self._loader.get_best_model(
            task_domain="text",
            min_f1=self.min_companion_f1,
        )
        companion_available = companion_info is not None

        # Explicit preference
        if prefer == ModelTarget.CLOUD:
            return RoutingDecision(
                target=ModelTarget.CLOUD,
                reason="Explicit cloud preference",
                companion_available=companion_available,
                complexity_score=task_complexity,
            )
        if prefer == ModelTarget.COMPANION and companion_available:
            return RoutingDecision(
                target=ModelTarget.COMPANION,
                reason="Explicit companion preference",
                companion_available=True,
                companion_model_id=companion_info.model_id if companion_info else "",
                complexity_score=task_complexity,
            )

        # No companion available → cloud
        if not companion_available:
            return RoutingDecision(
                target=ModelTarget.CLOUD,
                reason="No companion model available",
                companion_available=False,
                complexity_score=task_complexity,
            )

        # Too complex → cloud
        if task_complexity > self.max_complexity_for_companion:
            return RoutingDecision(
                target=ModelTarget.CLOUD,
                reason=f"Task too complex ({task_complexity:.2f} > {self.max_complexity_for_companion})",
                companion_available=True,
                companion_model_id=companion_info.model_id,
                complexity_score=task_complexity,
            )

        # Short/simple input → companion (save cost)
        if len(user_input) < 100 and task_complexity < 0.4:
            cost_saved = self._estimate_cost_saved(user_input)
            return RoutingDecision(
                target=ModelTarget.COMPANION,
                reason=f"Simple task (len={len(user_input)}, complexity={task_complexity:.2f})",
                companion_available=True,
                companion_model_id=companion_info.model_id,
                complexity_score=task_complexity,
                estimated_cost_saved=cost_saved,
            )

        # Default: try companion first, fall back to cloud
        # This is the "companion-first" strategy for cost saving
        cost_saved = self._estimate_cost_saved(user_input)
        decision = RoutingDecision(
            target=ModelTarget.COMPANION,
            reason=f"Companion-first strategy (complexity={task_complexity:.2f})",
            companion_available=True,
            companion_model_id=companion_info.model_id,
            complexity_score=task_complexity,
            estimated_cost_saved=cost_saved,
        )

        self._last_decision = decision
        return decision

    # ── Model Calls ────────────────────────────────────────────────────────

    async def call_companion(self, messages: List[dict],
                             tools: List[dict] = None) -> dict:
        """Call the companion model for inference.

        Returns:
            dict with keys: content, tool_calls, confidence, latency_ms.
        """
        if self._loader is None:
            from companion_model import CompanionModelLoader
            self._loader = CompanionModelLoader()

        if not self._loader.is_loaded():
            self._loader.load(task_domain="text")

        self._stats.total_attempts += 1
        t0 = time.monotonic()

        try:
            result = self._loader.generate(messages, tools)

            elapsed = (time.monotonic() - t0) * 1000
            self._stats.avg_latency_ms = (
                (self._stats.avg_latency_ms * (self._stats.total_attempts - 1) + elapsed)
                / self._stats.total_attempts
            )

            # Parse result
            if result.startswith("{"):
                # May be a tool call JSON
                try:
                    parsed = json.loads(result)
                    tool_calls = parsed.get("tool_calls", [])
                    content = ""
                except json.JSONDecodeError:
                    tool_calls = []
                    content = result
            else:
                tool_calls = []
                content = result

            success = bool(content) or bool(tool_calls)
            if success:
                self._stats.successes += 1

            return {
                "content": content,
                "tool_calls": tool_calls,
                "confidence": 0.7 if success else 0.3,
                "latency_ms": round(elapsed, 1),
                "source": "companion",
            }

        except Exception as e:
            self._stats.failures += 1
            logger.warning(f"Companion model call failed: {e}")
            return {
                "content": "",
                "tool_calls": [],
                "confidence": 0.0,
                "latency_ms": 0,
                "source": "companion",
                "error": str(e),
            }

    async def call_cloud(self, messages: List[dict],
                         tools: List[dict] = None) -> dict:
        """Call the cloud model (delegates to agent's client).

        Returns:
            dict with keys: content, tool_calls, confidence, latency_ms.
        """
        if self._agent is None:
            return {"content": "", "tool_calls": [], "confidence": 0.0,
                    "source": "cloud", "error": "No agent configured"}

        t0 = time.monotonic()

        try:
            kwargs = {
                "model": self._agent.model,
                "messages": messages,
                **self._agent.extra_params,
            }
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"

            resp = await self._agent.client.chat.completions.create(**kwargs)
            msg = resp.choices[0].message

            elapsed = (time.monotonic() - t0) * 1000

            tool_calls = []
            if msg.tool_calls:
                tool_calls = [
                    {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    }
                    for tc in msg.tool_calls
                ]

            return {
                "content": msg.content or "",
                "tool_calls": tool_calls,
                "confidence": 0.9,
                "latency_ms": round(elapsed, 1),
                "source": "cloud",
            }

        except Exception as e:
            return {
                "content": "",
                "tool_calls": [],
                "confidence": 0.0,
                "latency_ms": 0,
                "source": "cloud",
                "error": str(e),
            }

    # ── Response Quality ───────────────────────────────────────────────────

    def is_response_acceptable(self, response: dict) -> bool:
        """Check if a companion model response is good enough to return."""
        if response.get("error"):
            return False
        if response.get("confidence", 0) < self.min_confidence_threshold:
            return False
        if not response.get("content") and not response.get("tool_calls"):
            return False
        return True

    # ── Hybrid execution ───────────────────────────────────────────────────

    async def execute(self, messages: List[dict],
                      tools: List[dict] = None,
                      task_complexity: float = 0.5) -> dict:
        """Execute a request with automatic cloud/companion routing.

        This is the main entry point. It:
          1. Decides which model to use
          2. Calls the model
          3. Falls back to cloud if companion fails
          4. Tracks statistics
        """
        decision = self.decide(
            user_input=self._extract_user_text(messages),
            task_complexity=task_complexity,
        )

        logger.info(
            f"Routing decision: {decision.target.value} — {decision.reason}"
        )

        if decision.target == ModelTarget.COMPANION:
            # Try companion first
            response = await self.call_companion(messages, tools)

            if self.is_response_acceptable(response):
                if decision.estimated_cost_saved > 0:
                    self._stats.total_cost_saved += decision.estimated_cost_saved
                return response

            # Companion failed → fall back to cloud
            self._stats.fallbacks_to_cloud += 1
            logger.info("Companion response unacceptable, falling back to cloud")

        # Use cloud
        return await self.call_cloud(messages, tools)

    # ── Complexity Estimation ──────────────────────────────────────────────

    @staticmethod
    def estimate_complexity(user_input: str) -> float:
        """Estimate task complexity from user input.

        Returns a score from 0.0 (trivial) to 1.0 (very complex).
        """
        score = 0.0
        text = user_input.lower()

        # Length-based
        if len(user_input) > 500:
            score += 0.3
        elif len(user_input) > 200:
            score += 0.15

        # Complex indicators
        complex_keywords = [
            "analyze", "compare", "evaluate", "optimize", "refactor",
            "design", "architect", "implement", "debug", "investigate",
            "分析", "比较", "评估", "优化", "重构", "设计", "架构",
            "实现", "调试", "调查", "审查", "综合",
        ]
        score += 0.05 * sum(1 for kw in complex_keywords if kw in text)
        score = min(score, 0.7)  # Cap keyword contribution

        # Multi-step indicators
        multi_step = ["first", "then", "finally", "after that",
                      "首先", "然后", "最后", "接着", "之后"]
        if any(kw in text for kw in multi_step):
            score += 0.2

        # Code/API indicators (complex)
        code_indicators = ["write code", "implement function", "create api",
                          "写代码", "实现函数", "创建 API"]
        if any(kw in text for kw in code_indicators):
            score += 0.2

        return min(score, 1.0)

    # ── Helpers ────────────────────────────────────────────────────────────

    def _extract_user_text(self, messages: List[dict]) -> str:
        """Extract the last user message text."""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, str):
                    return content
                if isinstance(content, list):
                    parts = []
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            parts.append(block.get("text", ""))
                    return " ".join(parts)
        return ""

    def _estimate_cost_saved(self, user_input: str) -> float:
        """Estimate cost saved by using companion instead of cloud."""
        # Rough estimate: cloud ~$0.01/1K tokens, companion ~$0 (local)
        estimated_tokens = len(user_input) / 4 + 200  # input + output
        cost_per_1k = 0.01  # ~GPT-4o-mini pricing
        return round(estimated_tokens / 1000 * cost_per_1k, 4)

    def get_stats(self) -> dict:
        """Return companion model usage statistics."""
        return {
            "total_attempts": self._stats.total_attempts,
            "successes": self._stats.successes,
            "failures": self._stats.failures,
            "fallbacks_to_cloud": self._stats.fallbacks_to_cloud,
            "success_rate": round(self._stats.success_rate, 4),
            "avg_latency_ms": round(self._stats.avg_latency_ms, 1),
            "total_cost_saved": round(self._stats.total_cost_saved, 4),
            "last_decision": {
                "target": self._last_decision.target.value if self._last_decision else "none",
                "reason": self._last_decision.reason if self._last_decision else "",
            } if self._last_decision else None,
        }

    def reset_stats(self):
        """Reset performance statistics."""
        self._stats = CompanionStats()
        self._last_decision = None
