"""Learning curve dashboard — track model performance over time.

Records checkpoints as models are trained and data accumulates. Provides
learning curves, model comparisons, and milestone estimation.

Data is stored in the model_registry.db as a 'learning_checkpoints' table.

Usage:
    from learning_dashboard import LearningDashboard
    dash = LearningDashboard()
    dash.record_checkpoint(store, "model_v2.pkl")
    curve = dash.get_learning_curve()
    print(dash.to_text())
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

import numpy as np

logger = logging.getLogger(__name__)

# ── SQL ──────────────────────────────────────────────────────────────────────

SQL_CREATE_CHECKPOINTS = """
CREATE TABLE IF NOT EXISTS learning_checkpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id TEXT NOT NULL,
    model_path TEXT NOT NULL,
    backend TEXT NOT NULL,
    n_samples INTEGER NOT NULL,
    accuracy REAL NOT NULL,
    f1 REAL NOT NULL,
    precision REAL NOT NULL,
    recall REAL NOT NULL,
    delta_f1 REAL DEFAULT 0.0,
    delta_accuracy REAL DEFAULT 0.0,
    created_at TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}'
)
"""

SQL_INSERT_CHECKPOINT = """
INSERT INTO learning_checkpoints
    (model_id, model_path, backend, n_samples, accuracy, f1, precision, recall,
     delta_f1, delta_accuracy, created_at, metadata)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

SQL_SELECT_ALL_CHECKPOINTS = """
SELECT * FROM learning_checkpoints ORDER BY created_at ASC
"""

SQL_SELECT_LATEST = """
SELECT * FROM learning_checkpoints ORDER BY created_at DESC LIMIT 1
"""


class LearningDashboard:
    """Tracks model performance over time as data accumulates."""

    def __init__(self, registry_db: str = ".trajectory/model_registry.db"):
        self._conn = sqlite3.connect(registry_db, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(SQL_CREATE_CHECKPOINTS)
        self._conn.commit()

    def record_checkpoint(self, store, model_path: str, model_id: str = "",
                          backend: str = "sklearn") -> dict:
        """Record a performance snapshot of a model at this point in time.

        Args:
            store: TrajectoryStore to evaluate on.
            model_path: Path to the model file.
            model_id: Registry model ID.
            backend: "sklearn" or "torch".

        Returns:
            Dict with checkpoint details.
        """
        from eval_ab import _load_scorer
        from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score

        scorer = _load_scorer(model_path)
        total = store.count()
        if total == 0:
            return {"error": "no_data"}

        trajectories = store.query(limit=max(total, 1))
        y_true = []
        y_pred = []
        for traj in trajectories:
            from reward_model import extract_features
            features = extract_features(traj)
            try:
                score = scorer.predict(features)
            except Exception:
                score = 0.5
            y_true.append(1 if traj.get("success") else 0)
            y_pred.append(1 if score >= 0.5 else 0)

        acc = float(accuracy_score(y_true, y_pred))
        f1 = float(f1_score(y_true, y_pred, zero_division=0))
        prec = float(precision_score(y_true, y_pred, zero_division=0))
        rec = float(recall_score(y_true, y_pred, zero_division=0))

        # Compute delta from previous checkpoint
        prev = self._conn.execute(SQL_SELECT_LATEST).fetchone()
        delta_f1 = round(f1 - prev["f1"], 4) if prev else 0.0
        delta_acc = round(acc - prev["accuracy"], 4) if prev else 0.0

        created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self._conn.execute(
            SQL_INSERT_CHECKPOINT,
            (model_id, model_path, backend, total, acc, f1, prec, rec,
             delta_f1, delta_acc, created_at, json.dumps({}, ensure_ascii=False)),
        )
        self._conn.commit()

        return {
            "model_id": model_id,
            "n_samples": total,
            "accuracy": round(acc, 4),
            "f1": round(f1, 4),
            "precision": round(prec, 4),
            "recall": round(rec, 4),
            "delta_f1": delta_f1,
            "delta_accuracy": delta_acc,
            "created_at": created_at,
        }

    def get_learning_curve(self) -> dict:
        """Return learning curve data for plotting."""
        rows = self._conn.execute(SQL_SELECT_ALL_CHECKPOINTS).fetchall()
        if not rows:
            return {"data_points": [], "metrics": {}}

        samples = [r["n_samples"] for r in rows]
        f1s = [r["f1"] for r in rows]
        accs = [r["accuracy"] for r in rows]
        deltas = [r["delta_f1"] for r in rows]

        return {
            "data_points": [
                {
                    "model_id": r["model_id"],
                    "n_samples": r["n_samples"],
                    "f1": r["f1"],
                    "accuracy": r["accuracy"],
                    "delta_f1": r["delta_f1"],
                    "created_at": r["created_at"],
                }
                for r in rows
            ],
            "metrics": {
                "f1_start": f1s[0] if f1s else 0,
                "f1_current": f1s[-1] if f1s else 0,
                "f1_improvement": round(f1s[-1] - f1s[0], 4) if len(f1s) >= 2 else 0,
                "accuracy_current": accs[-1] if accs else 0,
                "total_checkpoints": len(rows),
                "best_f1": round(max(f1s), 4) if f1s else 0,
                "avg_delta_f1": round(np.mean(deltas[1:]), 4) if len(deltas) > 1 else 0,
            },
        }

    def estimate_next_milestone(self, target_f1: float) -> dict:
        """Estimate how many more samples needed to reach target F1.

        Uses linear extrapolation from the learning curve.
        """
        curve = self.get_learning_curve()
        points = curve["data_points"]
        if len(points) < 2:
            return {
                "target_f1": target_f1,
                "estimated_additional_samples": None,
                "confidence": "low",
                "reason": "Need at least 2 checkpoints for estimation",
            }

        # Simple linear extrapolation: f1 = a * log(n_samples) + b
        valid_points = [
            p for p in points
            if p.get("n_samples", 0) > 0 and p.get("f1") is not None
        ]
        xs = np.log([p["n_samples"] for p in valid_points])
        ys = np.array([p["f1"] for p in valid_points])
        if len(xs) < 2 or len(set(xs.tolist())) < 2:
            return {
                "target_f1": target_f1,
                "estimated_additional_samples": None,
                "confidence": "low",
                "reason": "Need at least 2 distinct sample counts for estimation",
            }

        # Fit linear regression on log scale
        coeffs = np.polyfit(xs, ys, 1)
        a, b = coeffs[0], coeffs[1]
        if a <= 0:
            return {
                "target_f1": target_f1,
                "estimated_additional_samples": None,
                "confidence": "low",
                "reason": "No positive learning trend detected",
            }

        # Solve: target_f1 = a * log(n) + b
        import math
        target_n = int(math.exp((target_f1 - b) / a))
        current_n = points[-1]["n_samples"]
        additional = max(0, target_n - current_n)

        return {
            "target_f1": target_f1,
            "current_f1": points[-1]["f1"],
            "current_samples": current_n,
            "estimated_total_samples": target_n,
            "estimated_additional_samples": additional,
            "confidence": "medium" if additional < 10000 else "low",
            "learning_rate": round(a, 6),
        }

    def compare_models(self) -> dict:
        """Head-to-head comparison of all model versions."""
        rows = self._conn.execute(SQL_SELECT_ALL_CHECKPOINTS).fetchall()
        if not rows:
            return {"models": []}

        best = max(rows, key=lambda r: r["f1"])
        return {
            "models": [
                {
                    "model_id": r["model_id"],
                    "n_samples": r["n_samples"],
                    "f1": r["f1"],
                    "accuracy": r["accuracy"],
                    "delta_f1": r["delta_f1"],
                    "created_at": r["created_at"],
                }
                for r in rows
            ],
            "best_model": {
                "model_id": best["model_id"],
                "f1": best["f1"],
                "n_samples": best["n_samples"],
            },
            "total_versions": len(rows),
        }

    def to_text(self) -> str:
        """Format dashboard as plain text."""
        curve = self.get_learning_curve()
        compare = self.compare_models()

        lines = ["=" * 60, "  LEARNING DASHBOARD", "=" * 60, ""]
        m = curve["metrics"]
        lines.append(f"Checkpoints: {m['total_checkpoints']}")
        lines.append(f"F1: {m['f1_start']:.4f} → {m['f1_current']:.4f} (Δ = {m['f1_improvement']:+.4f})")
        lines.append(f"Best F1: {m['best_f1']:.4f}")
        lines.append(f"Avg ΔF1 per retrain: {m['avg_delta_f1']:+.4f}")
        lines.append("")

        if compare["models"]:
            lines.append(f"{'Model':<20} {'Samples':>8} {'F1':>8} {'ΔF1':>8} {'Date'}")
            lines.append("-" * 60)
            for cp in compare["models"]:
                lines.append(
                    f"{cp['model_id']:<20} {cp['n_samples']:>8} {cp['f1']:>8.4f} "
                    f"{cp['delta_f1']:>+8.4f} {cp['created_at'][:10]}"
                )
            lines.append("")
            best = compare["best_model"]
            lines.append(f"Best: {best['model_id']} (F1={best['f1']:.4f}, n={best['n_samples']})")

        return "\n".join(lines)

    def close(self) -> None:
        self._conn.close()
