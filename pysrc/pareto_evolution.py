"""Multi-Objective Pareto Evolution: Optimize multiple conflicting goals at once.

Single-objective problem (just success_rate) creates perverse incentives:
   - 99% success rate but 10x latency increase = "great job" 🤔
   - Cost explodes but nobody notices = "evolution is working!"

Pareto solution: Find the optimal frontier where NO objective can be improved
without worsening at least one other objective.

The four core objectives (0.0 = worst, 1.0 = best):
   1. success_rate  - Task completion success
   2. efficiency    - Latency / speed (inverted)
   3. cost_effective - Token / resource usage (inverted)
   4. stability     - Low variance, few demotions

Every evolution decision is now evaluated against ALL FOUR objectives.
"""

from __future__ import annotations

import math
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from enum import Enum


logger = logging.getLogger(__name__)


class Objective(Enum):
    """The four dimensions of evolutionary performance."""
    SUCCESS = "success_rate"
    EFFICIENCY = "efficiency"
    COST_EFFECTIVE = "cost_effective"
    STABILITY = "stability"


@dataclass
class MultiObjectiveMetrics:
    """A point in the 4-dimensional objective space."""
    success_rate: float = 0.0
    efficiency: float = 0.0  # 1.0 = fastest
    cost_effective: float = 0.0  # 1.0 = lowest cost
    stability: float = 0.0  # 1.0 = most stable

    @property
    def vector(self) -> Tuple[float, float, float, float]:
        return (self.success_rate, self.efficiency, self.cost_effective, self.stability)

    def dominates(self, other: 'MultiObjectiveMetrics') -> bool:
        """Does this point DOMINATE another point?

        A dominates B iff:
           A is >= B on ALL objectives, AND
           A is > B on AT LEAST one objective
        """
        self_vec = self.vector
        other_vec = other.vector

        all_ge = all(s >= o for s, o in zip(self_vec, other_vec))
        any_gt = any(s > o for s, o in zip(self_vec, other_vec))

        return all_ge and any_gt

    def euclidean_distance(self, other: 'MultiObjectiveMetrics') -> float:
        """Distance between two points in objective space."""
        return math.sqrt(
            sum((s - o) ** 2 for s, o in zip(self.vector, other.vector))
        )


@dataclass
class WeightedPreferences:
    """User preferences for each objective (weights sum to 1.0)."""
    success_weight: float = 0.4
    efficiency_weight: float = 0.25
    cost_weight: float = 0.2
    stability_weight: float = 0.15

    @property
    def weights(self) -> Tuple[float, float, float, float]:
        return (self.success_weight, self.efficiency_weight, self.cost_weight, self.stability_weight)

    def weighted_score(self, metrics: MultiObjectiveMetrics) -> float:
        """Compute scalar weighted sum score for a point."""
        return sum(w * v for w, v in zip(self.weights, metrics.vector))

    def normalize(self) -> None:
        """Normalize weights to sum to 1.0."""
        total = sum(self.weights)
        if total > 0:
            self.success_weight /= total
            self.efficiency_weight /= total
            self.cost_weight /= total
            self.stability_weight /= total


@dataclass
class EvolutionTradeoff:
    """A tradeoff discovered between two objectives."""
    objective_a: str
    objective_b: str
    correlation: float  # -1.0 (strong inverse) to +1.0 (strong positive)
    strength: str  # "strong", "moderate", "weak"
    insight: str


class ParetoEvolutionLearner:
    """Multi-objective evolutionary optimizer using Pareto efficiency.

    Usage:
        pareto = ParetoEvolutionLearner()
        pareto.add_metrics("research:EXPLORATION", MultiObjectiveMetrics(
            success_rate=0.85, efficiency=0.7, cost_effective=0.6, stability=0.8
        ))

        # Get the Pareto frontier
        frontier = pareto.get_pareto_frontier()

        # Pick optimal point based on user preferences
        best = pareto.get_optimal_for_preferences(WeightedPreferences(
            success_weight=0.5, efficiency_weight=0.3, cost_weight=0.1, stability_weight=0.1
        ))
    """

    def __init__(self):
        self._points: Dict[str, MultiObjectiveMetrics] = {}
        self._preferences = WeightedPreferences()  # Default weights
        self._frontier: List[str] = []  # Cache of current Pareto frontier

    def add_metrics(self, point_id: str, metrics: MultiObjectiveMetrics) -> None:
        """Add or update a point in objective space."""
        self._points[point_id] = metrics
        self._frontier = []  # Invalidate cache

    def get_pareto_frontier(self) -> List[Tuple[str, MultiObjectiveMetrics]]:
        """Compute the current Pareto frontier.

        The frontier contains all points where NO objective can be improved
        without worsening at least one other objective.
        """
        if self._frontier:
            return [(pid, self._points[pid]) for pid in self._frontier]

        all_ids = list(self._points.keys())
        if not all_ids:
            return []

        frontier_ids = []

        for i, candidate_id in enumerate(all_ids):
            candidate = self._points[candidate_id]
            dominated = False

            # Check if any other point dominates this candidate
            for j, other_id in enumerate(all_ids):
                if i == j:
                    continue
                other = self._points[other_id]
                if other.dominates(candidate):
                    dominated = True
                    break

            if not dominated:
                frontier_ids.append(candidate_id)

        self._frontier = frontier_ids
        return [(pid, self._points[pid]) for pid in frontier_ids]

    def get_optimal_for_preferences(
        self, preferences: Optional[WeightedPreferences] = None
    ) -> Optional[Tuple[str, MultiObjectiveMetrics]]:
        """Pick the point on the Pareto frontier that best matches user preferences.

        Uses the weighted Tchebycheff scalarization method:
           min (max (w_i * (z_i^* - f_i(x))))
        Where z_i^* is the ideal point (best possible on each objective)
        """
        frontier = self.get_pareto_frontier()
        if not frontier:
            return None

        prefs = preferences or self._preferences

        # Find the ideal point (best possible value for each objective)
        ideal = MultiObjectiveMetrics(
            success_rate=max(m.success_rate for _, m in frontier),
            efficiency=max(m.efficiency for _, m in frontier),
            cost_effective=max(m.cost_effective for _, m in frontier),
            stability=max(m.stability for _, m in frontier),
        )

        # Find frontier point with minimum Tchebycheff distance to ideal
        best_id = None
        best_metrics = None
        min_distance = float('inf')

        for point_id, metrics in frontier:
            # Tchebycheff distance = max weighted gap from ideal
            distances = [
                prefs.success_weight * (ideal.success_rate - metrics.success_rate),
                prefs.efficiency_weight * (ideal.efficiency - metrics.efficiency),
                prefs.cost_weight * (ideal.cost_effective - metrics.cost_effective),
                prefs.stability_weight * (ideal.stability - metrics.stability),
            ]
            tchebycheff = max(distances)

            # Minimize maximum regret
            if tchebycheff < min_distance:
                min_distance = tchebycheff
                best_id = point_id
                best_metrics = metrics

        return (best_id, best_metrics) if best_id else None

    def should_promote_pareto(
        self,
        category: str,
        from_level: str,
        to_level: str,
        current_metrics: MultiObjectiveMetrics,
        projected_metrics: Optional[MultiObjectiveMetrics] = None,
        preferences: Optional[WeightedPreferences] = None,
    ) -> Tuple[bool, str]:
        """Multi-objective promotion decision.

        Returns: (should_promote, explanation)

        Promotion is allowed iff:
        1. Projected metrics are NOT WORSE on ALL objectives
        2. At least one objective IMPROVES
        3. The resulting point would NOT be dominated by existing points
        """
        prefs = preferences or self._preferences

        # Use current metrics if no projection (conservative)
        projected = projected_metrics or current_metrics

        # Check 1: No regression in the weighted score
        current_score = prefs.weighted_score(current_metrics)
        projected_score = prefs.weighted_score(projected)
        score_change = projected_score - current_score

        # Check 2: Not strictly dominated by any existing point
        dominated_by = []
        for _, other in self.get_pareto_frontier():
            if other.dominates(projected):
                dominated_by.append(other)

        # Check 3: Would this point expand the frontier?
        # (i.e., it dominates at least one existing frontier point)
        dominates_some = False
        for _, other in self.get_pareto_frontier():
            if projected.dominates(other):
                dominates_some = True
                break

        # Decision
        if dominated_by:
            return (
                False,
                f"Projected state would be dominated by {len(dominated_by)} existing points. "
                f"Weighted score change: {score_change:+.3f}"
            )

        if score_change < -0.05:  # Allow small tolerance (-5%)
            return (
                False,
                f"Projected weighted score drops by {abs(score_change)*100:.1f}%. "
                f"Current: {current_score:.3f}, Projected: {projected_score:.3f}"
            )

        if dominates_some or score_change > 0.02:
            return (
                True,
                f"Promotion would improve weighted score by {score_change*100:.1f}% "
                f"and {'expand' if dominates_some else 'add to'} the Pareto frontier."
            )

        # Neutral case: no harm, but no clear gain either
        return (
            True,
            f"Neutral projection (score change: {score_change*100:+.1f}%). "
            f"Promotion allowed but not strongly recommended."
        )

    def get_tradeoff_analysis(self) -> List[EvolutionTradeoff]:
        """Discover and quantify tradeoffs between objectives.

        This answers questions like:
            - "Is there a speed-accuracy tradeoff?"
            - "Does higher cost correlate with higher success?"
            - "Is stability inversely related to exploration?"
        """
        if len(self._points) < 5:
            return []  # Need enough data for meaningful correlation

        tradeoffs = []

        # Extract all values for each objective
        all_success = [m.success_rate for m in self._points.values()]
        all_efficiency = [m.efficiency for m in self._points.values()]
        all_cost = [m.cost_effective for m in self._points.values()]
        all_stability = [m.stability for m in self._points.values()]

        # Check all 6 pairwise combinations
        pairs = [
            (Objective.SUCCESS, Objective.EFFICIENCY, all_success, all_efficiency),
            (Objective.SUCCESS, Objective.COST_EFFECTIVE, all_success, all_cost),
            (Objective.SUCCESS, Objective.STABILITY, all_success, all_stability),
            (Objective.EFFICIENCY, Objective.COST_EFFECTIVE, all_efficiency, all_cost),
            (Objective.EFFICIENCY, Objective.STABILITY, all_efficiency, all_stability),
            (Objective.COST_EFFECTIVE, Objective.STABILITY, all_cost, all_stability),
        ]

        for obj_a, obj_b, values_a, values_b in pairs:
            corr = self._pearson_correlation(values_a, values_b)
            abs_corr = abs(corr)

            if abs_corr < 0.2:
                strength = "weak"
            elif abs_corr < 0.5:
                strength = "moderate"
            else:
                strength = "strong"

            if corr > 0.3:
                insight = f"{obj_a.value} and {obj_b.value} are POSITIVELY correlated ({corr:+.2f}) — they improve together"
            elif corr < -0.3:
                insight = f"{obj_a.value} and {obj_b.value} are INVERSELY correlated ({corr:+.2f}) — classic tradeoff"
            else:
                insight = f"No strong correlation between {obj_a.value} and {obj_b.value} ({corr:+.2f}) — can optimize independently"

            tradeoffs.append(EvolutionTradeoff(
                objective_a=obj_a.value,
                objective_b=obj_b.value,
                correlation=corr,
                strength=strength,
                insight=insight,
            ))

        return sorted(tradeoffs, key=lambda t: abs(t.correlation), reverse=True)

    def get_frontier_summary(self) -> Dict[str, Any]:
        """Get a human-readable summary of the Pareto frontier."""
        frontier = self.get_pareto_frontier()

        if not frontier:
            return {"frontier_size": 0, "total_points": len(self._points)}

        # Best on each dimension
        best_success = max(frontier, key=lambda x: x[1].success_rate)
        best_efficiency = max(frontier, key=lambda x: x[1].efficiency)
        best_cost = max(frontier, key=lambda x: x[1].cost_effective)
        best_stability = max(frontier, key=lambda x: x[1].stability)

        return {
            "frontier_size": len(frontier),
            "total_points": len(self._points),
            "frontier_coverage": len(frontier) / len(self._points),
            "best_on": {
                Objective.SUCCESS.value: best_success[0],
                Objective.EFFICIENCY.value: best_efficiency[0],
                Objective.COST_EFFECTIVE.value: best_cost[0],
                Objective.STABILITY.value: best_stability[0],
            },
            "tradeoffs": [t.__dict__ for t in self.get_tradeoff_analysis()],
        }

    @staticmethod
    def _pearson_correlation(x: List[float], y: List[float]) -> float:
        """Compute Pearson correlation coefficient between two lists."""
        n = len(x)
        if n < 2:
            return 0.0

        mean_x = sum(x) / n
        mean_y = sum(y) / n

        numerator = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
        denom_x = math.sqrt(sum((xi - mean_x) ** 2 for xi in x))
        denom_y = math.sqrt(sum((yi - mean_y) ** 2 for yi in y))

        if denom_x == 0 or denom_y == 0:
            return 0.0

        return numerator / (denom_x * denom_y)


def normalize_to_01(value: float, min_val: float, max_val: float) -> float:
    """Normalize a value to [0, 1] range with clipping."""
    if max_val == min_val:
        return 0.5
    normalized = (value - min_val) / (max_val - min_val)
    return max(0.0, min(1.0, normalized))


def normalize_latency(latency_ms: float, best: float = 100, worst: float = 5000) -> float:
    """Normalize latency (lower is better) to [0, 1] where 1 = best."""
    # Invert so lower latency = higher score
    return 1.0 - normalize_to_01(latency_ms, best, worst)


def normalize_cost(token_count: float, best: float = 100, worst: float = 10000) -> float:
    """Normalize cost (lower is better) to [0, 1] where 1 = best."""
    return 1.0 - normalize_to_01(token_count, best, worst)


def normalize_stability(consecutive_successes: int, best: int = 50, worst: int = 0) -> float:
    """Normalize stability to [0, 1]."""
    return normalize_to_01(consecutive_successes, worst, best)
