"""Causal Evolution: Moving from correlation to causal inference.

Problem with correlation-based evolution:
   "After promotion, success rate dropped" → NOT PROOF that promotion caused it
   Could be external factors (task got harder, API down, etc.)

Solution: Causal inference methods
1. Propensity Score Matching (PSM): For each promotion, find a "synthetic control"
   that didn't promote but had similar pre-treatment characteristics
2. Randomized Trials: 10% chance to randomly promote/delay for true A/B testing
3. Difference-in-Differences: Compare treatment vs control change over time

Key output: ATT (Average Treatment Effect on the Treated)
   ATT > 0: Promotion actually helped
   ATT < 0: Promotion was harmful
   ATT ≈ 0: Promotion had no measurable effect
"""

from __future__ import annotations

import time
import math
import random
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from collections import defaultdict

logger = logging.getLogger(__name__)


@dataclass
class PromotionObservation:
    """A single promotion event with pre and post metrics."""
    category: str
    timestamp: float
    from_level: str
    to_level: str
    # Pre-treatment (before promotion)
    pre_success_rate: float
    pre_executions: int
    pre_avg_latency: float
    # Post-treatment (after promotion, observed over window)
    post_success_rate: Optional[float] = None
    post_avg_latency: Optional[float] = None
    post_executions: int = 0
    # Causal attribution
    was_randomized: bool = False  # Was this a random trial?
    synthetic_control_match: Optional[str] = None  # Which category was the control
    att: Optional[float] = None  # Average Treatment Effect


@dataclass
class CausalInsight:
    """A causal conclusion from evolutionary data."""
    category: str
    from_level: str
    to_level: str
    att: float  # Positive = promotion helped, Negative = promotion hurt
    confidence: float  # How confident we are (statistical significance)
    sample_size: int
    recommendation: str  # "raise_thresholds", "lower_thresholds", "no_change"
    explanation: str


class CausalEvolutionLearner:
    """Learns the CAUSAL effect of promotion decisions, not just correlations.

    Usage:
        causal = CausalEvolutionLearner()
        causal.track_promotion(category, from_level, to_level, pre_metrics)
        # ... after observation window ...
        causal.observe_post_promotion(category, post_metrics)
        insights = causal.compute_causal_effects()
    """

    # Observation window for post-promotion effect
    OBSERVATION_WINDOW = 20

    # Percentage of promotions to randomize (for true A/B testing)
    RANDOMIZATION_RATE = 0.10

    # Matching parameters for PSM
    MATCH_CALIPER = 0.1  # Max distance for a valid match

    def __init__(self):
        self._observations: Dict[Tuple[str, str, float], PromotionObservation] = {}
        self._category_history: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        self._insights: List[CausalInsight] = []

    def should_randomize_promotion(self, category: str, to_level: str) -> bool:
        """Decide if this promotion should be randomized for causal testing.

        Randomization is the GOLD STANDARD of causal inference.
        We sacrifice short-term optimality for long-term knowledge.
        """
        # Don't randomize if we have too few observations
        history = self._get_history(category, to_level)
        if len(history) < 5:
            return False

        # Flip biased coin
        return random.random() < self.RANDOMIZATION_RATE

    def track_promotion(
        self,
        category: str,
        from_level: str,
        to_level: str,
        pre_success_rate: float,
        pre_executions: int,
        pre_avg_latency: float = 1.0,
        was_randomized: bool = False,
    ) -> None:
        """Track that a promotion just happened, with pre-treatment covariates."""
        key = (category, to_level, time.time())
        obs = PromotionObservation(
            category=category,
            timestamp=time.time(),
            from_level=from_level,
            to_level=to_level,
            pre_success_rate=pre_success_rate,
            pre_executions=pre_executions,
            pre_avg_latency=pre_avg_latency,
            was_randomized=was_randomized,
        )
        self._observations[key] = obs

        # Also track as historical data point for future matching
        self._category_history[category].append({
            "timestamp": time.time(),
            "level": to_level,
            "success_rate": pre_success_rate,
            "executions": pre_executions,
            "was_promotion": True,
        })

    def observe_post_promotion(
        self,
        category: str,
        to_level: str,
        success_rate: float,
        avg_latency: float,
    ) -> None:
        """Observe post-promotion metrics for the most recent promotion."""
        # Find the matching observation
        matches = [
            (key, obs) for key, obs in self._observations.items()
            if obs.category == category and obs.to_level == to_level
            and obs.post_success_rate is None
        ]
        if not matches:
            return

        key, obs = matches[-1]  # Most recent
        obs.post_success_rate = success_rate
        obs.post_avg_latency = avg_latency
        obs.post_executions = self.OBSERVATION_WINDOW

        # 1. Naive difference (correlation, not causation)
        naive_diff = success_rate - obs.pre_success_rate

        # 2. Causal estimate: find synthetic control via Propensity Score Matching
        control = self._find_synthetic_control(obs)
        if control:
            obs.synthetic_control_match = control["category"]
            # ATT = (Treated_post - Treated_pre) - (Control_post - Control_pre)
            # This is Difference-in-Differences
            treated_change = (obs.post_success_rate or 0) - obs.pre_success_rate
            control_change = control["post_success"] - control["pre_success"]
            obs.att = treated_change - control_change

            logger.info(
                f"Causal: {category} {obs.from_level}→{to_level}: "
                f"naive_diff={naive_diff:+.2f}, ATT_causal={obs.att:+.2f} "
                f"(matched to {control['category']})"
            )
        else:
            # No good match, fall back to naive difference
            obs.att = naive_diff
            logger.debug(
                f"Causal: No synthetic control found for {category} {to_level}, "
                f"using naive difference {naive_diff:+.2f}"
            )

    def compute_causal_effects(self) -> List[CausalInsight]:
        """Compute all causal insights from collected observations."""
        insights = []

        # Group by (from_level, to_level) transition
        by_transition = defaultdict(list)
        for obs in self._observations.values():
            if obs.att is not None:
                by_transition[(obs.from_level, obs.to_level)].append(obs)

        for (from_level, to_level), observations in by_transition.items():
            if len(observations) < 3:
                continue  # Need at least 3 samples for reasonable stats

            atts = [o.att for o in observations if o.att is not None]
            mean_att = sum(atts) / len(atts)
            n = len(atts)

            # Compute confidence via bootstrap-like heuristic
            # Higher n + less variance = higher confidence
            variance = sum((a - mean_att) ** 2 for a in atts) / max(1, n - 1)
            std_error = math.sqrt(variance / max(1, n))
            confidence = max(0.0, min(0.99, 1.0 - std_error * 2))

            # Generate recommendation
            if mean_att < -0.05 and confidence > 0.7:
                rec = "raise_thresholds"
                explanation = (
                    f"Promotions {from_level}→{to_level} cause average success rate drop "
                    f"of {abs(mean_att)*100:.1f}% (ATT={mean_att:+.3f}, n={n}). "
                    f"Raise promotion thresholds by {abs(mean_att)*200:.0f}%."
                )
            elif mean_att > 0.05 and confidence > 0.7:
                rec = "lower_thresholds"
                explanation = (
                    f"Promotions {from_level}→{to_level} cause average success rate gain "
                    f"of {mean_att*100:.1f}% (ATT={mean_att:+.3f}, n={n}). "
                    f"Promotion thresholds can be lowered by {mean_att*100:.0f}%."
                )
            else:
                rec = "no_change"
                explanation = (
                    f"No measurable causal effect for {from_level}→{to_level} "
                    f"(ATT={mean_att:+.3f}, n={n}, confidence={confidence:.2f}). "
                    f"Keep current thresholds."
                )

            # Also group by category for per-category insights
            by_category = defaultdict(list)
            for obs in observations:
                if obs.att is not None:
                    by_category[obs.category].append(obs.att)

            for cat, cat_atts in by_category.items():
                if len(cat_atts) >= 2:
                    cat_mean = sum(cat_atts) / len(cat_atts)
                    insights.append(CausalInsight(
                        category=cat,
                        from_level=from_level,
                        to_level=to_level,
                        att=cat_mean,
                        confidence=confidence,
                        sample_size=len(cat_atts),
                        recommendation=rec,
                        explanation=explanation,
                    ))

        self._insights = insights
        return insights

    def get_recommendation_for_transition(
        self, category: str, from_level: str, to_level: str
    ) -> Optional[CausalInsight]:
        """Get causal recommendation for a specific transition and category."""
        for insight in self._insights:
            if (insight.category == category and
                insight.from_level == from_level and
                insight.to_level == to_level):
                return insight
        return None

    def get_threshold_adjustment(
        self, category: str, from_level: str, to_level: str
    ) -> float:
        """Get multiplier to adjust promotion thresholds based on causal evidence.

        Returns:
            multiplier < 1.0 = make promotion easier
            multiplier = 1.0 = no change
            multiplier > 1.0 = make promotion harder
        """
        insight = self.get_recommendation_for_transition(category, from_level, to_level)
        if not insight:
            return 1.0  # No causal data → no change

        if insight.recommendation == "raise_thresholds":
            # Scale by effect size, capped at 50% increase
            return min(1.5, 1.0 + abs(insight.att) * 2)
        elif insight.recommendation == "lower_thresholds":
            # Scale by effect size, capped at 30% decrease
            return max(0.7, 1.0 - abs(insight.att) * 1.5)
        else:
            return 1.0

    # ── Internal: Propensity Score Matching ─────────────────────────────────

    def _find_synthetic_control(self, promotion: PromotionObservation) -> Optional[Dict[str, Any]]:
        """Find a synthetic control for a promotion using propensity score matching.

        The control is a category-time point that was SIMILAR to the treatment
        at time of promotion, but DID NOT GET PROMOTED.

        This is how we answer the counterfactual:
            "What would have happened if we didn't promote?"
        """
        candidates = []

        # Search across all category histories
        for cat, history in self._category_history.items():
            if cat == promotion.category:
                continue  # Don't match to self

            # Find points in this category's history with similar pre-metrics
            for point in history:
                if point.get("was_promotion", False):
                    continue  # Only match to points that DIDN'T promote

                # Compute propensity score distance
                distance = self._propensity_distance(promotion, point)
                if distance < self.MATCH_CALIPER:
                    # This is a candidate control!
                    # Get the success rate at this point and N steps after
                    idx = history.index(point)
                    if idx + self.OBSERVATION_WINDOW < len(history):
                        pre_success = point["success_rate"]
                        post_success = history[idx + self.OBSERVATION_WINDOW]["success_rate"]
                        candidates.append({
                            "category": cat,
                            "distance": distance,
                            "pre_success": pre_success,
                            "post_success": post_success,
                            "change": post_success - pre_success,
                        })

        # Return the best match (smallest distance)
        if candidates:
            candidates.sort(key=lambda c: c["distance"])
            return candidates[0]
        return None

    def _propensity_distance(self, promo: PromotionObservation, point: Dict) -> float:
        """Compute normalized distance between promotion and candidate control point.

        This is the "propensity score" in PSM literature.
        """
        dist_success = abs(promo.pre_success_rate - point.get("success_rate", 0))
        dist_executions = abs(promo.pre_executions - point.get("executions", 0)) / 100.0

        # Euclidean distance in normalized covariate space
        return math.sqrt(dist_success ** 2 + dist_executions ** 2)

    def _get_history(self, category: str, level: str) -> List[Dict]:
        """Get historical data points for a category at a specific level."""
        return [
            h for h in self._category_history.get(category, [])
            if h.get("level") == level
        ]
