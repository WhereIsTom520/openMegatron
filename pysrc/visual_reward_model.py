"""Visual Reward Model — evaluates GUI action quality from screenshots.

Two backends:
  1. Lightweight: heuristic features from screenshot pairs + action metadata
     (no GPU needed, fast, complements existing text reward model)
  2. Vision: ResNet/ViT feature extractor + classifier head
     (requires torch + GPU, full visual understanding)

The lightweight backend can run on any machine and provides immediate
value by scoring misclicks, visual changes, and action coherence.
The vision backend is for when GPU resources are available.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


# ── Lightweight Heuristic Scorer ─────────────────────────────────────────────


def compute_visual_diff_score(before_path: str, after_path: str) -> float:
    """Compute pixel-level difference between two screenshots.

    Returns a score in [0, 1] where 0 = identical, 1 = completely different.
    A moderate difference (~0.1-0.5) indicates the action had visible effect.
    """
    try:
        from PIL import Image
        before = Image.open(before_path).convert("L").resize((256, 256))
        after = Image.open(after_path).convert("L").resize((256, 256))
        before_arr = np.array(before, dtype=np.float32) / 255.0
        after_arr = np.array(after, dtype=np.float32) / 255.0
        diff = np.mean(np.abs(after_arr - before_arr))
        return float(diff)
    except Exception as e:
        logger.debug(f"Visual diff failed: {e}")
        return 0.0


def compute_click_accuracy(x: int, y: int, screen_w: int, screen_h: int) -> float:
    """Heuristic: estimate if a click is in a reasonable UI area.

    Returns 1.0 if click is in central/interactive region, lower for edges.
    This is a rough proxy — real accuracy requires DOM/accessibility tree.
    """
    # Penalize extreme edges (usually not interactive)
    edge_margin = 0.05
    if (x < screen_w * edge_margin or x > screen_w * (1 - edge_margin) or
            y < screen_h * edge_margin or y > screen_h * (1 - edge_margin)):
        return 0.3  # Edge click — likely misclick
    # Penalize very top (usually title bar)
    if y < screen_h * 0.08:
        return 0.4
    return 0.85  # Central area — reasonable


def extract_visual_features(step: dict, screen_w: int = 1920,
                            screen_h: int = 1080) -> np.ndarray:
    """Extract visual features from a single GUI step.

    Args:
        step: dict with keys: action, action_params, result, elapsed_ms,
              screenshot_before, screenshot_after (paths).
        screen_w, screen_h: Screen dimensions.

    Returns:
        numpy array of 18 features.
    """
    features = []

    # 1-3: Action type one-hot (click=1, type=2, scroll=3, other=0)
    action = step.get("action", "")
    features.append(1.0 if action == "click" else 0.0)
    features.append(1.0 if action == "type" else 0.0)
    features.append(1.0 if action == "scroll" else 0.0)

    # 4-5: Click coordinates (normalized to [0,1])
    params = step.get("action_params", {})
    if action == "click":
        x = float(params.get("x", 0))
        y = float(params.get("y", 0))
        features.append(x / screen_w)
        features.append(y / screen_h)
    else:
        features.append(0.5)
        features.append(0.5)

    # 6: Click accuracy heuristic
    if action == "click":
        features.append(compute_click_accuracy(
            int(params.get("x", 0)), int(params.get("y", 0)),
            screen_w, screen_h,
        ))
    else:
        features.append(0.5)

    # 7: Elapsed time (normalized)
    elapsed = float(step.get("elapsed_ms", 0))
    features.append(min(elapsed / 5000.0, 1.0))

    # 8: Action succeeded (from result)
    result = step.get("result", {})
    features.append(1.0 if result.get("status") == "success" else 0.0)

    # 9: Has error message
    features.append(1.0 if result.get("message") else 0.0)

    # 10-11: Visual diff score (before vs after)
    before = step.get("screenshot_before", "")
    after = step.get("screenshot_after", "")
    if before and after and os.path.exists(before) and os.path.exists(after):
        diff = compute_visual_diff_score(before, after)
    else:
        diff = 0.0
    features.append(diff)
    features.append(1.0 if 0.01 < diff < 0.95 else 0.0)  # "meaningful change" indicator

    # 12-13: Screen position features
    features.append(screen_w / 1920.0)
    features.append(screen_h / 1080.0)

    # 14-15: Action complexity
    if action == "type":
        text_len = len(str(params.get("text", "")))
        features.append(min(text_len / 200.0, 1.0))
    else:
        features.append(0.0)
    features.append(1.0 if action == "scroll" else 0.0)

    # 16-18: Reserved for future use
    features.append(0.0)  # dom_distance placeholder
    features.append(0.0)  # ui_element_count placeholder
    features.append(0.0)  # text_alignment_score placeholder

    return np.array(features, dtype=np.float32)


def extract_visual_trajectory_features(trajectory: dict) -> np.ndarray:
    """Extract aggregated features from a full visual trajectory.

    Args:
        trajectory: dict with steps list and metadata.

    Returns:
        numpy array of 20 features for trajectory-level scoring.
    """
    steps = trajectory.get("steps", [])
    if not steps:
        return np.zeros(20, dtype=np.float32)

    # Per-step features
    step_features = []
    for step in steps:
        sf = extract_visual_features(step)
        step_features.append(sf)

    step_arr = np.array(step_features)

    # Aggregate
    features = []
    # 1-4: Step count and action distribution
    features.append(min(len(steps) / 50.0, 1.0))
    features.append(np.mean(step_arr[:, 0]))  # click ratio
    features.append(np.mean(step_arr[:, 1]))  # type ratio
    features.append(np.mean(step_arr[:, 2]))  # scroll ratio

    # 5-8: Click quality
    click_steps = step_arr[step_arr[:, 0] > 0.5]
    if len(click_steps) > 0:
        features.append(np.mean(click_steps[:, 5]))  # avg click accuracy
        features.append(np.min(click_steps[:, 5]))   # worst click
    else:
        features.append(0.5)
        features.append(0.5)
    features.append(float(len(click_steps)))  # click count
    features.append(np.mean(step_arr[:, 7]))  # avg elapsed

    # 9-12: Success and errors
    features.append(np.mean(step_arr[:, 8]))  # success rate
    features.append(np.sum(step_arr[:, 9]))   # error count
    features.append(np.mean(step_arr[:, 10])) # avg visual diff
    features.append(np.mean(step_arr[:, 11])) # meaningful change ratio

    # 13-16: Efficiency
    total_elapsed = trajectory.get("total_elapsed_ms", 0)
    features.append(min(total_elapsed / 120000.0, 1.0))  # normalized total time
    features.append(float(len(steps)) / max(1.0, total_elapsed / 1000.0))  # steps/sec
    features.append(1.0 if trajectory.get("success") else 0.0)
    features.append(0.0)  # placeholder

    # 17-20: Reserved
    for _ in range(4):
        features.append(0.0)

    return np.array(features, dtype=np.float32)


# ── Visual Reward Scorer (Lightweight) ───────────────────────────────────────


class VisualRewardScorer:
    """Lightweight visual reward scorer using heuristic features + RandomForest.

    Can be used standalone (rule-based heuristics) or trained on labeled
    visual trajectory data.

    Training format: same as reward_model.py — (features, label) pairs
    where label=1 for successful trajectories, 0 for failed.
    """

    def __init__(self):
        self._model = None
        self._feature_dim = 20
        self._trained = False

    def score_trajectory(self, trajectory: dict) -> dict:
        """Score a visual trajectory.

        Returns:
            dict with {reward, confidence, dimensions}.
        """
        features = extract_visual_trajectory_features(trajectory)

        if self._trained and self._model is not None:
            try:
                proba = self._model.predict_proba(features.reshape(1, -1))[0]
                reward = float(proba[1]) if len(proba) > 1 else float(proba[0])
                confidence = float(max(proba))
                return {
                    "reward": round(reward, 4),
                    "confidence": round(confidence, 4),
                    "source": "visual_model",
                    "dimensions": self._rule_dimensions(trajectory, features),
                }
            except Exception as e:
                logger.debug(f"Visual model prediction failed: {e}")

        # Rule-based fallback
        return self._rule_based_score(trajectory, features)

    def _rule_based_score(self, trajectory: dict, features: np.ndarray) -> dict:
        """Heuristic visual scoring based on extracted features."""
        steps = trajectory.get("steps", [])

        # Success rate of individual actions
        success_count = sum(
            1 for s in steps
            if s.get("result", {}).get("status") == "success"
        )
        action_success_rate = success_count / max(1, len(steps))

        # Click quality
        click_accuracy = features[4] if features[4] > 0 else 0.5

        # Visual change: moderate change is good, none = stuck, extreme = broken
        avg_diff = features[10]
        visual_score = 1.0 - abs(avg_diff - 0.3) * 2  # peak at ~0.3 diff

        # Efficiency
        step_count = len(steps)
        efficiency = 1.0 / (1.0 + max(0, step_count - 5) * 0.15)

        # Overall
        reward = max(0.0, min(1.0,
            0.35 * action_success_rate +
            0.20 * click_accuracy +
            0.20 * visual_score +
            0.15 * efficiency +
            0.10 * (1.0 if trajectory.get("success") else 0.0)
        ))
        confidence = max(0.0, min(1.0,
            0.40 * action_success_rate +
            0.30 * click_accuracy +
            0.30 * visual_score
        ))

        return {
            "reward": round(reward, 4),
            "confidence": round(confidence, 4),
            "source": "visual_rule",
            "dimensions": self._rule_dimensions(trajectory, features),
        }

    def _rule_dimensions(self, trajectory: dict, features: np.ndarray) -> dict:
        steps = trajectory.get("steps", [])
        success_count = sum(
            1 for s in steps
            if s.get("result", {}).get("status") == "success"
        )
        return {
            "action_success_rate": round(success_count / max(1, len(steps)), 4),
            "click_accuracy": round(float(features[4]), 4),
            "visual_diff_avg": round(float(features[10]), 4),
            "step_count": len(steps),
            "total_elapsed_ms": trajectory.get("total_elapsed_ms", 0),
        }

    def train(self, trajectories: List[dict], labels: List[int]) -> dict:
        """Train the visual reward model on labeled trajectories.

        Args:
            trajectories: List of trajectory dicts.
            labels: List of 0/1 success labels.

        Returns:
            dict with training metrics.
        """
        if len(trajectories) < 10:
            return {"error": "Need at least 10 trajectories to train", "n_samples": len(trajectories)}

        X = np.array([extract_visual_trajectory_features(t) for t in trajectories])
        y = np.array(labels)

        try:
            from sklearn.ensemble import RandomForestClassifier
            from sklearn.model_selection import train_test_split
            from sklearn.metrics import accuracy_score, f1_score

            X_train, X_test, y_train, y_test = train_test_split(
                X, y, test_size=0.2, random_state=42, stratify=y,
            )

            self._model = RandomForestClassifier(
                n_estimators=100, max_depth=6, class_weight="balanced",
                random_state=42,
            )
            self._model.fit(X_train, y_train)
            self._trained = True

            y_pred = self._model.predict(X_test)
            accuracy = accuracy_score(y_test, y_pred)
            f1 = f1_score(y_test, y_pred, zero_division=0)

            return {
                "n_samples": len(trajectories),
                "accuracy": round(accuracy, 4),
                "f1": round(f1, 4),
                "feature_importance": [
                    {"index": i, "importance": round(imp, 4)}
                    for i, imp in sorted(
                        enumerate(self._model.feature_importances_),
                        key=lambda x: x[1], reverse=True,
                    )[:5]
                ],
            }
        except ImportError:
            return {"error": "scikit-learn required for training"}
        except Exception as e:
            return {"error": str(e)}

    def save(self, path: str):
        """Save the trained model."""
        if self._model is not None:
            import joblib
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            joblib.dump(self._model, path)

    def load(self, path: str):
        """Load a trained model."""
        import joblib
        self._model = joblib.load(path)
        self._trained = True


# ── Vision Backend (ResNet) ──────────────────────────────────────────────────


class VisionRewardModel:
    """GPU-accelerated vision reward model using a pretrained CNN backbone.

    Uses torchvision's ResNet18 as feature extractor + custom classifier head.
    Requires: pip install torch torchvision

    Usage:
        model = VisionRewardModel()
        model.train(trajectories, labels, epochs=10)
        score = model.score_trajectory(trajectory)
    """

    def __init__(self, device: str = None):
        self._device = device or ("cuda" if self._has_cuda() else "cpu")
        self._model = None
        self._trained = False

    @staticmethod
    def _has_cuda() -> bool:
        try:
            import torch
            return torch.cuda.is_available()
        except ImportError:
            return False

    def _build_model(self):
        """Build ResNet18 + classifier head."""
        import torch
        import torch.nn as nn
        import torchvision.models as models

        base = models.resnet18(weights=None)
        # Modify first conv for smaller input (256x256 grayscale → 3ch by repeating)
        base.fc = nn.Sequential(
            nn.Linear(512, 128),
            nn.ReLU(),
            nn.Dropout(0.3),
            nn.Linear(128, 1),
            nn.Sigmoid(),
        )
        self._model = base.to(self._device)

    def _preprocess_screenshots(self, paths: List[str]) -> "torch.Tensor":
        """Load and preprocess screenshots for the model."""
        import torch
        from PIL import Image
        from torchvision import transforms

        transform = transforms.Compose([
            transforms.Resize((256, 256)),
            transforms.Grayscale(num_output_channels=3),
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.5, 0.5, 0.5], std=[0.5, 0.5, 0.5]),
        ])

        tensors = []
        for path in paths:
            try:
                if os.path.exists(path):
                    img = Image.open(path).convert("RGB")
                    tensors.append(transform(img))
                else:
                    tensors.append(torch.zeros(3, 256, 256))
            except Exception:
                tensors.append(torch.zeros(3, 256, 256))

        return torch.stack(tensors).to(self._device)

    def score_trajectory(self, trajectory: dict) -> dict:
        """Score using the vision model (falls back to lightweight if not trained)."""
        if not self._trained or self._model is None:
            scorer = VisualRewardScorer()
            return scorer.score_trajectory(trajectory)

        import torch
        steps = trajectory.get("steps", [])
        if not steps:
            return {"reward": 0.5, "confidence": 0.0, "source": "vision_model"}

        # Use the first step's before screenshot
        before_path = steps[0].get("screenshot_before", "")
        tensor = self._preprocess_screenshots([before_path])

        with torch.no_grad():
            score = self._model(tensor).item()

        return {
            "reward": round(score, 4),
            "confidence": 0.7,
            "source": "vision_model",
            "dimensions": {"vision_score": round(score, 4)},
        }

    def train(self, trajectories: List[dict], labels: List[int],
              epochs: int = 10, batch_size: int = 8, lr: float = 1e-3) -> dict:
        """Train the vision model on trajectory screenshots."""
        import torch
        import torch.nn as nn
        from torch.utils.data import DataLoader, TensorDataset

        if len(trajectories) < 10:
            return {"error": "Need at least 10 trajectories"}

        self._build_model()

        # Extract before screenshots from each trajectory's first step
        screenshot_paths = []
        for t in trajectories:
            steps = t.get("steps", [])
            if steps and steps[0].get("screenshot_before"):
                screenshot_paths.append(steps[0]["screenshot_before"])
            else:
                screenshot_paths.append("")

        X = self._preprocess_screenshots(screenshot_paths)
        y = torch.tensor(labels, dtype=torch.float32).to(self._device)

        dataset = TensorDataset(X, y)
        loader = DataLoader(dataset, batch_size=batch_size, shuffle=True)

        optimizer = torch.optim.Adam(self._model.parameters(), lr=lr)
        criterion = nn.BCELoss()

        self._model.train()
        losses = []
        for epoch in range(epochs):
            epoch_loss = 0.0
            for batch_X, batch_y in loader:
                optimizer.zero_grad()
                pred = self._model(batch_X).squeeze()
                loss = criterion(pred, batch_y)
                loss.backward()
                optimizer.step()
                epoch_loss += loss.item()
            avg_loss = epoch_loss / max(1, len(loader))
            losses.append(avg_loss)

        self._trained = True
        return {
            "n_samples": len(trajectories),
            "epochs": epochs,
            "final_loss": round(losses[-1], 4) if losses else 0,
            "loss_history": [round(l, 4) for l in losses],
        }

    def save(self, path: str):
        import torch
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        torch.save(self._model.state_dict(), path)

    def load(self, path: str):
        import torch
        self._build_model()
        self._model.load_state_dict(torch.load(path, map_location=self._device))
        self._model.eval()
        self._trained = True
