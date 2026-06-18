"""Meta-Evolution: The framework that evolves the evolution framework itself.

This implements Level 1 of self-evolution: hyperparameter self-tuning.
- Tracks outcome of every promotion/demotion
- Learns optimal thresholds per category
- Adapts to changing task distributions over time

Future levels:
- Level 2: Strategy pool self-evolution (generate/evolve strategies)
- Level 3: Evolution paradigm self-modification (invent new levels/algorithms)
"""

from __future__ import annotations

import time
import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
from collections import defaultdict

logger = logging.getLogger(__name__)


class EvolutionLevel(Enum):
    """Maturity level of the self-healing system."""
    REACTIVE = 1     # Run -> Fail -> Fix (basic RepairHook)
    PREDICTIVE = 2   # Pre-check -> Avoid failure (PredictiveGuard)
    EXPLORATION = 3  # Multi-strategy A/B -> Learn best (ExplorationEngine)
    AUTONOMOUS = 4   # All three combined, auto-tune parameters


@dataclass
class PromotionOutcome:
    """Record of a single promotion event and its outcome."""
    category: str
    from_level: str
    to_level: str
    timestamp: float
    # Before promotion
    success_rate_before: float
    executions_before: int
    # After promotion (observed over N executions)
    success_rate_after: Optional[float] = None
    executions_after: int = 0
    # Outcome label
    was_successful: Optional[bool] = None  # True = improved/stable, False = degraded


@dataclass
class TunedThresholds:
    """Learned thresholds for a (category, level) pair."""
    category: str
    level: str
    min_success_rate: float = 0.7
    promote_after: int = 10
    # Learning metadata
    total_promotions: int = 0
    successful_promotions: int = 0
    last_updated: float = 0.0


class MetaEvolutionLearner:
    """Learns to optimize the evolution framework itself.

    Usage:
        meta = MetaEvolutionLearner()
        meta.track_promotion(category, from_level, to_level, state_before)

        # Later, after observing enough post-promotion behavior:
        meta.observe_post_promotion_behavior(category, state)

        # Get tuned thresholds instead of hardcoded ones:
        thresholds = meta.get_thresholds(category, target_level)
    """

    # Default thresholds (fallback when no data)
    DEFAULT_THRESHOLDS = {
        EvolutionLevel.PREDICTIVE: {
            "min_success_rate": 0.7,
            "promote_after": 10,
        },
        EvolutionLevel.EXPLORATION: {
            "min_success_rate": 0.8,
            "promote_after": 15,
        },
        EvolutionLevel.AUTONOMOUS: {
            "min_success_rate": 0.85,
            "promote_after": 25,
        },
    }

    # How many post-promotion executions to observe before judging
    OBSERVATION_WINDOW = 15

    # Tuning parameters
    TUNING_FACTOR_SUCCESS = 0.95    # Make threshold easier if promotions work
    TUNING_FACTOR_FAILURE = 1.08    # Make threshold harder if promotions fail

    def __init__(self, persistence_path: Optional[str] = None):
        self._outcomes: List[PromotionOutcome] = []
        self._thresholds: Dict[Tuple[str, str], TunedThresholds] = {}
        self._pending_promotions: Dict[Tuple[str, str], PromotionOutcome] = {}
        self._persistence_path = persistence_path
        self._load()

    def track_promotion(self, category: str, from_level: EvolutionLevel,
                         to_level: EvolutionLevel, success_rate_before: float,
                         executions_before: int) -> None:
        """Track that a promotion just happened.

        Call this RIGHT AFTER a promotion occurs.
        """
        key = (category, to_level.name)
        if key in self._pending_promotions:
            # Already tracking this (state machine edge case), close old one
            old = self._pending_promotions.pop(key)
            old.was_successful = False  # Re-promotion = previous one didn't stick
            self._outcomes.append(old)

        outcome = PromotionOutcome(
            category=category,
            from_level=from_level.name,
            to_level=to_level.name,
            timestamp=time.time(),
            success_rate_before=success_rate_before,
            executions_before=executions_before,
        )
        self._pending_promotions[key] = outcome
        logger.info(
            f"MetaEvolution: Tracking promotion {category}: {from_level.name} → {to_level.name} "
            f"(success_before={success_rate_before:.2f})"
        )

    def track_demotion(self, category: str, from_level: EvolutionLevel,
                       to_level: EvolutionLevel, reason: str) -> None:
        """Track that a demotion happened.

        A demotion is STRONG evidence that the previous promotion was premature.
        """
        key = (category, from_level.name)
        if key in self._pending_promotions:
            # This demotion means the pending promotion failed badly
            outcome = self._pending_promotions.pop(key)
            outcome.was_successful = False
            self._outcomes.append(outcome)
            logger.info(
                f"MetaEvolution: Promotion {category}: {outcome.from_level} → {outcome.to_level} "
                f"FAILED (demoted, reason={reason})"
            )
            self._update_thresholds_from_outcome(outcome)

    def observe_execution(self, category: str, current_level: EvolutionLevel,
                          success: bool) -> None:
        """Observe a single execution outcome for pending promotions.

        Call this on EVERY execution (success or failure).
        """
        key = (category, current_level.name)
        if key not in self._pending_promotions:
            return

        outcome = self._pending_promotions[key]
        outcome.executions_after += 1

        # Update rolling success rate
        if outcome.success_rate_after is None:
            outcome.success_rate_after = 1.0 if success else 0.0
        else:
            n = outcome.executions_after
            outcome.success_rate_after = (
                outcome.success_rate_after * (n - 1) + (1.0 if success else 0.0)
            ) / n

        # Have we observed enough to judge?
        if outcome.executions_after >= self.OBSERVATION_WINDOW:
            self._finalize_promotion_outcome(outcome, key)

    def _finalize_promotion_outcome(self, outcome: PromotionOutcome,
                                    key: Tuple[str, str]) -> None:
        """Judge if a promotion was successful and update thresholds."""
        del self._pending_promotions[key]

        # Success = didn't get worse (allow 5% degradation tolerance)
        delta = outcome.success_rate_after - outcome.success_rate_before
        outcome.was_successful = delta >= -0.05  # Tolerate slight regression

        self._outcomes.append(outcome)

        logger.info(
            f"MetaEvolution: Promotion {outcome.category} outcome: "
            f"{'SUCCESS' if outcome.was_successful else 'FAILURE'} "
            f"(before={outcome.success_rate_before:.2f}, after={outcome.success_rate_after:.2f}, "
            f"delta={delta:+.2f})"
        )

        self._update_thresholds_from_outcome(outcome)
        self._save()

    def _update_thresholds_from_outcome(self, outcome: PromotionOutcome) -> None:
        """Tune thresholds based on promotion outcome."""
        thresh_key = (outcome.category, outcome.to_level)
        if thresh_key not in self._thresholds:
            default = self.DEFAULT_THRESHOLDS.get(EvolutionLevel[outcome.to_level], {})
            self._thresholds[thresh_key] = TunedThresholds(
                category=outcome.category,
                level=outcome.to_level,
                min_success_rate=default.get("min_success_rate", 0.7),
                promote_after=default.get("promote_after", 10),
            )

        thresh = self._thresholds[thresh_key]
        thresh.total_promotions += 1

        if outcome.was_successful:
            thresh.successful_promotions += 1
            # Promotion worked! Make threshold slightly easier
            thresh.min_success_rate *= self.TUNING_FACTOR_SUCCESS
            thresh.promote_after = max(5, int(thresh.promote_after * self.TUNING_FACTOR_SUCCESS))
        else:
            # Promotion failed! Make threshold harder
            thresh.min_success_rate = min(0.98, thresh.min_success_rate * self.TUNING_FACTOR_FAILURE)
            thresh.promote_after = min(100, int(thresh.promote_after * self.TUNING_FACTOR_FAILURE))

        thresh.last_updated = time.time()

    def get_thresholds(self, category: str, target_level: EvolutionLevel) -> Dict[str, Any]:
        """Get learned thresholds for promoting to target_level.

        Returns tuned thresholds, or defaults if no learning yet.
        """
        key = (category, target_level.name)
        if key in self._thresholds:
            thresh = self._thresholds[key]
            return {
                "min_success_rate": round(thresh.min_success_rate, 3),
                "promote_after": thresh.promote_after,
                "is_tuned": True,
                "total_promotions": thresh.total_promotions,
                "success_rate": round(thresh.successful_promotions / max(1, thresh.total_promotions), 3),
            }

        # Fallback to default
        default = self.DEFAULT_THRESHOLDS.get(target_level, {})
        return {
            "min_success_rate": default.get("min_success_rate", 0.7),
            "promote_after": default.get("promote_after", 10),
            "is_tuned": False,
        }

    def get_learning_report(self) -> Dict[str, Any]:
        """Generate a report of meta-evolution learning progress."""
        report = {
            "total_promotions_tracked": len(self._outcomes),
            "pending_promotions": len(self._pending_promotions),
            "successful_promotions": sum(1 for o in self._outcomes if o.was_successful),
            "failed_promotions": sum(1 for o in self._outcomes if o.was_successful is False),
            "tuned_thresholds_count": len(self._thresholds),
            "thresholds": {
                f"{cat}->{lvl}": {
                    "min_success_rate": round(t.min_success_rate, 3),
                    "promote_after": t.promote_after,
                    "promotion_success_rate": round(t.successful_promotions / max(1, t.total_promotions), 3),
                }
                for (cat, lvl), t in self._thresholds.items()
            },
        }
        if report["total_promotions_tracked"] > 0:
            report["overall_promotion_success_rate"] = round(
                report["successful_promotions"] / report["total_promotions_tracked"], 3
            )
        return report

    def _save(self) -> None:
        """Persist learned state to disk."""
        if not self._persistence_path:
            return
        try:
            data = {
                "thresholds": {
                    f"{k[0]}:{k[1]}": {
                        "min_success_rate": v.min_success_rate,
                        "promote_after": v.promote_after,
                        "total_promotions": v.total_promotions,
                        "successful_promotions": v.successful_promotions,
                    }
                    for k, v in self._thresholds.items()
                },
                "outcomes_count": len(self._outcomes),
            }
            with open(self._persistence_path, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.debug(f"MetaEvolution save failed: {e}")

    def _load(self) -> None:
        """Load learned state from disk."""
        if not self._persistence_path:
            return
        try:
            with open(self._persistence_path, 'r') as f:
                data = json.load(f)
            for key_str, thresh_data in data.get("thresholds", {}).items():
                cat, lvl = key_str.split(":", 1)
                self._thresholds[(cat, lvl)] = TunedThresholds(
                    category=cat,
                    level=lvl,
                    min_success_rate=thresh_data["min_success_rate"],
                    promote_after=thresh_data["promote_after"],
                    total_promotions=thresh_data.get("total_promotions", 0),
                    successful_promotions=thresh_data.get("successful_promotions", 0),
                )
            logger.info(
                f"MetaEvolution: Loaded {len(self._thresholds)} tuned thresholds "
                f"from {self._persistence_path}"
            )
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.debug(f"MetaEvolution load failed: {e}")
