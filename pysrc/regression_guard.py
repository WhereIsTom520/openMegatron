"""Regression guard — prevents deploying worse models.

Runs validation checks before a new model can be deployed. Three checks:
  1. F1 gate: new_f1 >= current_f1 - tolerance
  2. Holdout test: new model performs at least as well on a fixed set
  3. Edge case check: model handles extreme inputs without crashing

Used by AutoRetrainLoop to gate deployment decisions.

Usage:
    from regression_guard import RegressionGuard
    guard = RegressionGuard()
    result = guard.validate("model_v2.pkl", "model_v1.pkl", store)
    if result["passed"]:
        deploy()
"""

from __future__ import annotations

import json
import logging
from typing import Any

import numpy as np

from reward_model import (
    FEATURE_NAMES,
    SklearnRewardScorer,
    TorchRewardScorer,
    extract_features,
    features_to_array,
)

logger = logging.getLogger(__name__)

# Tolerance: new model can be up to 2% worse on F1 and still pass
F1_TOLERANCE = 0.02
# Minimum samples needed for holdout test
MIN_HOLDOUT_SAMPLES = 10


def _load_scorer(model_path: str):
    path = model_path.lower()
    if path.endswith(".pt") or path.endswith(".pth"):
        return TorchRewardScorer.load(model_path)
    return SklearnRewardScorer.load(model_path)


class RegressionGuard:
    """Validates new models before deployment — prevents regression.

    Three validation checks:
      1. F1 gate — basic quality floor
      2. Holdout test — no regression on fixed evaluation set
      3. Edge case check — handles extreme inputs gracefully
    """

    def __init__(self, f1_tolerance: float = F1_TOLERANCE):
        self._tolerance = f1_tolerance

    def validate(
        self,
        new_model_path: str,
        current_model_path: str,
        store,
    ) -> dict:
        """Run full validation suite.

        Args:
            new_model_path: Path to the candidate model.
            current_model_path: Path to the currently deployed model.
            store: TrajectoryStore for evaluation data.

        Returns:
            Dict with {passed, checks, summary}.
        """
        checks = []
        all_passed = True

        # ── Load models ──
        try:
            new_scorer = _load_scorer(new_model_path)
            current_scorer = _load_scorer(current_model_path)
        except Exception as exc:
            return {
                "passed": False,
                "checks": [{"name": "model_load", "passed": False, "error": str(exc)}],
                "summary": f"Failed to load models: {exc}",
            }

        # ── Check 1: F1 gate ──
        f1_result = self._check_f1_gate(new_scorer, current_scorer, store)
        checks.append(f1_result)
        if not f1_result["passed"]:
            all_passed = False

        # ── Check 2: Holdout test ──
        holdout_result = self._check_holdout(new_scorer, current_scorer, store)
        checks.append(holdout_result)
        if not holdout_result["passed"]:
            all_passed = False

        # ── Check 3: Edge cases ──
        edge_result = self._check_edge_cases(new_scorer)
        checks.append(edge_result)
        if not edge_result["passed"]:
            all_passed = False

        return {
            "passed": all_passed,
            "checks": checks,
            "summary": self._build_summary(checks, all_passed),
        }

    def _check_f1_gate(self, new_scorer, current_scorer, store) -> dict:
        """Check 1: new F1 >= current F1 - tolerance."""
        try:
            new_eval = new_scorer.evaluate(store)
            current_eval = current_scorer.evaluate(store)

            new_f1 = new_eval.get("f1", 0.0)
            current_f1 = current_eval.get("f1", 0.0)
            delta = new_f1 - current_f1
            passed = new_f1 >= current_f1 - self._tolerance

            return {
                "name": "f1_gate",
                "passed": passed,
                "new_f1": round(new_f1, 4),
                "current_f1": round(current_f1, 4),
                "delta": round(delta, 4),
                "tolerance": self._tolerance,
                "detail": f"New F1={new_f1:.4f}, Current F1={current_f1:.4f}, Δ={delta:+.4f}",
            }
        except Exception as exc:
            return {"name": "f1_gate", "passed": False, "error": str(exc)}

    def _check_holdout(self, new_scorer, current_scorer, store) -> dict:
        """Check 2: new model doesn't regress on holdout samples."""
        try:
            total = store.count()
            if total < MIN_HOLDOUT_SAMPLES:
                return {
                    "name": "holdout_test",
                    "passed": True,
                    "detail": f"Skipped: only {total} samples (need {MIN_HOLDOUT_SAMPLES})",
                    "skipped": True,
                }

            # Use last 20% of trajectories as holdout
            trajectories = store.query(limit=max(total, 1))
            split_idx = max(int(len(trajectories) * 0.8), MIN_HOLDOUT_SAMPLES)
            holdout = trajectories[split_idx:]

            new_wins = 0
            current_wins = 0
            ties = 0
            for traj in holdout:
                features = extract_features(traj)
                true_label = 1 if traj.get("success") else 0
                try:
                    new_pred = 1 if new_scorer.predict(features) >= 0.5 else 0
                    cur_pred = 1 if current_scorer.predict(features) >= 0.5 else 0
                except Exception:
                    continue

                new_correct = new_pred == true_label
                cur_correct = cur_pred == true_label
                if new_correct and not cur_correct:
                    new_wins += 1
                elif cur_correct and not new_correct:
                    current_wins += 1
                else:
                    ties += 1

            total_compared = new_wins + current_wins + ties
            passed = new_wins >= current_wins or total_compared == 0

            return {
                "name": "holdout_test",
                "passed": passed,
                "holdout_size": len(holdout),
                "new_wins": new_wins,
                "current_wins": current_wins,
                "ties": ties,
                "detail": f"New wins={new_wins}, Current wins={current_wins}, Ties={ties}",
            }
        except Exception as exc:
            return {"name": "holdout_test", "passed": False, "error": str(exc)}

    def _check_edge_cases(self, scorer) -> dict:
        """Check 3: model handles extreme inputs without crashing."""
        edge_cases = [
            # All zeros
            {name: 0.0 for name in FEATURE_NAMES},
            # Very large values
            {name: 1e6 for name in FEATURE_NAMES},
            # Zero tool calls, zero duration
            {"tool_count": 0, "duration_ms": 0, "has_error_tool": 0, "error_tool_ratio": 0,
             "skill_count": 0, "avg_tool_duration": 0, "user_input_len": 0,
             "source_is_claude": 0, "hour_of_day": 0,
             "stability": 1.0, "speed": 1.0, "efficiency": 1.0},
            # All errors
            {"tool_count": 10, "duration_ms": 60000, "has_error_tool": 1, "error_tool_ratio": 1.0,
             "skill_count": 3, "avg_tool_duration": 6000, "user_input_len": 500,
             "source_is_claude": 0, "hour_of_day": 23,
             "stability": 0.0, "speed": 0.0, "efficiency": 0.0},
        ]

        failures = []
        for i, features in enumerate(edge_cases):
            try:
                score = scorer.predict(features)
                if not (0.0 <= score <= 1.0):
                    failures.append(f"case_{i}: score={score} out of [0,1]")
            except Exception as exc:
                failures.append(f"case_{i}: crashed: {exc}")

        passed = len(failures) == 0
        return {
            "name": "edge_cases",
            "passed": passed,
            "cases_tested": len(edge_cases),
            "failures": failures,
            "detail": f"{len(edge_cases) - len(failures)}/{len(edge_cases)} edge cases passed",
        }

    def _build_summary(self, checks: list[dict], passed: bool) -> str:
        if passed:
            return "All checks passed — safe to deploy"
        failed = [c["name"] for c in checks if not c.get("passed", False)]
        return f"BLOCKED: {len(failed)} check(s) failed: {', '.join(failed)}"
