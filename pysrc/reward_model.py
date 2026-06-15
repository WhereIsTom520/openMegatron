"""Learned reward model for agent trajectory scoring.

Replaces the hardcoded rule-based _score_task_trace() with a model trained
on trajectory data from Phase 1. Two backends:

  - sklearn: RandomForestClassifier (fast, no GPU, ~100KB model)
  - torch:   3-layer MLP (more expressive, PyTorch .pt format)

Both backends share the same API via the RewardScorer abstract interface.

Usage:
    from trajectory_store import TrajectoryStore
    from reward_model import create_scorer

    store = TrajectoryStore(".trajectory/trajectories.db")
    scorer = create_scorer("sklearn")
    metrics = scorer.train(store)
    score = scorer.predict(features_dict)
    scorer.save("model.pkl")
"""

from __future__ import annotations

import json
import logging
import os
import pickle
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Optional

import numpy as np

logger = logging.getLogger(__name__)

# ── Feature extraction ───────────────────────────────────────────────────────

FEATURE_NAMES = [
    "tool_count",
    "duration_ms",
    "has_error_tool",
    "error_tool_ratio",
    "skill_count",
    "avg_tool_duration",
    "user_input_len",
    "source_is_claude",
    "hour_of_day",
    "stability",
    "speed",
    "efficiency",
    "source_is_codex",
    "source_is_custom",
    "task_is_frontend",
    "task_is_code",
    "task_is_desktop",
    "verification_count",
    "has_build_signal",
    "has_test_signal",
    "has_browser_signal",
    "step_success_ratio",
    "repeated_tool_ratio",
    "feedback_label",
    "feedback_confidence",
]

LEGACY_FEATURE_NAMES = FEATURE_NAMES[:12]


BUILD_MARKERS = ("npm run build", "pnpm build", "yarn build", "vite build", "build succeeded", "build failed")
TEST_MARKERS = ("pytest", "npm test", "pnpm test", "yarn test", "vitest", "jest", "passed", "failed")
BROWSER_MARKERS = ("playwright", "browser", "screenshot", "localhost", "page loaded", "console error")
FRONTEND_MARKERS = ("frontend", "front-end", "react", "vue", "vite", "tsx", "css", "页面", "前端")
CODE_MARKERS = ("code", "coding", "python", "typescript", "javascript", "bug", "test", "代码")
DESKTOP_MARKERS = ("desktop", "computer", "screen", "click", "keyboard", "鼠标", "电脑", "桌面")


def extract_features(trajectory: dict) -> dict:
    """Extract a fixed-size feature vector from a trajectory dict.

    Args:
        trajectory: A dict from TrajectoryStore._row_to_dict() or TrajectoryCollector.

    Returns:
        Dict with keys matching FEATURE_NAMES.
    """
    tool_calls = trajectory.get("tool_calls", []) or []
    tool_count = len(tool_calls)
    duration_ms = float(trajectory.get("duration_ms", 0.0))

    # Error tool analysis
    error_count = sum(1 for tc in tool_calls if _tool_status(tc) in ("error", "denied"))
    has_error_tool = 1 if error_count > 0 else 0
    error_tool_ratio = error_count / max(tool_count, 1)

    # Skill count
    selected = trajectory.get("selected_skills", [])
    skill_count = len(selected) if isinstance(selected, list) else 0

    # Average tool duration
    durations = [float(tc.get("duration_ms", 0.0)) for tc in tool_calls]
    avg_tool_duration = np.mean(durations).item() if durations else 0.0

    # User input length
    user_input = str(trajectory.get("user_input") or trajectory.get("user_goal") or "")
    user_input_len = len(user_input)

    # Source encoding
    source = str(trajectory.get("source", "openmegatron")).lower()
    source_is_claude = 1 if source == "claude_code" else 0
    source_is_codex = 1 if source == "codex" else 0
    source_is_custom = 1 if source not in {"openmegatron", "claude_code", "codex"} else 0

    # Hour of day
    created_at = str(trajectory.get("created_at", ""))
    hour_of_day = 12  # default noon
    if created_at and "T" in created_at:
        try:
            hour_of_day = int(created_at.split("T")[1].split(":")[0])
        except (ValueError, IndexError):
            pass

    # Reward dimensions from metadata (may be pre-computed)
    metadata = trajectory.get("metadata", {}) or {}
    dims = metadata.get("reward_dimensions", {}) or {}

    stability = float(dims.get("stability", 1.0))
    speed = float(dims.get("speed", 1.0))
    efficiency = float(dims.get("efficiency", 1.0))
    feedback = metadata.get("feedback", {}) if isinstance(metadata.get("feedback"), dict) else {}
    feedback_label = float(feedback.get("label", 0.5))
    feedback_confidence = float(feedback.get("confidence", 0.0))

    selected = trajectory.get("selected_skills", [])
    skill_text = " ".join(str(item) for item in selected) if isinstance(selected, list) else str(selected)
    task_text = f"{user_input} {skill_text}".lower()
    task_is_frontend = 1 if any(marker in task_text for marker in FRONTEND_MARKERS) else 0
    task_is_code = 1 if task_is_frontend or any(marker in task_text for marker in CODE_MARKERS) else 0
    task_is_desktop = 1 if any(marker in task_text for marker in DESKTOP_MARKERS) else 0

    verification = _extract_verification_signals(trajectory, metadata)
    tool_names = [str(tc.get("tool", "")).lower() for tc in tool_calls if tc.get("tool")]
    repeated_tool_ratio = 1.0 - (len(set(tool_names)) / max(len(tool_names), 1)) if tool_names else 0.0
    step_success_ratio = (tool_count - error_count) / max(tool_count, 1)

    return {
        "tool_count": tool_count,
        "duration_ms": duration_ms,
        "has_error_tool": has_error_tool,
        "error_tool_ratio": round(error_tool_ratio, 4),
        "skill_count": skill_count,
        "avg_tool_duration": round(avg_tool_duration, 2),
        "user_input_len": user_input_len,
        "source_is_claude": source_is_claude,
        "hour_of_day": hour_of_day,
        "stability": round(stability, 4),
        "speed": round(speed, 4),
        "efficiency": round(efficiency, 4),
        "source_is_codex": source_is_codex,
        "source_is_custom": source_is_custom,
        "task_is_frontend": task_is_frontend,
        "task_is_code": task_is_code,
        "task_is_desktop": task_is_desktop,
        "verification_count": verification["count"],
        "has_build_signal": verification["has_build"],
        "has_test_signal": verification["has_test"],
        "has_browser_signal": verification["has_browser"],
        "step_success_ratio": round(step_success_ratio, 4),
        "repeated_tool_ratio": round(repeated_tool_ratio, 4),
        "feedback_label": round(feedback_label, 4),
        "feedback_confidence": round(feedback_confidence, 4),
    }


def features_to_array(features: dict, feature_names: list[str] = None) -> np.ndarray:
    """Convert a feature dict to a numpy array in FEATURE_NAMES order."""
    names = feature_names or FEATURE_NAMES
    return np.array([float(features.get(name, 0.0)) for name in names], dtype=np.float32)


def extract_training_label(trajectory: dict) -> int:
    """Return the strongest available binary supervision label.

    Priority:
      1. High-confidence explicit/implicit feedback labels.
      2. Verifiable build/test/browser signals.
      3. Stored success flag.
    """
    metadata = trajectory.get("metadata", {}) or {}
    feedback = metadata.get("feedback") if isinstance(metadata.get("feedback"), dict) else None
    if feedback and float(feedback.get("confidence", 0.0)) >= 0.7:
        return 1 if float(feedback.get("label", 0.0)) >= 0.5 else 0

    verification = _extract_verification_signals(trajectory, metadata)
    if verification["count"] > 0:
        return 1 if verification["passed"] >= verification["failed"] else 0

    return 1 if trajectory.get("success") else 0


def extract_dataset(store) -> tuple[np.ndarray, np.ndarray]:
    """Extract (X, y) training dataset from a TrajectoryStore.

    Args:
        store: TrajectoryStore instance.

    Returns:
        (X, y) where X is (n_samples, n_features) float32 array,
        y is (n_samples,) int array of success labels.
    """
    from trajectory_store import TrajectoryStore

    total = store.count()
    if total == 0:
        return (
            np.empty((0, len(FEATURE_NAMES)), dtype=np.float32),
            np.empty((0,), dtype=np.int32),
        )

    # Fetch all trajectories
    trajectories = store.query(limit=max(total, 1), offset=0)

    X_list = []
    y_list = []
    for traj in trajectories:
        features = extract_features(traj)
        X_list.append(features_to_array(features))
        y_list.append(extract_training_label(traj))

    if not X_list:
        return (
            np.empty((0, len(FEATURE_NAMES)), dtype=np.float32),
            np.empty((0,), dtype=np.int32),
        )

    return np.array(X_list, dtype=np.float32), np.array(y_list, dtype=np.int32)


def _extract_verification_signals(trajectory: dict, metadata: dict) -> dict:
    signals = []
    for key in ("verification", "verification_signals", "rubric", "checks"):
        value = metadata.get(key)
        if isinstance(value, list):
            signals.extend(item for item in value if isinstance(item, dict))
        elif isinstance(value, dict):
            signals.extend(item for item in value.values() if isinstance(item, dict))

    text_parts = []
    for call in trajectory.get("tool_calls", []) or []:
        text_parts.extend([
            str(call.get("tool", "")),
            str(call.get("args", "")),
            str(call.get("output_preview", "")),
            str(call.get("raw_output", "")),
            _tool_status(call),
        ])
    combined = " ".join(text_parts).lower()

    passed = 0
    failed = 0
    for signal in signals:
        status = str(signal.get("status") or signal.get("result") or "").lower()
        ok = signal.get("passed")
        if ok is True or status in {"pass", "passed", "success", "ok"}:
            passed += 1
        elif ok is False or status in {"fail", "failed", "error"}:
            failed += 1

    status_words = combined.split()
    if any(marker in combined for marker in BUILD_MARKERS):
        if "failed" in status_words or "error" in status_words:
            failed += 1
        else:
            passed += 1
    if any(marker in combined for marker in TEST_MARKERS):
        if "failed" in status_words or "error" in status_words:
            failed += 1
        elif "passed" in status_words or "success" in status_words:
            passed += 1
    if any(marker in combined for marker in BROWSER_MARKERS):
        if "console error" in combined or "failed" in status_words:
            failed += 1
        else:
            passed += 1

    return {
        "count": passed + failed,
        "passed": passed,
        "failed": failed,
        "has_build": 1 if any(marker in combined for marker in BUILD_MARKERS) else 0,
        "has_test": 1 if any(marker in combined for marker in TEST_MARKERS) else 0,
        "has_browser": 1 if any(marker in combined for marker in BROWSER_MARKERS) else 0,
    }


def _tool_status(call: dict) -> str:
    parsed = call.get("parsed_output") if isinstance(call.get("parsed_output"), dict) else {}
    return str(call.get("status") or parsed.get("status") or "").lower()


# ── Abstract base ────────────────────────────────────────────────────────────

class RewardScorer(ABC):
    """Abstract interface for learned reward scoring."""

    @abstractmethod
    def train(self, store) -> dict:
        """Train the model from trajectory store data. Returns metrics dict."""
        ...

    @abstractmethod
    def predict(self, features: dict) -> float:
        """Predict P(success | features) as a float in [0.0, 1.0]."""
        ...

    def predict_batch(self, features_list: list[dict]) -> list[float]:
        """Predict for a batch of feature dicts."""
        return [self.predict(f) for f in features_list]

    @abstractmethod
    def save(self, path: str) -> None:
        """Persist the model to disk."""
        ...

    @staticmethod
    @abstractmethod
    def load(path: str) -> "RewardScorer":
        """Load a persisted model from disk."""
        ...

    def evaluate(self, store) -> dict:
        """Evaluate the model on trajectory store data.

        Returns:
            Dict with accuracy, precision, recall, f1, n_samples.
        """
        X, y = extract_dataset(store)
        if len(y) == 0:
            return {"accuracy": 0.0, "precision": 0.0, "recall": 0.0, "f1": 0.0, "n_samples": 0}

        from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score

        y_pred_proba = np.array(self.predict_batch(
            [{name: float(X[i, j]) for j, name in enumerate(FEATURE_NAMES)}
             for i in range(len(y))]
        ))
        y_pred = (y_pred_proba >= 0.5).astype(int)

        return {
            "accuracy": round(accuracy_score(y, y_pred), 4),
            "precision": round(precision_score(y, y_pred, zero_division=0), 4),
            "recall": round(recall_score(y, y_pred, zero_division=0), 4),
            "f1": round(f1_score(y, y_pred, zero_division=0), 4),
            "n_samples": len(y),
        }


# ── Sklearn backend ──────────────────────────────────────────────────────────

class SklearnRewardScorer(RewardScorer):
    """Random Forest based reward scorer."""

    def __init__(self, model=None, feature_names: list[str] = None):
        self._model = model
        self._trained = model is not None
        self._feature_names = feature_names or FEATURE_NAMES

    def train(self, store) -> dict:
        """Train RandomForest on trajectory data."""
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.model_selection import train_test_split

        X, y = extract_dataset(store)
        if len(y) < 10:
            logger.warning("Not enough training data (%d samples), need >= 10", len(y))
            return {"error": "insufficient_data", "n_samples": len(y), "min_required": 10}

        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42, stratify=y if len(set(y)) > 1 else None
        )

        self._model = RandomForestClassifier(
            n_estimators=100,
            max_depth=8,
            random_state=42,
            class_weight="balanced",
            n_jobs=-1,
        )
        self._model.fit(X_train, y_train)
        self._trained = True
        self._feature_names = FEATURE_NAMES

        # Evaluate
        from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score

        y_pred = self._model.predict(X_test)
        y_proba = self._model.predict_proba(X_test)

        # Feature importance
        importances = dict(zip(FEATURE_NAMES, self._model.feature_importances_))
        top_features = sorted(importances.items(), key=lambda x: x[1], reverse=True)[:5]

        return {
            "backend": "sklearn",
            "train_samples": len(X_train),
            "test_samples": len(X_test),
            "accuracy": round(accuracy_score(y_test, y_pred), 4),
            "precision": round(precision_score(y_test, y_pred, zero_division=0), 4),
            "recall": round(recall_score(y_test, y_pred, zero_division=0), 4),
            "f1": round(f1_score(y_test, y_pred, zero_division=0), 4),
            "top_features": [{"name": n, "importance": round(v, 4)} for n, v in top_features],
        }

    def predict(self, features: dict) -> float:
        """Predict P(success) using RandomForest probability."""
        if not self._trained or self._model is None:
            return 0.5  # Default prior
        X = features_to_array(features, self._feature_names).reshape(1, -1)
        proba = self._model.predict_proba(X)
        # proba shape: (1, n_classes); positive class is index 1
        if proba.shape[1] >= 2:
            return float(proba[0, 1])
        return float(proba[0, 0])

    def save(self, path: str) -> None:
        """Save model with joblib."""
        import joblib
        os.makedirs(Path(path).parent, exist_ok=True)
        data = {
            "model": self._model,
            "feature_names": self._feature_names,
            "backend": "sklearn",
        }
        joblib.dump(data, path)
        logger.info("Sklearn model saved to %s", path)

    @staticmethod
    def load(path: str) -> "SklearnRewardScorer":
        """Load model from joblib file."""
        import joblib
        data = joblib.load(path)
        scorer = SklearnRewardScorer(
            model=data["model"],
            feature_names=data.get("feature_names") or LEGACY_FEATURE_NAMES,
        )
        return scorer


# ── Torch backend ────────────────────────────────────────────────────────────

class TorchRewardScorer(RewardScorer):
    """PyTorch MLP based reward scorer.

    3-layer network: input_dim -> 64 -> 32 -> 1 (sigmoid).
    """

    def __init__(self, model=None, input_dim: int = None, feature_names: list[str] = None):
        import torch
        self._torch = torch
        self._feature_names = feature_names or FEATURE_NAMES
        self._input_dim = input_dim or len(self._feature_names)
        if model is not None:
            self._model = model
            self._trained = True
        else:
            self._model = self._build_network()
            self._trained = False

    def _build_network(self):
        """Build a 3-layer MLP."""
        import torch.nn as nn
        return nn.Sequential(
            nn.Linear(self._input_dim, 64),
            nn.ReLU(),
            nn.Dropout(0.2),
            nn.Linear(64, 32),
            nn.ReLU(),
            nn.Dropout(0.2),
            nn.Linear(32, 1),
            nn.Sigmoid(),
        )

    def train(self, store) -> dict:
        """Train MLP on trajectory data."""
        import torch
        import torch.nn as nn
        from sklearn.model_selection import train_test_split

        X, y = extract_dataset(store)
        if len(y) < 10:
            logger.warning("Not enough training data (%d samples), need >= 10", len(y))
            return {"error": "insufficient_data", "n_samples": len(y), "min_required": 10}

        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42, stratify=y if len(set(y)) > 1 else None
        )

        # Convert to tensors
        X_train_t = torch.tensor(X_train, dtype=torch.float32)
        y_train_t = torch.tensor(y_train, dtype=torch.float32).view(-1, 1)
        X_test_t = torch.tensor(X_test, dtype=torch.float32)
        y_test_t = torch.tensor(y_test, dtype=torch.float32).view(-1, 1)

        self._feature_names = FEATURE_NAMES
        self._input_dim = len(FEATURE_NAMES)
        self._model = self._build_network()
        criterion = nn.BCELoss()
        optimizer = torch.optim.Adam(self._model.parameters(), lr=1e-3)

        epochs = 50
        batch_size = min(32, len(X_train))
        train_losses = []

        self._model.train()
        for epoch in range(epochs):
            epoch_loss = 0.0
            perm = torch.randperm(len(X_train_t))
            for i in range(0, len(X_train_t), batch_size):
                idx = perm[i:i + batch_size]
                X_batch = X_train_t[idx]
                y_batch = y_train_t[idx]

                optimizer.zero_grad()
                outputs = self._model(X_batch)
                loss = criterion(outputs, y_batch)
                loss.backward()
                optimizer.step()
                epoch_loss += loss.item()

            avg_loss = epoch_loss / max(1, (len(X_train_t) // batch_size))
            train_losses.append(avg_loss)

        self._trained = True

        # Evaluate
        self._model.eval()
        with torch.no_grad():
            y_pred_t = self._model(X_test_t)
            y_pred = (y_pred_t.numpy() >= 0.5).astype(int).flatten()

        from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score

        return {
            "backend": "torch",
            "train_samples": len(X_train),
            "test_samples": len(X_test),
            "epochs": epochs,
            "final_loss": round(train_losses[-1], 6),
            "accuracy": round(accuracy_score(y_test, y_pred), 4),
            "precision": round(precision_score(y_test, y_pred, zero_division=0), 4),
            "recall": round(recall_score(y_test, y_pred, zero_division=0), 4),
            "f1": round(f1_score(y_test, y_pred, zero_division=0), 4),
        }

    def predict(self, features: dict) -> float:
        """Predict P(success) using the MLP."""
        if not self._trained or self._model is None:
            return 0.5
        import torch
        X = features_to_array(features, self._feature_names)
        X_t = torch.tensor(X, dtype=torch.float32).unsqueeze(0)
        self._model.eval()
        with torch.no_grad():
            output = self._model(X_t)
        return float(output.item())

    def save(self, path: str) -> None:
        """Save model state dict as .pt file."""
        import torch
        os.makedirs(Path(path).parent, exist_ok=True)
        data = {
            "state_dict": self._model.state_dict(),
            "input_dim": self._input_dim,
            "feature_names": self._feature_names,
            "backend": "torch",
        }
        torch.save(data, path)
        logger.info("Torch model saved to %s", path)

    @staticmethod
    def load(path: str) -> "TorchRewardScorer":
        """Load model from .pt file."""
        import torch
        data = torch.load(path, map_location="cpu", weights_only=False)
        scorer = TorchRewardScorer(
            input_dim=data["input_dim"],
            feature_names=data.get("feature_names") or LEGACY_FEATURE_NAMES,
        )
        scorer._model.load_state_dict(data["state_dict"])
        scorer._trained = True
        return scorer


# ── Factory ──────────────────────────────────────────────────────────────────

def create_scorer(backend: str = "sklearn") -> RewardScorer:
    """Create a RewardScorer instance.

    Args:
        backend: "sklearn" (RandomForest) or "torch" (MLP).

    Returns:
        A RewardScorer instance (untrained).
    """
    backend = backend.lower().strip()
    if backend == "torch":
        return TorchRewardScorer()
    return SklearnRewardScorer()
