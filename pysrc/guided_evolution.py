"""Predictive-Guided and Exploration-Guided Evolution.

This completes the Enactive-AI cycle:
  1. PREDICTIVE-GUIDED: before execution, the system predicts what might go
     wrong and pre-adapts parameters or pre-checks.
  2. EXPLORATION-GUIDED: during/after execution, multiple strategies are
     tried (A/B tested) and the best is learned for next time.

The original RepairHook was REACTIVE (fix after failure).
PredictiveGuard made it PROACTIVE (prevent before failure).
ExplorationEngine made it ADAPTIVE (try multiple, learn best).

This module completes the loop by:
  - Auto-elevating over time: reactive -> predictive -> exploration
  - Choosing the right mode per category based on past performance
  - Recording the "mode" so the agent can report its evolution level
"""

from __future__ import annotations

import time
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from repair_hook import RepairIssue, RepairHook
from predictive_engine import PredictiveGuard, ExplorationEngine, PreFlightIssue, Strategy

logger = logging.getLogger(__name__)


class EvolutionLevel(Enum):
    """Maturity level of the self-healing system."""
    REACTIVE = 1     # Run -> Fail -> Fix (basic RepairHook)
    PREDICTIVE = 2   # Pre-check -> Avoid failure (PredictiveGuard)
    EXPLORATION = 3  # Multi-strategy A/B -> Learn best (ExplorationEngine)
    AUTONOMOUS = 4   # All three combined, auto-tune parameters


@dataclass
class EvolutionState:
    """Current state of evolution for a skill category."""
    category: str
    level: EvolutionLevel
    max_level: EvolutionLevel = EvolutionLevel.AUTONOMOUS
    total_executions: int = 0
    total_failures: int = 0
    consecutive_successes: int = 0
    consecutive_failures: int = 0
    pre_checks_enabled: bool = False
    exploration_enabled: bool = False
    last_evolution_promotion: float = 0.0

    @property
    def success_rate(self) -> float:
        if self.total_executions == 0:
            return 1.0
        return 1.0 - (self.total_failures / max(self.total_executions, 1))


class GuidedEvolution:
    """Orchestrates the reactive -> predictive -> exploration evolution.

    Usage:
        evolution = GuidedEvolution()
        state = evolution.get_state("research")
        result = await evolution.execute(
            category="research",
            task=my_task,
            parameters={"query": "transformer"},
        )
        evolution.report(category="research", success=True)
    """

    PROMOTION_THRESHOLDS = {
        EvolutionLevel.REACTIVE: {
            "promote_after": 10,       # executions before considering promotion
            "min_success_rate": 0.7,    # must have this success rate
        },
        EvolutionLevel.PREDICTIVE: {
            "promote_after": 15,
            "min_success_rate": 0.8,
        },
    }

    LIGHTWEIGHT_MODELS = {"gpt-4o-mini", "gpt-3.5-turbo", "gemini-2.0-flash-lite", "qwen2.5-coder-7b", "llama-3.1-8b"}

    def __init__(self, model: str = None, reward_scorer=None, max_generations: int = 10):
        self._model = model or "gpt-4o-mini"
        self._states: Dict[str, EvolutionState] = {}
        self._repair_hook = RepairHook(model=self._model)
        self._predictive_guard = PredictiveGuard()
        self._exploration_engine = ExplorationEngine(reward_scorer=reward_scorer)
        self._reward_integration = None  # Set by install_reward_model_into_evolution()
        self.max_generations = max_generations
        self.generation = 0
        self._register_default_categories()
        # Lightweight model: start at REACTIVE, cap at PREDICTIVE (no exploration)
        model_lower = (self._model or "").lower()
        if any(m in model_lower for m in self.LIGHTWEIGHT_MODELS):
            for state in self._states.values():
                state.max_level = EvolutionLevel.PREDICTIVE

    def _mutate(self, seed: str) -> str:
        """Simple deterministic mutation used by the legacy sync API."""
        text = str(seed)
        return f"{text} :: variant-{self.generation + 1}"

    def evolve(self, seed: str, fitness_fn, target_fitness: float = 1.0):
        """Legacy synchronous evolution loop."""
        current = seed
        best = current
        best_score = float("-inf")
        self.generation = 0
        for generation in range(1, self.max_generations + 1):
            self.generation = generation
            candidate = self._mutate(current)
            score = float(fitness_fn(candidate))
            if score > best_score:
                best = candidate
                best_score = score
            if score >= target_fitness:
                return candidate
            current = candidate
        return best if best_score >= target_fitness else None

    def _register_default_categories(self) -> None:
        for cat in ("research", "code", "media", "monitoring", "general"):
            self._states[cat] = EvolutionState(
                category=cat,
                level=EvolutionLevel.REACTIVE,
            )

    def get_state(self, category: str) -> EvolutionState:
        """Get the current evolution state for a category."""
        return self._states.get(category, EvolutionState(
            category=category,
            level=EvolutionLevel.REACTIVE,
        ))

    async def execute(
        self,
        category: str,
        task: Any,
        *,
        task_name: str = "unknown",
        parameters: dict = None,
        validators: List[Any] = None,
        max_attempts: int = 3,
    ) -> Dict[str, Any]:
        """Execute a task with the best available mode for the category."""
        state = self.get_state(category)
        state.total_executions += 1
        parameters = parameters or {}
        validators = validators or []

        if state.level.value >= EvolutionLevel.PREDICTIVE.value:
            # Pre-flight check
            can_proceed, pre_issues = await self._predictive_guard.inspect(category, parameters)

            # Auto-fix what we can
            if pre_issues:
                auto_fixed, updated_params = await self._predictive_guard.auto_fix(
                    category, parameters, pre_issues
                )
                if auto_fixed:
                    parameters = updated_params

            blockers = [i for i in pre_issues if i.severity == "blocker"]
            if blockers:
                state.total_failures += 1
                state.consecutive_failures += 1
                state.consecutive_successes = 0
                return {
                    "status": "blocked",
                    "pre_flight_issues": [str(i.message) for i in blockers],
                    "mode": "predictive_blocked",
                }

        if state.level.value >= EvolutionLevel.EXPLORATION.value:
            # Use RepairHook with ExplorationEngine fallback
            # First, try normal execution with validators
            result = await self._repair_hook.repair(
                task,
                task_name=task_name,
                context={"skill_category": category, **(parameters or {})},
                validators=validators,
                max_attempts=max_attempts,
            )

            if result["status"] == "error" and result.get("trace"):
                # Try exploration
                last_issues = []
                if result["trace"].attempts:
                    last_issues = result["trace"].attempts[-1].issues
                if last_issues:
                    # Generate strategies based on issue categories
                    strategies = self._generate_strategies(category, last_issues, parameters)
                    if strategies:
                        best_strategy, explore_results = await self._exploration_engine.explore(
                            strategies,
                            parameters,
                            max_explorations=min(3, len(strategies)),
                        )
                        if best_strategy:
                            result["exploration_best"] = best_strategy.name

            success = result.get("status") == "success"
        else:
            # Basic reactive mode
            try:
                raw_result = await task() if callable(task) else task
                result = {"status": "success", "result": raw_result}
                success = True
            except Exception as exc:
                result = {"status": "error", "error": str(exc)}
                success = False

        # Track metrics
        if success:
            state.consecutive_successes += 1
            state.consecutive_failures = 0
        else:
            state.total_failures += 1
            state.consecutive_failures += 1
            state.consecutive_successes = 0

        # Consider auto-promotion
        await self._maybe_promote(state)

        result["evolution_level"] = state.level.name.lower()
        return result

    async def _maybe_promote(self, state: EvolutionState) -> None:
        """Check if the category should be promoted to the next level.

        If a learned reward model is available via _reward_integration,
        uses model-based decision instead of hardcoded thresholds.
        """
        if state.level == EvolutionLevel.AUTONOMOUS:
            return

        # Use learned reward model if available
        if self._reward_integration is not None:
            try:
                if self._reward_integration.should_promote(state):
                    old_level = state.level
                    if state.level == EvolutionLevel.REACTIVE:
                        state.level = EvolutionLevel.PREDICTIVE
                        state.pre_checks_enabled = True
                    elif state.level == EvolutionLevel.PREDICTIVE and state.max_level.value > EvolutionLevel.PREDICTIVE.value:
                        state.level = EvolutionLevel.EXPLORATION
                        state.exploration_enabled = True
                    elif state.level == EvolutionLevel.EXPLORATION and state.max_level.value > EvolutionLevel.EXPLORATION.value:
                        state.level = EvolutionLevel.AUTONOMOUS
                    if state.level != old_level:
                        state.last_evolution_promotion = time.time()
                        logger.info(
                            "Evolution promoted %s: %s -> %s (model-based, success_rate=%.2f, executions=%d)",
                            state.category, old_level.name, state.level.name,
                            state.success_rate, state.total_executions,
                        )
                return
            except Exception:
                logger.debug("Model-based promotion failed, falling back to thresholds", exc_info=True)

        # Fallback: hardcoded thresholds
        thresholds = self.PROMOTION_THRESHOLDS.get(state.level)
        if not thresholds:
            return

        if state.total_executions < thresholds["promote_after"]:
            return
        if state.success_rate < thresholds["min_success_rate"]:
            return

        # Promote!
        old_level = state.level
        if state.level == EvolutionLevel.REACTIVE:
            state.level = EvolutionLevel.PREDICTIVE
            state.pre_checks_enabled = True
        elif state.level == EvolutionLevel.PREDICTIVE and state.max_level.value > EvolutionLevel.PREDICTIVE.value:
            state.level = EvolutionLevel.EXPLORATION
            state.exploration_enabled = True
        elif state.level == EvolutionLevel.EXPLORATION and state.max_level.value > EvolutionLevel.EXPLORATION.value:
            state.level = EvolutionLevel.AUTONOMOUS

        state.last_evolution_promotion = time.time()
        logger.info(
            "Evolution promoted %s: %s -> %s (success_rate=%.2f, executions=%d)",
            state.category, old_level.name, state.level.name,
            state.success_rate, state.total_executions,
        )

    def _generate_strategies(
        self,
        category: str,
        issues: List[RepairIssue],
        parameters: dict,
    ) -> List[Strategy]:
        """Generate fix strategies based on issues."""
        from predictive_engine import strategies_for_empty_result, strategies_for_execution_error
        strategies: List[Strategy] = []
        for issue in issues:
            if issue.category == "empty_result":
                strategies.extend(strategies_for_empty_result(category, parameters))
            elif issue.category == "execution_error":
                strategies.extend(strategies_for_execution_error(issue.message))
        return strategies

    def report(self, category: str, success: bool) -> None:
        """Shortcut to update metrics after external execution."""
        state = self.get_state(category)
        state.total_executions += 1
        if success:
            state.consecutive_successes += 1
            state.consecutive_failures = 0
        else:
            state.total_failures += 1
            state.consecutive_failures += 1
            state.consecutive_successes = 0

    def evolution_summary(self) -> Dict[str, Dict]:
        """Get a summary of evolution states for all categories."""
        return {
            cat: {
                "level": state.level.name.lower(),
                "total_executions": state.total_executions,
                "failures": state.total_failures,
                "success_rate": round(state.success_rate, 3),
                "consecutive_successes": state.consecutive_successes,
                "pre_checks_enabled": state.pre_checks_enabled,
                "exploration_enabled": state.exploration_enabled,
            }
            for cat, state in self._states.items()
        }
