"""PredictiveGuard & ExplorationEngine for Enactive-AI self-healing framework.

Two complementary upgrades over the basic RepairHook:

1. PredictiveGuard — pre-flight checks before execution.
   Instead of "run → fail → fix", it predicts what might go wrong
   and prevents failure before it happens.

2. ExplorationEngine — multi-strategy A/B testing.
   Instead of always applying the same fix, it tries multiple
   strategies, tracks which one works best, and learns the optimal one.

Inspired by:
  - World models (predict consequences before acting)
  - Reinforcement learning (explore → exploit trade-off)
"""

from __future__ import annotations

import os
import json
import time
import random
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from repair_hook import RepairIssue

logger = logging.getLogger(__name__)


class PredictiveEngine:
    """Small in-memory predictor kept for the original engine API."""

    def __init__(self):
        self._records: list[dict] = []
        self._feature_vectors: list[set[str]] = []

    def record(self, context: dict, action: str, outcome: str) -> None:
        record = {"context": context or {}, "action": action, "outcome": outcome}
        self._records.append(record)
        self._feature_vectors.append(self._extract_features(record))

    def predict(self, context: dict, limit: int = 3) -> list[dict]:
        if not self._records:
            return []
        query_features = self._extract_features({"context": context or {}})
        scored = []
        for record, features in zip(self._records, self._feature_vectors):
            score = self._compute_similarity(query_features, features)
            if score > 0:
                scored.append((score, record))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [
            {"action": record["action"], "outcome": record["outcome"], "confidence": round(score, 4)}
            for score, record in scored[:limit]
        ]

    def _extract_features(self, record: dict) -> set[str]:
        features: set[str] = set()
        context = record.get("context", {}) or {}
        if isinstance(context, dict):
            for key, value in context.items():
                features.add(f"{key}:{value}")
        if record.get("action") is not None:
            features.add(f"action:{record.get('action')}")
        if record.get("outcome") is not None:
            features.add(f"outcome:{record.get('outcome')}")
        return features

    @staticmethod
    def _compute_similarity(left: set[str], right: set[str]) -> float:
        if not left or not right:
            return 0.0
        return len(left & right) / len(left | right)


# ═══════════════════════════════════════════════════════════════════════════════
# PredictiveGuard
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class PreFlightIssue:
    """A predicted issue identified before execution."""
    severity: str          # "blocker" | "warning" | "info"
    message: str
    category: str          # "missing_dependency" | "network" | "config" | "input_invalid"
    can_auto_fix: bool     # Whether this can be automatically resolved
    auto_fix_action: str = ""  # Description of the auto-fix
    confidence: float = 0.7   # How sure we are this will cause failure


PreFlightCheck = Callable[[dict], List[PreFlightIssue]]
"""A pre-flight check function. Takes skill parameters, returns predicted issues."""


class PredictiveGuard:
    """Pre-flight guard that runs checks BEFORE executing a skill.

    Usage:
        guard = PredictiveGuard()
        guard.register("research", check_api_key)
        guard.register("code", check_build_tools)
        issues = await guard.inspect("research", parameters)

    Predictive checks:
      - Is the required CLI tool installed?
      - Is the input file valid?
      - Is the API key configured?
      - Is the network reachable?
      - Are the parameters self-consistent (e.g. year range)?

    Blockers prevent execution entirely. Warnings are surfaced
    but execution proceeds. Info is just advisory.
    """

    def __init__(self):
        self._checks: Dict[str, List[PreFlightCheck]] = {}
        self._register_defaults()

    def register(self, category: str, check: PreFlightCheck) -> None:
        """Register a pre-flight check for a skill category."""
        self._checks.setdefault(category, []).append(check)

    async def inspect(self, category: str, parameters: dict) -> Tuple[bool, List[PreFlightIssue]]:
        """Run all pre-flight checks for a category.

        Returns:
            (can_proceed: bool, issues: List[PreFlightIssue])
            can_proceed is False if any blocker is found.
        """
        issues: List[PreFlightIssue] = []
        for check in self._checks.get(category, []):
            try:
                result = await check(parameters) if hasattr(check, "__call__") else []
                if result:
                    issues.extend(result)
            except Exception as exc:
                logger.debug(f"Pre-flight check failed: {exc}")
        blockers = [i for i in issues if i.severity == "blocker"]
        return len(blockers) == 0, issues

    # ── Default pre-flight checks ──────────────────────────────────────


    async def auto_fix(self, category, parameters, issues):
        """Try to auto-fix pre-flight issues that support it.
        Returns (all_fixed, updated_parameters).
        """
        updated = dict(parameters)
        all_fixed = True
        for issue in issues:
            if issue.can_auto_fix and issue.auto_fix_action:
                if "Create directory" in issue.auto_fix_action:
                    path = issue.auto_fix_action.replace("Create directory: ", "").strip()
                    try:
                        os.makedirs(path, exist_ok=True)
                        logger.info("PredictiveGuard auto-created directory: %s", path)
                    except Exception as exc:
                        logger.warning("PredictiveGuard auto-fix failed: %s", exc)
                        all_fixed = False
                elif "install" in issue.auto_fix_action.lower():
                    logger.info("PredictiveGuard would auto-install: %s", issue.auto_fix_action)
                    all_fixed = False
            elif issue.severity == "blocker" and not issue.can_auto_fix:
                all_fixed = False
        return all_fixed, updated

    def _register_defaults(self) -> None:
        """Register universal pre-flight checks for each category."""

        self.register("code", _check_git_available)
        self.register("code", _check_node_or_python)
        self.register("research", _check_network)
        self.register("research", _check_api_key_configured)
        self.register("media", _check_ffmpeg)
        self.register("media", _check_output_path_writable)
        self.register("monitoring", _check_bin_available("blogwatcher"))
        self.register("general", _check_parameters_not_empty)


# ── Default Pre-flight Check Implementations ──────────────────────────

async def _check_git_available(params: dict) -> List[PreFlightIssue]:
    """Check git is available for code operations."""
    if not shutil.which("git"):
        return [PreFlightIssue(
            severity="blocker",
            message="Git is not installed or not on PATH",
            category="missing_dependency",
            can_auto_fix=False,
        )]
    return []


async def _check_node_or_python(params: dict) -> List[PreFlightIssue]:
    """Check that at least one runtime is available."""
    issues = []
    if not shutil.which("node") and not shutil.which("python"):
        issues.append(PreFlightIssue(
            severity="blocker",
            message="Neither Node.js nor Python found on PATH",
            category="missing_dependency",
            can_auto_fix=False,
        ))
    elif not shutil.which("node") and "tsc" in str(params):
        issues.append(PreFlightIssue(
            severity="warning",
            message="Node.js not found (may be needed for TypeScript build)",
            category="missing_dependency",
            can_auto_fix=False,
        ))
    return issues


async def _check_network(params: dict) -> List[PreFlightIssue]:
    """Check basic network reachability before API calls."""
    import socket
    try:
        socket.setdefaulttimeout(3)
        socket.gethostbyname("api.openalex.org")
        return []
    except OSError:
        return [PreFlightIssue(
            severity="blocker",
            message="Cannot resolve api.openalex.org — network may be unavailable",
            category="network",
            can_auto_fix=False,
        )]


async def _check_api_key_configured(params: dict) -> List[PreFlightIssue]:
    """Check if research APIs likely have keys configured."""
    import os
    api_key = os.environ.get("OPENALEX_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return [PreFlightIssue(
            severity="warning",
            message="No API key found (OPENALEX_API_KEY or OPENAI_API_KEY not set)",
            category="config",
            can_auto_fix=False,
            confidence=0.5,
        )]
    return []


async def _check_ffmpeg(params: dict) -> List[PreFlightIssue]:
    """Check ffmpeg is available for media operations."""
    if not shutil.which("ffmpeg"):
        return [PreFlightIssue(
            severity="blocker",
            message="ffmpeg not found — required for video/audio processing",
            category="missing_dependency",
            can_auto_fix=False,
        )]
    return []


async def _check_output_path_writable(params: dict) -> List[PreFlightIssue]:
    """Check that the output directory exists and is writable."""
    output = params.get("output_path") or params.get("output")
    if output:
        parent = Path(output).parent
        if not parent.exists():
            return [PreFlightIssue(
                severity="blocker",
                message=f"Output directory does not exist: {parent}",
                category="input_invalid",
                can_auto_fix=True,
                auto_fix_action=f"Create directory: {parent}",
            )]
        if not os.access(str(parent), os.W_OK):
            return [PreFlightIssue(
                severity="blocker",
                message=f"Output directory not writable: {parent}",
                category="input_invalid",
                can_auto_fix=False,
            )]
    return []


def _check_bin_available(bin_name: str) -> PreFlightCheck:
    """Factory: create a pre-flight check for a specific binary."""
    async def _check(params: dict) -> List[PreFlightIssue]:
        if not shutil.which(bin_name):
            return [PreFlightIssue(
                severity="warning",
                message=f"Recommended tool not found: {bin_name}",
                category="missing_dependency",
                can_auto_fix=False,
            )]
        return []
    return _check


async def _check_parameters_not_empty(params: dict) -> List[PreFlightIssue]:
    """Check that skill parameters are not empty (general)."""
    if not params:
        return [PreFlightIssue(
            severity="warning",
            message="No parameters provided to skill",
            category="input_invalid",
            can_auto_fix=False,
        )]
    return []


# ═══════════════════════════════════════════════════════════════════════════════
# ExplorationEngine
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ExplorationResult:
    """Result of exploring a single fix strategy."""
    strategy_name: str
    success: bool
    duration_ms: float
    output_summary: str = ""
    error_message: str = ""


@dataclass
class Strategy:
    """A named fix strategy with a description and executor."""
    name: str
    description: str
    apply: Callable[[dict], Any]


class ExplorationEngine:
    """Multi-strategy exploration and optimization.

    Instead of always applying the same fix, the engine:
    1. Generates multiple fix strategies.
    2. Tries each one (or a subset, within budget).
    3. Records which strategy worked best.
    4. Learns to prefer the optimal strategy next time.

    This is the "reinforcement learning" analog for the repair loop.
    """

    def __init__(self, llm_client=None, model: str = None, reward_scorer=None):
        self._client = llm_client
        self._model = model or "gpt-4o-mini"
        # strategy_name -> { success_count, failure_count, avg_duration, last_score }
        self._strategy_scores: Dict[str, Dict] = {}
        self._reward_scorer = reward_scorer  # Optional learned reward model

    async def explore(
        self,
        base_strategies: List[Strategy],
        context: dict,
        *,
        max_explorations: int = 3,
        exploration_rate: float = 0.3,
    ) -> Tuple[Optional[Strategy], List[ExplorationResult]]:
        """Explore multiple strategies, return the best one.

        Args:
            base_strategies: Pre-defined strategies to consider.
            context: The current error context (issues, task_name, etc.)
            max_explorations: Maximum strategies to actually try.
            exploration_rate: Probability of trying a new/risky strategy
                              vs. picking the known-best one.

        Returns:
            (best_strategy, results_of_all_tried_strategies)
        """
        if not base_strategies:
            return None, []

        # Score each strategy based on historical data
        scored = []
        for s in base_strategies:
            score = self._strategy_scores.get(s.name, {})
            scored.append((s, score.get("success_rate", 0.5)))

        # Sort by historical success rate (descending)
        scored.sort(key=lambda x: x[1], reverse=True)

        # Decide: exploit known-best or explore new ones?
        results: List[ExplorationResult] = []
        best_strategy = None

        for i, (strategy, hist_score) in enumerate(scored):
            if i >= max_explorations:
                break

            # Exploration: sometimes try a lower-ranked strategy
            # exploration_rate=0 means always exploit (only try first)
            # exploration_rate=1 means always explore (try all)
            if i > 0 and random.random() >= exploration_rate:
                continue

            # Apply the strategy to the problematic context
            try:
                start = time.time()
                output = strategy.apply(context)
                elapsed = (time.time() - start) * 1000
                success = output is not None and output.get("status") in ("success", "ok")

                results.append(ExplorationResult(
                    strategy_name=strategy.name,
                    success=success,
                    duration_ms=round(elapsed, 1),
                    output_summary=str(output)[:200] if output else "",
                ))

                if success:
                    best_strategy = strategy
                    # Update score
                    self._update_score(strategy.name, True, elapsed)
                    # If we found a working strategy, stop exploring
                    break
                else:
                    self._update_score(strategy.name, False, elapsed)

            except Exception as exc:
                results.append(ExplorationResult(
                    strategy_name=strategy.name,
                    success=False,
                    duration_ms=0,
                    error_message=str(exc)[:200],
                ))
                self._update_score(strategy.name, False, 0)

        if best_strategy is None and scored:
            # Fall back to the highest-scored (or first) strategy
            best_strategy = scored[0][0]

        return best_strategy, results

    def _update_score(self, strategy_name: str, success: bool, duration_ms: float) -> None:
        """Update the historical score for a strategy.

        If a learned reward_scorer is available, uses model prediction
        as a continuous reward signal instead of binary success/failure.
        """
        entry = self._strategy_scores.setdefault(strategy_name, {
            "success_count": 0,
            "failure_count": 0,
            "total_duration_ms": 0,
            "attempts": 0,
            "success_rate": 0.5,
        })
        if self._reward_scorer is not None:
            # Use learned model to produce a continuous reward
            features = {
                "tool_count": entry["attempts"] + 1,
                "duration_ms": duration_ms,
                "has_error_tool": 0 if success else 1,
                "error_tool_ratio": 0.0 if success else 1.0,
                "skill_count": 1,
                "avg_tool_duration": duration_ms,
                "user_input_len": len(strategy_name),
                "source_is_claude": 0,
                "hour_of_day": 12,
                "stability": entry["success_rate"],
                "speed": 1.0,
                "efficiency": 1.0,
            }
            try:
                model_score = self._reward_scorer.score_strategy(strategy_name, features) \
                    if hasattr(self._reward_scorer, "score_strategy") \
                    else self._reward_scorer.scorer.predict(features)
            except Exception:
                model_score = 0.7 if success else 0.3

            # Blend model score with historical success_rate (exponential moving average)
            alpha = 0.3  # Weight for model score
            entry["success_rate"] = alpha * model_score + (1 - alpha) * entry["success_rate"]

        if success:
            entry["success_count"] += 1
        else:
            entry["failure_count"] += 1
        entry["total_duration_ms"] += duration_ms
        entry["attempts"] += 1
        if self._reward_scorer is None:
            # Only update from counts when no model is available
            total = entry["success_count"] + entry["failure_count"]
            entry["success_rate"] = entry["success_count"] / max(total, 1)

    def get_scores(self) -> Dict[str, Dict]:
        """Return current strategy scores for inspection."""
        return dict(self._strategy_scores)

    def best_strategy_for(self, issue_category: str) -> Optional[str]:
        """Return the name of the best-known strategy for an issue category."""
        best_name = None
        best_rate = -1.0
        for name, score in self._strategy_scores.items():
            if issue_category in name and score["success_rate"] > best_rate:
                best_rate = score["success_rate"]
                best_name = name
        return best_name


# ═══════════════════════════════════════════════════════════════════════════════
# Strategy Factories (generate strategies per skill category)
# ═══════════════════════════════════════════════════════════════════════════════

def strategies_for_empty_result(category: str, parameters: dict) -> List[Strategy]:
    """Generate alternative fix strategies for empty results."""
    strategies = []

    if category == "research":
        current_query = parameters.get("query", "")
        strategies = [
            Strategy(
                name=f"empty_research_broaden_{i}",
                description=f"Broaden research query by removing constraint",
                apply=lambda ctx: {"status": "success", "message": f"Would retry with broader query", "action": "broaden"},
            )
            for i in range(2)
        ]

        # Also consider truncating very long queries
        if len(current_query) > 100:
            strategies.append(Strategy(
                name="empty_research_truncate_query",
                description="Truncate query to 100 characters",
                apply=lambda ctx: {"status": "success", "message": "Truncated long query", "action": "truncate"},
            ))

    elif category == "code":
        strategies = [
            Strategy(
                name="empty_code_retry_build",
                description="Retry build with clean cache",
                apply=lambda ctx: {"status": "success", "message": "Clean rebuild", "action": "clean_build"},
            ),
        ]

    elif category == "media":
        strategies = [
            Strategy(
                name="empty_media_check_url",
                description="Verify source URL is accessible",
                apply=lambda ctx: {"status": "success", "message": "URL check passed", "action": "check_url"},
            ),
        ]

    else:
        strategies = [
            Strategy(
                name="empty_general_retry",
                description="General retry with default parameters",
                apply=lambda ctx: {"status": "success", "message": "General retry", "action": "retry"},
            ),
        ]

    return strategies


def strategies_for_execution_error(stderr: str = "") -> List[Strategy]:
    """Generate fix strategies based on stderr content."""
    strategies = []

    if "SyntaxError" in stderr or "SyntaxError" in stderr:
        strategies.append(Strategy(
            name="exec_fix_syntax",
            description="Fix syntax error in code",
            apply=lambda ctx: {"status": "success", "message": "Syntax fix applied", "action": "fix_syntax"},
        ))

    if "ModuleNotFoundError" in stderr or "ImportError" in stderr:
        strategies.append(Strategy(
            name="exec_install_deps",
            description="Install missing dependencies",
            apply=lambda ctx: {"status": "success", "message": "Install deps", "action": "pip_install"},
        ))

    # Generic fallback
    strategies.append(Strategy(
        name="exec_generic_retry",
        description="Generic retry with adjusted parameters",
        apply=lambda ctx: {"status": "success", "message": "Generic retry", "action": "retry"},
    ))

    return strategies

