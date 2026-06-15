"""Training pipeline and CLI for the companion model reward scorer.

Orchestrates end-to-end training from TrajectoryStore data:
  - Feature extraction + dataset preparation
  - Model training with train/test split
  - K-fold cross-validation
  - Comparison with rule-based baseline

Usage:
    python -m pysrc.reward_trainer train --db .trajectory/trajectories.db --backend sklearn --output model.pkl
    python -m pysrc.reward_trainer train --db .trajectory/trajectories.db --backend torch --output model.pt
    python -m pysrc.reward_trainer evaluate --db .trajectory/trajectories.db --model model.pkl
    python -m pysrc.reward_trainer compare --db .trajectory/trajectories.db
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

import numpy as np

from reward_model import (
    FEATURE_NAMES,
    RewardScorer,
    SklearnRewardScorer,
    TorchRewardScorer,
    create_scorer,
    extract_dataset,
    extract_features,
    features_to_array,
)
from trajectory_store import TrajectoryStore

logger = logging.getLogger(__name__)


class RewardTrainer:
    """Trains a RewardScorer from TrajectoryStore data."""

    def __init__(self, store: TrajectoryStore, scorer: RewardScorer):
        self._store = store
        self._scorer = scorer

    def prepare_dataset(self) -> tuple[np.ndarray, np.ndarray]:
        """Extract features + labels from trajectory store.

        Returns:
            (X, y) where X is (n_samples, n_features), y is (n_samples,) binary labels.
        """
        return extract_dataset(self._store)

    def train(self, test_split: float = 0.2) -> dict:
        """Train the model, return metrics."""
        total = self._store.count()
        if total < 10:
            return {"error": "insufficient_data", "n_samples": total, "min_required": 10}

        metrics = self._scorer.train(self._store)
        return metrics

    def cross_validate(self, folds: int = 5) -> dict:
        """K-fold cross-validation.

        Returns:
            Dict with mean ± std for accuracy, precision, recall, f1 across folds.
        """
        from sklearn.model_selection import StratifiedKFold
        from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score
        from sklearn.ensemble import RandomForestClassifier

        X, y = self.prepare_dataset()
        if len(y) < folds * 2:
            return {"error": "insufficient_data", "n_samples": len(y), "min_required": folds * 2}

        # Only sklearn supports fast CV; for torch we'd need per-fold training
        kf = StratifiedKFold(n_splits=folds, shuffle=True, random_state=42)
        scores = {"accuracy": [], "precision": [], "recall": [], "f1": []}

        for train_idx, test_idx in kf.split(X, y):
            X_train, X_test = X[train_idx], X[test_idx]
            y_train, y_test = y[train_idx], y[test_idx]

            model = RandomForestClassifier(
                n_estimators=100, max_depth=8, random_state=42, class_weight="balanced"
            )
            model.fit(X_train, y_train)
            y_pred = model.predict(X_test)

            scores["accuracy"].append(accuracy_score(y_test, y_pred))
            scores["precision"].append(precision_score(y_test, y_pred, zero_division=0))
            scores["recall"].append(recall_score(y_test, y_pred, zero_division=0))
            scores["f1"].append(f1_score(y_test, y_pred, zero_division=0))

        return {
            "folds": folds,
            "n_samples": len(y),
            "accuracy": {"mean": round(np.mean(scores["accuracy"]), 4), "std": round(np.std(scores["accuracy"]), 4)},
            "precision": {"mean": round(np.mean(scores["precision"]), 4), "std": round(np.std(scores["precision"]), 4)},
            "recall": {"mean": round(np.mean(scores["recall"]), 4), "std": round(np.std(scores["recall"]), 4)},
            "f1": {"mean": round(np.mean(scores["f1"]), 4), "std": round(np.std(scores["f1"]), 4)},
        }

    def compare_with_baseline(self) -> dict:
        """Compare model predictions vs rule-based _score_task_trace().

        Extracts trajectories, scores them with both the learned model and
        the rule-based scorer, then computes correlation and agreement.

        Returns:
            Dict with pearson_r, mae, agreement_rate, n_samples.
        """
        from scipy.stats import pearsonr

        X, y = self.prepare_dataset()
        if len(y) < 10:
            return {"error": "insufficient_data", "n_samples": len(y), "min_required": 10}

        # Get model predictions
        model_scores = []
        baseline_scores = []

        trajectories = self._store.query(limit=max(len(y), 1))
        for traj in trajectories:
            features = extract_features(traj)

            # Model prediction
            try:
                model_score = self._scorer.predict(features)
            except Exception:
                model_score = 0.5
            model_scores.append(model_score)

            # Baseline: rule-based reward from the trajectory itself
            baseline = float(traj.get("reward", 0.5))
            baseline_scores.append(baseline)

        model_arr = np.array(model_scores)
        baseline_arr = np.array(baseline_scores)

        # Pearson correlation
        if len(model_arr) > 2:
            r, p_value = pearsonr(model_arr, baseline_arr)
        else:
            r, p_value = 0.0, 1.0

        # MAE
        mae = float(np.mean(np.abs(model_arr - baseline_arr)))

        # Agreement rate (both predict same direction relative to 0.5)
        model_dir = (model_arr >= 0.5).astype(int)
        baseline_dir = (baseline_arr >= 0.5).astype(int)
        agreement = float(np.mean(model_dir == baseline_dir))

        return {
            "n_samples": len(y),
            "pearson_r": round(r, 4),
            "pearson_p": round(p_value, 6),
            "mae": round(mae, 4),
            "agreement_rate": round(agreement, 4),
            "model_mean": round(float(np.mean(model_arr)), 4),
            "model_std": round(float(np.std(model_arr)), 4),
            "baseline_mean": round(float(np.mean(baseline_arr)), 4),
            "baseline_std": round(float(np.std(baseline_arr)), 4),
        }


# ── CLI ──────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Companion model reward scorer — train and evaluate",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # train
    train_cmd = sub.add_parser("train", help="Train a reward scorer")
    train_cmd.add_argument("--db", default=".trajectory/trajectories.db", help="SQLite database path")
    train_cmd.add_argument("--backend", default="sklearn", choices=["sklearn", "torch"],
                           help="Model backend (default: sklearn)")
    train_cmd.add_argument("--output", "-o", required=True, help="Output model file path (.pkl or .pt)")

    # evaluate
    eval_cmd = sub.add_parser("evaluate", help="Evaluate a trained model")
    eval_cmd.add_argument("--db", default=".trajectory/trajectories.db", help="SQLite database path")
    eval_cmd.add_argument("--model", "-m", required=True, help="Model file path (.pkl or .pt)")

    # compare
    compare_cmd = sub.add_parser("compare", help="Compare model vs rule-based baseline")
    compare_cmd.add_argument("--db", default=".trajectory/trajectories.db", help="SQLite database path")
    compare_cmd.add_argument("--model", "-m", help="Model file path (if omitted, trains a new sklearn model)")

    # cross-validate
    cv_cmd = sub.add_parser("cross-validate", help="K-fold cross-validation")
    cv_cmd.add_argument("--db", default=".trajectory/trajectories.db", help="SQLite database path")
    cv_cmd.add_argument("--folds", type=int, default=5, help="Number of folds (default: 5)")

    return parser


def _load_scorer(model_path: str) -> RewardScorer:
    """Auto-detect backend from file extension and load."""
    path = model_path.lower()
    if path.endswith(".pt") or path.endswith(".pth"):
        return TorchRewardScorer.load(model_path)
    else:
        return SklearnRewardScorer.load(model_path)


def main(argv: list[str] = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "cross-validate":
        store = TrajectoryStore(db_path=args.db)
        scorer = create_scorer("sklearn")
        trainer = RewardTrainer(store, scorer)
        result = trainer.cross_validate(folds=args.folds)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        store.close()
        return

    if args.command == "train":
        store = TrajectoryStore(db_path=args.db)
        total = store.count()
        print(f"Training data: {total} trajectories in {args.db}")

        scorer = create_scorer(args.backend)
        trainer = RewardTrainer(store, scorer)
        result = trainer.train()

        if "error" in result:
            print(f"Error: {result['error']} (have {result['n_samples']}, need {result['min_required']})")
            store.close()
            sys.exit(1)

        scorer.save(args.output)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        print(f"Model saved to {args.output}")
        store.close()
        return

    if args.command == "evaluate":
        store = TrajectoryStore(db_path=args.db)
        scorer = _load_scorer(args.model)
        result = scorer.evaluate(store)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        store.close()
        return

    if args.command == "compare":
        store = TrajectoryStore(db_path=args.db)
        if args.model:
            scorer = _load_scorer(args.model)
        else:
            scorer = create_scorer("sklearn")
            scorer.train(store)

        trainer = RewardTrainer(store, scorer)
        result = trainer.compare_with_baseline()
        print(json.dumps(result, indent=2, ensure_ascii=False))
        store.close()
        return


if __name__ == "__main__":
    main()
