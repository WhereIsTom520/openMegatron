"""Integration hooks: replace rule-based scoring with learned reward model.

Provides a drop-in replacement for agent._score_task_trace() using the
trained RewardScorer from reward_model.py. Also integrates with
ExplorationEngine and GuidedEvolution for model-based decisions.

Usage:
    from reward_integration import install_reward_model
    integration = install_reward_model(agent, "model.pkl", backend="sklearn")
    # agent._score_task_trace now uses the learned model
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from reward_model import (
    RewardScorer,
    SklearnRewardScorer,
    TorchRewardScorer,
    extract_features,
)

logger = logging.getLogger(__name__)


def _load_scorer(model_path: str) -> RewardScorer:
    """Auto-detect backend from file extension and load."""
    path = model_path.lower()
    if path.endswith(".pt") or path.endswith(".pth"):
        return TorchRewardScorer.load(model_path)
    else:
        return SklearnRewardScorer.load(model_path)


class RewardIntegration:
    """Replaces rule-based scoring with learned reward model.

    Provides drop-in replacements for:
      - _score_task_trace() -> dict
      - ExplorationEngine._update_score()
      - GuidedEvolution._maybe_promote()
    """

    def __init__(self, scorer: RewardScorer):
        self._scorer = scorer

    def score_trace(self, trace: dict) -> dict:
        """Drop-in replacement for agent._score_task_trace().

        Uses the learned reward model to score a task_trace dict,
        falling back to rule-based dimensions for the decomposition.

        Args:
            trace: The task_trace dict from agent.chat().

        Returns:
            Dict with {reward, confidence, dimensions} matching the
            original _score_task_trace() format.
        """
        # Extract features and get model prediction
        features = extract_features(trace)
        try:
            model_reward = self._scorer.predict(features)
        except Exception:
            model_reward = 0.5

        # Compute rule-based dimensions for transparency (still useful)
        tool_calls = trace.get("tool_calls", []) or []
        if not tool_calls:
            return {
                "reward": round(model_reward, 4),
                "confidence": round(max(0.35, model_reward), 4),
                "dimensions": {
                    "success": bool(trace.get("final_answer")),
                    "stability": 1.0,
                    "speed": 1.0,
                    "efficiency": 1.0,
                    "tool_count": 0,
                    "duration_ms": 0.0,
                },
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

        # Model reward replaces the weighted combination
        reward = max(0.0, min(1.0, model_reward))
        confidence = max(0.0, min(1.0, 0.50 * stability + 0.25 * speed + 0.25 * model_reward))

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
                "duration_ms": round(total_duration_ms, 2),
            },
        }

    def score_strategy(self, strategy_name: str, context: dict) -> float:
        """Score a repair strategy for ExplorationEngine.

        Args:
            strategy_name: Name of the strategy being evaluated.
            context: Dict with keys like category, error_type, parameters.

        Returns:
            Predicted success probability for this strategy in this context.
        """
        # Build features from strategy context
        features = {
            "tool_count": 1,  # Repair is typically a single fix
            "duration_ms": 0.0,
            "has_error_tool": 0,
            "error_tool_ratio": 0.0,
            "skill_count": 1,
            "avg_tool_duration": 0.0,
            "user_input_len": len(str(context.get("parameters", {}))),
            "source_is_external_agent": 0,
            "hour_of_day": 12,
            "stability": 0.5,
            "speed": 0.5,
            "efficiency": 0.5,
        }
        try:
            return self._scorer.predict(features)
        except Exception:
            return 0.5

    def should_promote(self, state) -> bool:
        """Model-based promotion decision for GuidedEvolution.

        Instead of hardcoded thresholds (10 executions, 0.7 success_rate),
        uses the reward model to predict whether promoting will improve outcomes.

        Args:
            state: EvolutionState with category, level, total_executions,
                   total_failures, consecutive_successes, etc.

        Returns:
            True if the evolution level should be promoted.
        """
        # Build features from evolution state
        features = {
            "tool_count": state.total_executions,
            "duration_ms": 0.0,
            "has_error_tool": 1 if state.total_failures > 0 else 0,
            "error_tool_ratio": state.total_failures / max(state.total_executions, 1),
            "skill_count": 1,
            "avg_tool_duration": 0.0,
            "user_input_len": 0,
            "source_is_external_agent": 0,
            "hour_of_day": 12,
            "stability": state.success_rate,
            "speed": 1.0,
            "efficiency": 1.0,
        }

        try:
            score = self._scorer.predict(features)
        except Exception:
            score = 0.5

        # Promote if model confidence is high and success rate is good
        current_success_rate = state.success_rate
        min_rate = 0.7 if state.total_executions >= 10 else 0.85
        return score > 0.6 and current_success_rate >= min_rate

    @property
    def scorer(self) -> RewardScorer:
        """Access the underlying scorer."""
        return self._scorer


def install_reward_model(
    agent: Any,
    model_path: str,
    backend: str = "sklearn",
) -> RewardIntegration:
    """Install a learned reward model into an agent instance.

    Monkey-patches agent._score_task_trace to use the learned model
    instead of the hardcoded rule-based formula.

    Args:
        agent: A YuanGeAgent instance.
        model_path: Path to a saved model file (.pkl or .pt).
        backend: "sklearn" or "torch" (auto-detected from file extension).

    Returns:
        RewardIntegration instance for direct access.
    """
    scorer = _load_scorer(model_path)
    integration = RewardIntegration(scorer)

    # Replace the scoring method
    agent._score_task_trace = integration.score_trace

    # Store reference for later use
    agent._reward_integration = integration

    logger.info(
        "Reward model installed: %s (backend=%s)",
        model_path,
        type(scorer).__name__,
    )
    return integration


def install_reward_model_into_evolution(
    evolution: Any,
    model_path: str,
) -> RewardIntegration:
    """Install a learned reward model into GuidedEvolution and ExplorationEngine.

    Args:
        evolution: A GuidedEvolution instance.
        model_path: Path to a saved model file.

    Returns:
        RewardIntegration instance.
    """
    scorer = _load_scorer(model_path)
    integration = RewardIntegration(scorer)

    # Inject into ExplorationEngine
    if hasattr(evolution, "_exploration_engine"):
        evolution._exploration_engine._reward_scorer = integration

    # Store for GuidedEvolution._maybe_promote
    evolution._reward_integration = integration

    logger.info("Reward model installed into evolution system: %s", model_path)
    return integration
