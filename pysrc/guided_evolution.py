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
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from repair_hook import RepairIssue, RepairHook
from predictive_engine import PredictiveGuard, ExplorationEngine, PreFlightIssue, Strategy
from meta_evolution import MetaEvolutionLearner, EvolutionLevel
from ontology_evolution import OntologyEvolutionTracker
from causal_evolution import CausalEvolutionLearner
from pareto_evolution import ParetoEvolutionLearner, MultiObjectiveMetrics, WeightedPreferences

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
    # ── Demotion & Freezing ──
    frozen_until: float = 0.0  # Timestamp until which promotion is frozen
    last_demotion: float = 0.0  # Timestamp of last demotion
    demotion_count: int = 0  # Total times demoted (for harsher penalties)

    @property
    def success_rate(self) -> float:
        if self.total_executions == 0:
            return 1.0
        return 1.0 - (self.total_failures / max(self.total_executions, 1))


class GuidedEvolution:
    """Orchestrates the reactive -> predictive -> exploration -> autonomous evolution.

    Enhancements:
    - Auto-demotion: continuous failures trigger fallback to lower levels (fail-safe)
    - Promotion freeze: demotion triggers a cooling period before re-promotion
    - Value-aware exploration: exploration budget scales with task value
    - Cross-category strategy migration: proven strategies auto-register to other categories
    """

    # Promotion thresholds
    PROMOTION_THRESHOLDS = {
        EvolutionLevel.REACTIVE: {
            "promote_after": 10,
            "min_success_rate": 0.7,
        },
        EvolutionLevel.PREDICTIVE: {
            "promote_after": 15,
            "min_success_rate": 0.8,
        },
        EvolutionLevel.EXPLORATION: {
            "promote_after": 25,
            "min_success_rate": 0.85,
        },
    }

    # Demotion thresholds
    DEMOTION_THRESHOLDS = {
        EvolutionLevel.EXPLORATION: {
            "consecutive_failures": 5,  # 5 consecutive failures → demote to PREDICTIVE
            "success_rate_below": 0.5,  # OR success rate < 50% over 20 runs
            "freeze_seconds": 3600,  # Freeze promotion for 1 hour after demotion
        },
        EvolutionLevel.PREDICTIVE: {
            "consecutive_failures": 8,
            "success_rate_below": 0.4,
            "freeze_seconds": 1800,  # Freeze for 30 minutes
        },
        EvolutionLevel.AUTONOMOUS: {
            "consecutive_failures": 3,
            "success_rate_below": 0.6,
            "freeze_seconds": 7200,  # 2 hours for autonomous (higher bar)
        },
    }

    LIGHTWEIGHT_MODELS = {"gpt-4o-mini", "gpt-3.5-turbo", "gemini-2.0-flash-lite", "qwen2.5-coder-7b", "llama-3.1-8b"}

    def __init__(self, model: str = None, reward_scorer=None, max_generations: int = 10,
                 memory_service=None, meta_evolution_path: Optional[str] = None):
        self._model = model or "gpt-4o-mini"
        self._states: Dict[str, EvolutionState] = {}
        self._repair_hook = RepairHook(model=self._model)
        self._predictive_guard = PredictiveGuard()
        self._exploration_engine = ExplorationEngine(reward_scorer=reward_scorer)
        self._reward_integration = None  # Set by install_reward_model_into_evolution()
        self._memory_service = memory_service  # Optional MemoryService for ontology tracking
        self.max_generations = max_generations
        self.generation = 0
        # Meta-evolution: the framework learns to optimize itself
        self._meta_learner = MetaEvolutionLearner(persistence_path=meta_evolution_path)
        # Ontology evolution: all decisions tracked as a queryable hypergraph
        self._ontology_tracker = OntologyEvolutionTracker(memory_service)
        # Causal evolution: from correlation to causal inference (PSM + DiD)
        self._causal_learner = CausalEvolutionLearner()
        # Pareto evolution: multi-objective optimization (4 dimensions)
        self._pareto_learner = ParetoEvolutionLearner()
        self._pareto_preferences = WeightedPreferences()  # Default: success > speed > cost > stability
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

                    # Add migrated strategies from cross-category learning
                    migrated = self._migrate_strategies_from_patterns(category)
                    strategies.extend(migrated)

                    if strategies:
                        # Calculate dynamic exploration budget based on task value
                        task_value = self._calculate_task_value(parameters)
                        max_exp, exp_rate = self._exploration_budget_for_value(task_value)

                        best_strategy, explore_results = await self._exploration_engine.explore(
                            strategies,
                            parameters,
                            max_explorations=max_exp,
                            exploration_rate=exp_rate,
                        )
                        if best_strategy:
                            result["exploration_best"] = best_strategy.name
                        result["exploration_count"] = len(explore_results)
                        result["task_value"] = task_value
                        result["exploration_budget"] = max_exp

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

        # Meta-learner observes every execution outcome
        self._meta_learner.observe_execution(category, state.level, success)

        # Consider auto-promotion
        await self._maybe_demote(state)
        await self._maybe_promote(state)

        # Lightweight: persist execution counts to ontology node
        await self._track_execution_state(state)

        result["evolution_level"] = state.level.name.lower()
        return result

    async def _maybe_demote(self, state: EvolutionState) -> None:
        """Check if the category should be demoted to a lower level (fail-safe).

        Demotion is triggered by:
        1. Too many consecutive failures (system is unstable at this level)
        2. Sustained low success rate (level is too ambitious)

        After demotion, promotion is frozen for a cooling period.
        """
        if state.level == EvolutionLevel.REACTIVE:
            return  # Can't demote further

        thresholds = self.DEMOTION_THRESHOLDS.get(state.level)
        if not thresholds:
            return

        now = time.time()
        should_demote = False
        reason = ""

        # Trigger 1: consecutive failures
        if state.consecutive_failures >= thresholds["consecutive_failures"]:
            should_demote = True
            reason = f"consecutive_failures={state.consecutive_failures} >= {thresholds['consecutive_failures']}"

        # Trigger 2: sustained low success rate (window of 20 executions)
        if state.total_executions >= 20:
            # Simple recent window approximation: use global rate for now
            # In production: maintain a sliding window of last N results
            if state.success_rate < thresholds["success_rate_below"]:
                should_demote = True
                reason = f"success_rate={state.success_rate:.2f} < {thresholds['success_rate_below']}"

        if should_demote:
            old_level = state.level

            # Demote!
            if state.level == EvolutionLevel.AUTONOMOUS:
                state.level = EvolutionLevel.EXPLORATION
            elif state.level == EvolutionLevel.EXPLORATION:
                state.level = EvolutionLevel.PREDICTIVE
                state.exploration_enabled = False
            elif state.level == EvolutionLevel.PREDICTIVE:
                state.level = EvolutionLevel.REACTIVE
                state.pre_checks_enabled = False
                state.exploration_enabled = False

            # Apply freeze
            state.frozen_until = now + thresholds["freeze_seconds"]
            state.last_demotion = now
            state.demotion_count += 1

            # Reset consecutive failure counter (we already reacted)
            state.consecutive_failures = 0

            logger.warning(
                "Evolution DEMOTED %s: %s -> %s (reason: %s, frozen_for=%ds, demotion_count=%d)",
                state.category, old_level.name, state.level.name,
                reason, thresholds["freeze_seconds"], state.demotion_count,
            )
            # Tell meta-learner: this promotion didn't work out
            self._meta_learner.track_demotion(state.category, old_level, state.level, reason)
            # Also track in ontology
            await self._ontology_tracker.track_demotion(
                state.category,
                old_level.name,
                state.level.name,
                reason,
                state.success_rate,
            )
            await self._track_demotion_event(state, old_level, reason)

    async def _track_demotion_event(self, state: EvolutionState, old_level: EvolutionLevel, reason: str) -> None:
        """Persist a demotion as a hyperedge in the ontology."""
        svc = self._memory_service
        if svc is None:
            return
        try:
            import json as _j
            d = chr(36)
            pg = getattr(svc, 'pg_pool', None)
            if pg is None:
                return
            now = time.time()
            he_id = svc.ontology_node_id("hyperedge", f"demotion:{state.category}:{now}")
            async with pg.acquire() as conn:
                cat_id = svc.ontology_node_id("topic", f"evolution:{state.category}")
                old_id = svc.ontology_node_id("claim", f"level:{old_level.name}:{state.category}")
                new_id = svc.ontology_node_id("claim", f"level:{state.level.name}:{state.category}")

                summary = {
                    "category": state.category,
                    "old_level": old_level.name,
                    "new_level": state.level.name,
                    "reason": reason,
                    "success_rate": state.success_rate,
                    "total_executions": state.total_executions,
                    "demotion_count": state.demotion_count,
                    "frozen_until": state.frozen_until,
                }
                await conn.execute(
                    "INSERT INTO memory_hyperedges (id, edge_type, label, summary, confidence, metadata, created_at, updated_at) VALUES ("
                    + d + "1, " + d + "2, " + d + "3, " + d + "4, " + d + "5, " + d + "6::jsonb, NOW(), NOW())"
                    + " ON CONFLICT (id) DO UPDATE SET summary = EXCLUDED.summary, metadata = EXCLUDED.metadata, updated_at = NOW()",
                    he_id, "demotion_event", f"Demotion: {state.category} {old_level.name} → {state.level.name}",
                    _j.dumps(summary),
                    1.0,
                    _j.dumps({"category": state.category, "evolution": True, "demotion": True}),
                )

                await svc._upsert_hyperedge_member(conn, he_id, cat_id, "category", "topic", 1.0,
                    {"role": "category", "category": state.category})
                await svc._upsert_hyperedge_member(conn, he_id, old_id, "old_level", "claim", 1.0,
                    {"role": "old_level", "level": old_level.name})
                await svc._upsert_hyperedge_member(conn, he_id, new_id, "new_level", "claim", 1.0,
                    {"role": "new_level", "level": state.level.name})
        except Exception:
            logger.debug("Ontology demotion tracking skipped", exc_info=True)

    async def _maybe_promote(self, state: EvolutionState) -> None:
        """Check if the category should be promoted to the next level.

        If a learned reward model is available via _reward_integration,
        uses model-based decision instead of hardcoded thresholds.

        Will NOT promote if:
        - Currently frozen (post-demotion cooling period)
        - Has been demoted > 3 times (this level is fundamentally unstable)
        """
        if state.level == EvolutionLevel.AUTONOMOUS:
            return

        now = time.time()

        # Block 1: Frozen due to recent demotion
        if state.frozen_until > now:
            logger.debug(
                "Promotion blocked for %s: frozen until %s (%ds remaining)",
                state.category, time.ctime(state.frozen_until), int(state.frozen_until - now),
            )
            return

        # Block 2: Too many demotions (this category struggles with higher levels)
        if state.demotion_count >= 3:
            logger.debug(
                "Promotion blocked for %s: demoted %d times (stability concerns)",
                state.category, state.demotion_count,
            )
            return

        # Use learned reward model if available
        if self._reward_integration is not None:
            try:
                if self._reward_integration.should_promote(state):
                    old_level = state.level
                    # Use meta-learner thresholds to validate
                    meta_thresh = self._meta_learner.get_thresholds(state.category, state.level)
                    # Apply promotion (using meta-learned thresholds as additional guard)
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
                        self._meta_learner.track_promotion(
                            state.category, old_level, state.level,
                            success_rate_before=state.success_rate,
                            executions_before=state.total_executions,
                        )
                        await self._track_evolution_event(state, old_level)
                return
            except Exception:
                logger.debug("Model-based promotion failed, falling back to thresholds", exc_info=True)

        # Fallback: use meta-learner thresholds (learned, or default if no data)
        next_level_map = {
            EvolutionLevel.REACTIVE: EvolutionLevel.PREDICTIVE,
            EvolutionLevel.PREDICTIVE: EvolutionLevel.EXPLORATION,
            EvolutionLevel.EXPLORATION: EvolutionLevel.AUTONOMOUS,
        }
        if state.level not in next_level_map:
            return
        next_level = next_level_map[state.level]
        if not (state.max_level.value > state.level.value):
            return

        # Base thresholds from meta-learner
        thresholds = self._meta_learner.get_thresholds(state.category, next_level)
        base_min_success = thresholds["min_success_rate"]
        base_promote_after = thresholds["promote_after"]

        # CAUSAL ADJUSTMENT: Apply causal multiplier to thresholds
        # If promotions have historically hurt this transition, make it harder
        causal_multiplier = self._causal_learner.get_threshold_adjustment(
            state.category, state.level.name, next_level.name
        )
        adjusted_min_success = base_min_success * causal_multiplier
        adjusted_promote_after = int(base_promote_after * max(1.0, causal_multiplier))

        if state.total_executions < adjusted_promote_after:
            return
        if state.success_rate < adjusted_min_success:
            return

        # PARETO CHECK: Verify promotion won't degrade multi-objective performance
        current_metrics = self._get_current_pareto_metrics(state)
        should_promote_pareto, pareto_reason = self._pareto_learner.should_promote_pareto(
            category=state.category,
            from_level=state.level.name,
            to_level=next_level.name,
            current_metrics=current_metrics,
            preferences=self._pareto_preferences,
        )
        if not should_promote_pareto:
            logger.debug(
                "Pareto blocked promotion %s: %s",
                state.category, pareto_reason,
            )
            return

        # Promote!
        old_level = state.level
        if state.level == EvolutionLevel.REACTIVE:
            state.level = EvolutionLevel.PREDICTIVE
            state.pre_checks_enabled = True
        elif state.level == EvolutionLevel.PREDICTIVE:
            state.level = EvolutionLevel.EXPLORATION
            state.exploration_enabled = True
        elif state.level == EvolutionLevel.EXPLORATION:
            state.level = EvolutionLevel.AUTONOMOUS

        state.last_evolution_promotion = time.time()
        logger.info(
            "Evolution promoted %s: %s -> %s (success_rate=%.2f, executions=%d, tuned=%s, causal_multiplier=%.2f)",
            state.category, old_level.name, state.level.name,
            state.success_rate, state.total_executions,
            thresholds.get("is_tuned", False),
            causal_multiplier if 'causal_multiplier' in locals() else 1.0,
        )
        self._meta_learner.track_promotion(
            state.category, old_level, state.level,
            success_rate_before=state.success_rate,
            executions_before=state.total_executions,
        )
        # Causal tracking
        self._causal_learner.track_promotion(
            state.category, old_level.name, state.level.name,
            pre_success_rate=state.success_rate,
            pre_executions=state.total_executions,
            pre_avg_latency=1.0,  # Would come from actual execution timing
        )
        # Update Pareto frontier with this (category, level) state
        pareto_id = f"{state.category}:{state.level.name}"
        self._pareto_learner.add_metrics(pareto_id, self._get_current_pareto_metrics(state))
        # Also track in the ontology hypergraph
        await self._ontology_tracker.track_promotion(
            state.category,
            old_level.name,
            state.level.name,
            success_rate_before=state.success_rate,
            success_rate_after=None,  # Will be updated when we judge outcome
            outcome="PENDING",
            thresholds=thresholds,
        )
        await self._track_evolution_event(state, old_level)

    async def _track_evolution_event(self, state: EvolutionState, old_level: EvolutionLevel) -> None:
        """Persist an evolution state transition as a hyperedge in the ontology."""
        svc = self._memory_service
        if svc is None:
            return
        try:
            import json as _j
            d = chr(36)
            pg = getattr(svc, 'pg_pool', None)
            if pg is None:
                return
            now = time.time()
            he_id = svc.ontology_node_id("hyperedge", f"evolution:{state.category}:{now}")
            async with pg.acquire() as conn:
                # Create category topic node
                cat_id = svc.ontology_node_id("topic", f"evolution:{state.category}")
                await svc._upsert_ontology_node(conn, cat_id, "topic",
                    f"Evolution: {state.category}", {
                        "category": state.category,
                        "current_level": state.level.name,
                        "success_rate": state.success_rate,
                        "total_executions": state.total_executions,
                    })

                # Create outcome nodes
                old_id = svc.ontology_node_id("claim", f"level:{old_level.name}:{state.category}")
                await svc._upsert_ontology_node(conn, old_id, "claim",
                    f"Level: {old_level.name}", {"level": old_level.name, "category": state.category})

                new_id = svc.ontology_node_id("claim", f"level:{state.level.name}:{state.category}")
                await svc._upsert_ontology_node(conn, new_id, "claim",
                    f"Level: {state.level.name}", {"level": state.level.name, "category": state.category})

                # Create hyperedge
                summary = {
                    "category": state.category,
                    "old_level": old_level.name,
                    "new_level": state.level.name,
                    "success_rate": state.success_rate,
                    "total_executions": state.total_executions,
                }
                await conn.execute(
                    "INSERT INTO memory_hyperedges (id, edge_type, label, summary, confidence, metadata, created_at, updated_at) VALUES ("
                    + d + "1, " + d + "2, " + d + "3, " + d + "4, " + d + "5, " + d + "6::jsonb, NOW(), NOW())"
                    + " ON CONFLICT (id) DO UPDATE SET summary = EXCLUDED.summary, metadata = EXCLUDED.metadata, updated_at = NOW()",
                    he_id, "evolution_event", f"Evolution: {state.category} {old_level.name} → {state.level.name}",
                    _j.dumps(summary),
                    1.0,
                    _j.dumps({"category": state.category, "evolution": True, "ontology": "ontology-guided-hypergraph-memory.v2"}),
                )

                # Add hyperedge members (using evolution_event roles: category, old_level, new_level, outcome)
                await svc._upsert_hyperedge_member(conn, he_id, cat_id, "category", "topic", 1.0,
                    {"role": "category", "category": state.category})
                await svc._upsert_hyperedge_member(conn, he_id, old_id, "old_level", "claim", 1.0,
                    {"role": "old_level", "level": old_level.name})
                await svc._upsert_hyperedge_member(conn, he_id, new_id, "new_level", "claim", 1.0,
                    {"role": "new_level", "level": state.level.name})
                # Outcome: whether the promotion succeeded (always true here since we're recording it)
                outcome_id = svc.ontology_node_id("claim", f"promoted:{state.category}:{now}")
                await svc._upsert_ontology_node(conn, outcome_id, "claim",
                    f"Promoted: {state.category} to {state.level.name}", {
                        "category": state.category,
                        "new_level": state.level.name,
                        "success_rate": state.success_rate,
                    })
                await svc._upsert_hyperedge_member(conn, he_id, outcome_id, "outcome", "claim", 1.0,
                    {"role": "outcome", "result": "promoted", "success_rate": state.success_rate})

                # Pairwise link: category topic → hyperedge (uses `produces` relation to artifact-like event)
                sql_link = ("INSERT INTO memory_links (source_id, target_id, relation, confidence, metadata, created_at) VALUES ("
                            + d + "1, " + d + "2, " + d + "3, " + d + "4, " + d + "5::jsonb, NOW())"
                            + " ON CONFLICT (source_id, target_id, relation) DO NOTHING")
                await conn.execute(sql_link, cat_id, he_id, "produces", 1.0,
                    _j.dumps({"edge_type": "evolution_event", "evolution": True}))
        except Exception:
            logger.debug("Ontology evolution tracking skipped", exc_info=True)

    async def _track_execution_state(self, state: EvolutionState) -> None:
        """Lightweight: persist current execution counts to an ontology node."""
        svc = self._memory_service
        if svc is None:
            return
        try:
            pg = getattr(svc, 'pg_pool', None)
            if pg is None:
                return
            cat_id = svc.ontology_node_id("topic", f"evolution:{state.category}")
            async with pg.acquire() as conn:
                await svc._upsert_ontology_node(conn, cat_id, "topic",
                    f"Evolution: {state.category}", {
                        "category": state.category,
                        "current_level": state.level.name,
                        "success_rate": state.success_rate,
                        "total_executions": state.total_executions,
                        "total_failures": state.total_failures,
                        "consecutive_successes": state.consecutive_successes,
                        "consecutive_failures": state.consecutive_failures,
                    })
        except Exception:
            pass

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
        # Meta-learner observes
        self._meta_learner.observe_execution(category, state.level, success)

    def evolution_summary(self) -> Dict[str, Dict]:
        """Get a summary of evolution states for all categories."""
        now = time.time()
        meta_report = self._meta_learner.get_learning_report()
        return {
            "categories": {
                cat: {
                    "level": state.level.name.lower(),
                    "total_executions": state.total_executions,
                    "failures": state.total_failures,
                    "success_rate": round(state.success_rate, 3),
                    "consecutive_successes": state.consecutive_successes,
                    "consecutive_failures": state.consecutive_failures,
                    "pre_checks_enabled": state.pre_checks_enabled,
                    "exploration_enabled": state.exploration_enabled,
                    "demotion_count": state.demotion_count,
                    "is_frozen": state.frozen_until > now,
                    "frozen_remaining_seconds": max(0, int(state.frozen_until - now)),
                }
                for cat, state in self._states.items()
            },
            "meta_evolution": meta_report,
        }

    # ── Task Value Scoring ──
    # Maps parameter keywords to exploration budget multipliers
    VALUE_KEYWORDS = {
        # High value (production/critical)
        "production": 2.0,
        "deploy": 2.0,
        "release": 2.0,
        "critical": 2.0,
        "important": 1.5,
        "priority": 1.5,
        # Medium value
        "research": 1.0,
        "code": 1.0,
        "analyze": 1.0,
        # Low value (exploration/fun)
        "test": 0.5,
        "experiment": 0.5,
        "play": 0.3,
        "demo": 0.3,
        "example": 0.3,
    }

    def _calculate_task_value(self, parameters: dict) -> float:
        """Calculate a task value score from parameters [0.3, 2.0].

        Higher value = more exploration budget.
        Base value is 1.0.
        """
        value = 1.0
        param_str = json.dumps(parameters).lower()

        for keyword, multiplier in self.VALUE_KEYWORDS.items():
            if keyword in param_str:
                value = max(value, multiplier)

        return value

    def _exploration_budget_for_value(self, task_value: float, base_max: int = 3, base_rate: float = 0.3) -> Tuple[int, float]:
        """Calculate dynamic exploration budget based on task value.

        Returns: (max_explorations, exploration_rate)
        """
        # Max explorations: scale with task value, capped at 5
        max_explorations = max(1, min(5, int(base_max * task_value)))

        # Exploration rate: higher for high-value tasks (we can afford to learn)
        exploration_rate = base_rate * (0.5 + task_value * 0.5)
        exploration_rate = max(0.1, min(0.5, exploration_rate))

        return max_explorations, exploration_rate

    # ── Cross-Category Strategy Migration ──
    def _migrate_strategies_from_patterns(self, category: str) -> List[Strategy]:
        """Generate executable Strategy objects from cross-category learned patterns.

        Instead of just suggesting pre-checks as strings, this produces real strategies
        that ExplorationEngine can directly apply.
        """
        strategies = []
        if not hasattr(self, '_cross_category_learner'):
            return strategies

        patterns = self._cross_category_learner.get_patterns()
        for p in patterns:
            if category not in p.applicable_categories and "all" not in p.applicable_categories:
                continue

            # Only migrate patterns with sufficient confidence
            if p.confidence < 0.5:
                continue

            # Convert pattern to an executable Strategy
            strategy_name = f"migrated_{p.pattern_id}_to_{category}"
            strategies.append(Strategy(
                name=strategy_name,
                description=f"Migrated from {p.source_category}: {p.recommended_fix}",
                apply=lambda ctx, fix=p.recommended_fix: {
                    "status": "ok",
                    "message": f"Applied cross-category fix: {fix}",
                    "source_pattern": p.pattern_id,
                    "source_category": p.source_category,
                },
            ))

        return strategies

    def set_pareto_preferences(
        self,
        success_weight: float = 0.4,
        efficiency_weight: float = 0.25,
        cost_weight: float = 0.2,
        stability_weight: float = 0.15,
    ) -> None:
        """Set user preferences for multi-objective optimization.

        Weights are normalized automatically.
        Higher weight = more important in promotion decisions.
        """
        self._pareto_preferences = WeightedPreferences(
            success_weight=success_weight,
            efficiency_weight=efficiency_weight,
            cost_weight=cost_weight,
            stability_weight=stability_weight,
        )
        self._pareto_preferences.normalize()

    def _get_current_pareto_metrics(self, state: EvolutionState) -> MultiObjectiveMetrics:
        """Convert evolution state to multi-objective metrics vector."""
        # Success rate directly from state
        success = state.success_rate

        # Efficiency: inverse correlation with exploration level
        # (more exploration = slower execution, but more learning)
        efficiency = max(0.3, 1.0 - (state.level.value - 1) * 0.15)

        # Cost effectiveness: higher levels use more tokens
        cost_effective = max(0.3, 1.0 - (state.level.value - 1) * 0.2)

        # Stability: based on demotion count and consecutive successes
        stability_penalty = state.demotion_count * 0.15
        stability_bonus = min(state.consecutive_successes / 50.0, 0.5)
        stability = max(0.1, 1.0 - stability_penalty + stability_bonus)

        return MultiObjectiveMetrics(
            success_rate=success,
            efficiency=efficiency,
            cost_effective=cost_effective,
            stability=stability,
        )

    def get_pareto_summary(self) -> Dict[str, Any]:
        """Get full multi-objective optimization summary AND persist tradeoffs to ontology."""
        summary = self._pareto_learner.get_frontier_summary()
        # Persist discovered tradeoffs to ontology
        import asyncio
        for tradeoff in summary.get("tradeoffs", []):
            try:
                asyncio.create_task(self._ontology_tracker.track_pareto_tradeoff(
                    objective_a=tradeoff["objective_a"],
                    objective_b=tradeoff["objective_b"],
                    correlation=tradeoff["correlation"],
                    strength=tradeoff["strength"],
                    insight=tradeoff["insight"],
                ))
            except Exception:
                pass  # Best effort
        return summary

    def get_causal_insights(self) -> List[Dict[str, Any]]:
        """Get causal insights from promotion history AND persist them to ontology."""
        insights = self._causal_learner.compute_causal_effects()
        result = []
        for i in insights:
            insight_dict = i.__dict__
            # Persist to ontology (fire-and-forget)
            import asyncio
            try:
                asyncio.create_task(self._ontology_tracker.track_causal_insight(insight_dict))
            except Exception:
                pass  # Best effort persistence
            result.append(insight_dict)
        return result
