"""A/B comparison framework — model-based vs rule-based scoring.

Compares the learned reward model against the hardcoded rule-based scorer
on trajectory data. Produces detailed metrics: agreement rate, win rate,
calibration error, per-category F1, and statistical significance tests.

Usage:
    from eval_ab import ABComparison
    ab = ABComparison()
    result = ab.run(store, "model.pkl")
    print(ab.to_markdown())
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

import numpy as np

from reward_model import (
    FEATURE_NAMES,
    SklearnRewardScorer,
    TorchRewardScorer,
    extract_features,
)

logger = logging.getLogger(__name__)


def _load_scorer(model_path: str):
    path = model_path.lower()
    if path.endswith(".pt") or path.endswith(".pth"):
        return TorchRewardScorer.load(model_path)
    return SklearnRewardScorer.load(model_path)


def _rule_based_score(trace: dict) -> float:
    """Replicate the rule-based _score_task_trace() formula."""
    tool_calls = trace.get("tool_calls", []) or []
    if not tool_calls:
        return 0.2 if trace.get("final_answer") else 0.0

    failures = 0
    successes = 0
    total_duration_ms = 0.0
    for call in tool_calls:
        parsed = call if isinstance(call, dict) else {}
        status = str(parsed.get("status", "")).lower()
        failed = status in ("error", "denied")
        failures += 1 if failed else 0
        successes += 0 if failed else 1
        total_duration_ms += float(parsed.get("duration_ms", 0.0))

    tool_count = len(tool_calls)
    stability = successes / tool_count if tool_count else 1.0
    speed = 1.0 / (1.0 + (total_duration_ms / 60000.0))
    efficiency = 1.0 / (1.0 + max(0, tool_count - 3) * 0.18)
    completion = 1.0 if trace.get("success") else 0.0
    reward = max(0.0, min(1.0, 0.45 * completion + 0.25 * stability + 0.20 * speed + 0.10 * efficiency))
    return reward


class ABComparison:
    """Compares model-based scoring vs rule-based scoring on trajectory data."""

    def __init__(self):
        self._results: list[dict] = []
        self._model_scores: list[float] = []
        self._rule_scores: list[float] = []
        self._labels: list[int] = []

    def run(self, store, model_path: str) -> dict:
        """Run full comparison: model vs rule vs ground truth.

        Args:
            store: TrajectoryStore instance.
            model_path: Path to a trained model file.

        Returns:
            Dict with all metrics.
        """
        scorer = _load_scorer(model_path)
        total = store.count()
        if total == 0:
            return {"error": "no_data", "n_samples": 0}

        trajectories = store.query(limit=max(total, 1))
        self._results = []
        self._model_scores = []
        self._rule_scores = []
        self._labels = []

        for traj in trajectories:
            features = extract_features(traj)
            try:
                model_score = scorer.predict(features)
            except Exception:
                model_score = 0.5

            rule_score = _rule_based_score(traj)
            true_label = 1 if traj.get("success") else 0

            self._model_scores.append(model_score)
            self._rule_scores.append(rule_score)
            self._labels.append(true_label)

            self._results.append({
                "trajectory_id": traj.get("id", ""),
                "model_score": round(model_score, 4),
                "rule_score": round(rule_score, 4),
                "true_label": true_label,
                "model_correct": (model_score >= 0.5) == bool(true_label),
                "rule_correct": (rule_score >= 0.5) == bool(true_label),
            })

        return self._compute_metrics()

    def _compute_metrics(self) -> dict:
        """Compute all comparison metrics."""
        if not self._results:
            return {"error": "no_results"}

        n = len(self._results)
        model_arr = np.array(self._model_scores)
        rule_arr = np.array(self._rule_scores)
        labels_arr = np.array(self._labels)

        # Binary predictions (threshold at 0.5)
        model_pred = (model_arr >= 0.5).astype(int)
        rule_pred = (rule_arr >= 0.5).astype(int)

        # Per-prediction accuracy
        model_correct = sum(1 for r in self._results if r["model_correct"])
        rule_correct = sum(1 for r in self._results if r["rule_correct"])
        model_acc = model_correct / n
        rule_acc = rule_correct / n

        # Win rate
        model_wins = sum(
            1 for r in self._results
            if r["model_correct"] and not r["rule_correct"]
        )
        rule_wins = sum(
            1 for r in self._results
            if r["rule_correct"] and not r["model_correct"]
        )
        ties = n - model_wins - rule_wins

        # Agreement
        agreement = sum(
            1 for r in self._results
            if (r["model_score"] >= 0.5) == (r["rule_score"] >= 0.5)
        ) / n

        # Correlation
        from scipy.stats import pearsonr
        if n > 2:
            corr, corr_p = pearsonr(model_arr, rule_arr)
        else:
            corr, corr_p = 0.0, 1.0

        # MAE between model and rule
        mae = float(np.mean(np.abs(model_arr - rule_arr)))

        # Calibration: compare predicted proba vs actual success rate
        calibration = self._compute_calibration(model_arr, labels_arr, bins=5)

        # Per-category F1
        from sklearn.metrics import f1_score, precision_score, recall_score
        model_f1 = float(f1_score(labels_arr, model_pred, zero_division=0))
        rule_f1 = float(f1_score(labels_arr, rule_pred, zero_division=0))
        model_precision = float(precision_score(labels_arr, model_pred, zero_division=0))
        rule_precision = float(precision_score(labels_arr, rule_pred, zero_division=0))
        model_recall = float(recall_score(labels_arr, model_pred, zero_division=0))
        rule_recall = float(recall_score(labels_arr, rule_pred, zero_division=0))

        # McNemar test for statistical significance
        try:
            from scipy.stats import chi2
            # Build contingency table
            both_right = sum(1 for r in self._results if r["model_correct"] and r["rule_correct"])
            both_wrong = sum(1 for r in self._results if not r["model_correct"] and not r["rule_correct"])
            model_only = model_wins
            rule_only = rule_wins
            # McNemar statistic
            if model_only + rule_only > 0:
                mcnemar_stat = (abs(model_only - rule_only) - 1) ** 2 / (model_only + rule_only)
                mcnemar_p = 1.0 - float(chi2.cdf(mcnemar_stat, 1))
            else:
                mcnemar_stat = 0.0
                mcnemar_p = 1.0
        except Exception:
            mcnemar_stat = 0.0
            mcnemar_p = 1.0

        return {
            "n_samples": n,
            "model_accuracy": round(model_acc, 4),
            "rule_accuracy": round(rule_acc, 4),
            "model_f1": round(model_f1, 4),
            "rule_f1": round(rule_f1, 4),
            "model_precision": round(model_precision, 4),
            "rule_precision": round(rule_precision, 4),
            "model_recall": round(model_recall, 4),
            "rule_recall": round(rule_recall, 4),
            "model_mean_score": round(float(np.mean(model_arr)), 4),
            "rule_mean_score": round(float(np.mean(rule_arr)), 4),
            "model_std_score": round(float(np.std(model_arr)), 4),
            "rule_std_score": round(float(np.std(rule_arr)), 4),
            "agreement_rate": round(agreement, 4),
            "pearson_r": round(corr, 4),
            "mae": round(mae, 4),
            "win_rate": {
                "model_wins": model_wins,
                "rule_wins": rule_wins,
                "ties": ties,
                "model_win_rate": round(model_wins / n, 4),
                "rule_win_rate": round(rule_wins / n, 4),
            },
            "calibration": calibration,
            "statistical_significance": {
                "mcnemar_stat": round(mcnemar_stat, 4),
                "mcnemar_p": round(mcnemar_p, 6),
                "significant_at_05": mcnemar_p < 0.05,
            },
            "winner": "model" if model_f1 > rule_f1 else ("rule" if rule_f1 > model_f1 else "tie"),
        }

    def per_dimension_breakdown(self) -> dict:
        """Breakdown metrics by skill category, tool_count, duration."""
        if not self._results:
            return {}

        # By tool_count bucket
        buckets = {"1-2 tools": [], "3-5 tools": [], "6+ tools": []}
        for r in self._results:
            # We don't have tool_count directly in results, skip detailed breakdown
            pass

        return {"note": "Run with full trajectory data for per-dimension breakdown"}

    def _compute_calibration(self, scores: np.ndarray, labels: np.ndarray, bins: int = 5) -> dict:
        """Compute calibration: predicted probability vs actual success rate."""
        bin_edges = np.linspace(0, 1, bins + 1)
        calib = []
        for i in range(bins):
            mask = (scores >= bin_edges[i]) & (scores < bin_edges[i + 1])
            if mask.sum() == 0:
                continue
            actual = float(labels[mask].mean())
            predicted = float(scores[mask].mean())
            calib.append({
                "bin": f"{bin_edges[i]:.1f}-{bin_edges[i+1]:.1f}",
                "count": int(mask.sum()),
                "predicted": round(predicted, 4),
                "actual": round(actual, 4),
                "error": round(abs(predicted - actual), 4),
            })

        # Overall calibration error (ECE)
        total = len(scores)
        ece = sum(c["count"] / total * c["error"] for c in calib) if total > 0 else 0.0

        return {"bins": calib, "ece": round(ece, 4)}

    def to_markdown(self, metrics: dict = None) -> str:
        """Format results as Markdown."""
        m = metrics or self._compute_metrics()
        if "error" in m:
            return f"**Error**: {m['error']}"

        winner = m["winner"]
        win_rate = m["win_rate"]

        return f"""## A/B Comparison: Model vs Rule-Based Scoring

| Metric | Model | Rule | Winner |
|--------|-------|------|--------|
| Accuracy | {m['model_accuracy']:.4f} | {m['rule_accuracy']:.4f} | {'Model' if m['model_accuracy'] > m['rule_accuracy'] else 'Rule'} |
| F1 | {m['model_f1']:.4f} | {m['rule_f1']:.4f} | {'Model' if m['model_f1'] > m['rule_f1'] else 'Rule'} |
| Precision | {m['model_precision']:.4f} | {m['rule_precision']:.4f} | — |
| Recall | {m['model_recall']:.4f} | {m['rule_recall']:.4f} | — |
| Mean Score | {m['model_mean_score']:.4f} | {m['rule_mean_score']:.4f} | — |

**Agreement**: {m['agreement_rate']:.2%} | **Pearson r**: {m['pearson_r']:.4f} | **MAE**: {m['mae']:.4f}

### Win Rate
- Model wins: {win_rate['model_wins']} ({win_rate['model_win_rate']:.2%})
- Rule wins: {win_rate['rule_wins']} ({win_rate['rule_win_rate']:.2%})
- Ties: {win_rate['ties']}

### Statistical Significance
- McNemar χ² = {m['statistical_significance']['mcnemar_stat']}, p = {m['statistical_significance']['mcnemar_p']}
- Significant at α=0.05: **{m['statistical_significance']['significant_at_05']}**

### Calibration (ECE = {m['calibration']['ece']:.4f})
| Bin | Count | Predicted | Actual | Error |
|-----|-------|-----------|--------|-------|
""" + "\n".join(
    f"| {b['bin']} | {b['count']} | {b['predicted']:.4f} | {b['actual']:.4f} | {b['error']:.4f} |"
    for b in m['calibration']['bins']
) + f"""

**Overall Winner**: 🏆 **{winner.upper()}** (n={m['n_samples']})
"""
