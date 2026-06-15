"""Tests for reward_model, reward_trainer, and reward_integration.

Covers Phase 2 of the companion model:
  - Feature extraction from trajectory data
  - SklearnRewardScorer: train, predict, save/load, evaluate
  - TorchRewardScorer: train, predict, save/load
  - RewardTrainer: dataset prep, training, cross-validation, baseline comparison
  - RewardIntegration: score_trace, score_strategy, should_promote, install hooks
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure pysrc is on the import path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pysrc"))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_synthetic_store(num_samples: int = 50):
    """Create a TrajectoryStore with synthetic training data."""
    from trajectory_store import TrajectoryStore
    import random
    random.seed(42)

    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "synth_train.db")
    store = TrajectoryStore(db_path=db_path)

    for i in range(num_samples):
        tool_count = random.randint(1, 8)
        duration_ms = random.uniform(500, 60000)
        has_error = random.random() < 0.3
        error_count = random.randint(1, 3) if has_error else 0
        error_ratio = error_count / max(tool_count, 1)
        success = not has_error  # Simple rule: errors = failure

        tool_calls = []
        for j in range(tool_count):
            is_error = j < error_count
            tool_calls.append({
                "tool": f"tool_{j}",
                "args": '{"key":"value"}',
                "output_preview": "error output" if is_error else "success output",
                "duration_ms": random.uniform(100, 5000),
                "status": "error" if is_error else "success",
            })

        store.store({
            "session_id": f"session_{i}",
            "user_input": f"Test request {i}" + "x" * random.randint(10, 200),
            "selected_skills": ["code-assistant-2.0.0"] if random.random() < 0.5 else ["paper-reader-1.0.0"],
            "tool_calls": tool_calls,
            "reward": 0.8 if success else 0.3,
            "confidence": 0.7 if success else 0.4,
            "success": success,
            "tool_count": tool_count,
            "duration_ms": duration_ms,
            "final_answer": "Answer text here",
            "source": "openmegatron",
            "created_at": f"2025-06-{i % 28 + 1:02d}T{random.randint(8, 22):02d}:00:00Z",
            "metadata": {
                "reward_dimensions": {
                    "success": success,
                    "stability": 1.0 - error_ratio,
                    "speed": 1.0 / (1.0 + duration_ms / 60000),
                    "efficiency": 1.0 / (1.0 + max(0, tool_count - 3) * 0.18),
                    "tool_count": tool_count,
                    "failures": error_count,
                    "duration_ms": duration_ms,
                },
            },
        })

    return store, tmpdir


def _cleanup_store(store, tmpdir):
    """Close store and remove temp directory."""
    import shutil
    store.close()
    shutil.rmtree(tmpdir, ignore_errors=True)


# ── TestFeatureExtraction ─────────────────────────────────────────────────────

class TestFeatureExtraction(unittest.TestCase):
    def test_extract_features_from_trajectory(self):
        from reward_model import extract_features, FEATURE_NAMES
        traj = {
            "session_id": "s1",
            "user_input": "Hello world",
            "selected_skills": ["code-assistant-2.0.0", "code-review-1.0.0"],
            "tool_calls": [
                {"tool": "run_skill_script", "args": "{}", "output_preview": "ok", "duration_ms": 100, "status": "success"},
                {"tool": "search", "args": "{}", "output_preview": "ok", "duration_ms": 200, "status": "success"},
            ],
            "duration_ms": 300.0,
            "success": True,
            "source": "openmegatron",
            "created_at": "2025-06-15T14:30:00Z",
            "metadata": {"reward_dimensions": {"stability": 1.0, "speed": 0.9, "efficiency": 0.8}},
        }

        features = extract_features(traj)
        self.assertEqual(features["tool_count"], 2)
        self.assertEqual(features["duration_ms"], 300.0)
        self.assertEqual(features["has_error_tool"], 0)
        self.assertEqual(features["error_tool_ratio"], 0.0)
        self.assertEqual(features["skill_count"], 2)
        self.assertAlmostEqual(features["avg_tool_duration"], 150.0)
        self.assertEqual(features["user_input_len"], 11)
        self.assertEqual(features["source_is_claude"], 0)
        self.assertEqual(features["source_is_codex"], 0)
        self.assertEqual(features["source_is_custom"], 0)
        self.assertEqual(features["task_is_code"], 1)
        self.assertEqual(features["task_is_frontend"], 0)
        self.assertEqual(features["hour_of_day"], 14)
        self.assertAlmostEqual(features["stability"], 1.0)
        self.assertAlmostEqual(features["speed"], 0.9)
        self.assertAlmostEqual(features["efficiency"], 0.8)
        self.assertAlmostEqual(features["step_success_ratio"], 1.0)
        self.assertAlmostEqual(features["repeated_tool_ratio"], 0.0)

        # All feature names are present
        for name in FEATURE_NAMES:
            self.assertIn(name, features)

    def test_extract_features_with_error_tool(self):
        from reward_model import extract_features
        traj = {
            "session_id": "s2",
            "user_input": "test",
            "selected_skills": [],
            "tool_calls": [
                {"tool": "run", "args": "{}", "output_preview": "fail", "duration_ms": 500, "status": "error"},
                {"tool": "run", "args": "{}", "output_preview": "ok", "duration_ms": 300, "status": "success"},
                {"tool": "run", "args": "{}", "output_preview": "denied", "duration_ms": 200, "status": "denied"},
            ],
            "duration_ms": 1000.0,
            "success": False,
            "source": "claude_code",
            "created_at": "2025-06-15T09:00:00Z",
            "metadata": {},
        }

        features = extract_features(traj)
        self.assertEqual(features["tool_count"], 3)
        self.assertEqual(features["has_error_tool"], 1)
        self.assertAlmostEqual(features["error_tool_ratio"], 2.0 / 3.0, places=3)
        self.assertEqual(features["source_is_claude"], 1)
        self.assertEqual(features["hour_of_day"], 9)

    def test_extract_features_with_rubric_signals(self):
        from reward_model import extract_features
        traj = {
            "session_id": "web1",
            "user_input": "Build a React frontend and verify it in browser",
            "selected_skills": ["code-assistant-2.0.0"],
            "tool_calls": [
                {"tool": "shell", "args": "npm run build", "output_preview": "build succeeded", "duration_ms": 1000, "status": "success"},
                {"tool": "browser", "args": "playwright screenshot localhost", "output_preview": "page loaded", "duration_ms": 500, "status": "success"},
                {"tool": "shell", "args": "npm run build", "output_preview": "build succeeded", "duration_ms": 900, "status": "success"},
            ],
            "duration_ms": 2400.0,
            "success": True,
            "source": "codex",
            "created_at": "2025-06-15T10:00:00Z",
            "metadata": {
                "feedback": {"label": 1, "confidence": 0.9},
                "verification": [{"name": "build", "passed": True}],
            },
        }

        features = extract_features(traj)
        self.assertEqual(features["source_is_codex"], 1)
        self.assertEqual(features["task_is_frontend"], 1)
        self.assertEqual(features["task_is_code"], 1)
        self.assertGreaterEqual(features["verification_count"], 1)
        self.assertEqual(features["has_build_signal"], 1)
        self.assertEqual(features["has_browser_signal"], 1)
        self.assertAlmostEqual(features["feedback_label"], 1.0)
        self.assertAlmostEqual(features["feedback_confidence"], 0.9)
        self.assertGreater(features["repeated_tool_ratio"], 0.0)

    def test_extract_training_label_prefers_feedback(self):
        from reward_model import extract_training_label
        traj = {
            "success": True,
            "metadata": {
                "feedback": {"label": 0, "confidence": 0.95},
            },
        }
        self.assertEqual(extract_training_label(traj), 0)

    def test_extract_training_label_uses_verification(self):
        from reward_model import extract_training_label
        traj = {
            "success": True,
            "metadata": {
                "verification": [
                    {"name": "build", "passed": False},
                    {"name": "browser", "passed": False},
                    {"name": "lint", "passed": True},
                ],
            },
        }
        self.assertEqual(extract_training_label(traj), 0)

    def test_extract_features_minimal(self):
        from reward_model import extract_features
        traj = {"session_id": "min", "tool_calls": []}
        features = extract_features(traj)
        self.assertEqual(features["tool_count"], 0)
        self.assertEqual(features["has_error_tool"], 0)
        self.assertEqual(features["skill_count"], 0)
        self.assertEqual(features["hour_of_day"], 12)

    def test_features_to_array_shape(self):
        from reward_model import features_to_array, extract_features, FEATURE_NAMES
        traj = {"session_id": "s1", "user_input": "hi", "tool_calls": []}
        features = extract_features(traj)
        arr = features_to_array(features)
        self.assertEqual(arr.shape, (len(FEATURE_NAMES),))


# ── TestSklearnRewardScorer ───────────────────────────────────────────────────

class TestSklearnRewardScorer(unittest.TestCase):
    def setUp(self):
        from reward_model import SklearnRewardScorer
        self.scorer = SklearnRewardScorer()
        self.store, self.tmpdir = _make_synthetic_store(60)

    def tearDown(self):
        _cleanup_store(self.store, self.tmpdir)

    def test_train_and_predict(self):
        metrics = self.scorer.train(self.store)
        self.assertIn("accuracy", metrics)
        self.assertIn("f1", metrics)
        self.assertIn("backend", metrics)
        self.assertEqual(metrics["backend"], "sklearn")
        self.assertGreaterEqual(metrics["accuracy"], 0.5)

        # Predict on a feature dict
        from reward_model import extract_features
        traj = {"session_id": "s1", "user_input": "test", "tool_calls": [
            {"tool": "t1", "args": "{}", "output_preview": "ok", "duration_ms": 100, "status": "success"}
        ], "duration_ms": 100, "success": True, "source": "openmegatron", "created_at": "2025-06-15T12:00:00Z", "metadata": {}}
        features = extract_features(traj)
        score = self.scorer.predict(features)
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 1.0)

    def test_save_and_load(self):
        self.scorer.train(self.store)
        save_path = os.path.join(self.tmpdir, "model.pkl")
        self.scorer.save(save_path)
        self.assertTrue(os.path.exists(save_path))

        from reward_model import SklearnRewardScorer
        loaded = SklearnRewardScorer.load(save_path)
        from reward_model import extract_features
        traj = {"session_id": "s1", "user_input": "test", "tool_calls": [], "duration_ms": 0, "success": True, "source": "openmegatron", "created_at": "2025-06-15T12:00:00Z", "metadata": {}}
        features = extract_features(traj)
        score = loaded.predict(features)
        self.assertGreaterEqual(score, 0.0)

    def test_evaluate(self):
        self.scorer.train(self.store)
        result = self.scorer.evaluate(self.store)
        self.assertIn("accuracy", result)
        self.assertIn("precision", result)
        self.assertIn("recall", result)
        self.assertIn("f1", result)
        self.assertGreater(result["n_samples"], 0)

    def test_empty_store(self):
        from trajectory_store import TrajectoryStore
        empty_store = TrajectoryStore(db_path=os.path.join(self.tmpdir, "empty.db"))
        result = self.scorer.train(empty_store)
        self.assertIn("error", result)
        self.assertEqual(result["error"], "insufficient_data")
        empty_store.close()

    def test_predict_before_train(self):
        score = self.scorer.predict({"tool_count": 1})
        self.assertEqual(score, 0.5)  # Default prior

    def test_predict_batch(self):
        self.scorer.train(self.store)
        from reward_model import extract_features
        features_list = [
            extract_features({"session_id": "s1", "user_input": "a", "tool_calls": [], "duration_ms": 0, "success": True, "source": "openmegatron", "created_at": "2025-06-15T12:00:00Z", "metadata": {}}),
            extract_features({"session_id": "s2", "user_input": "b", "tool_calls": [], "duration_ms": 0, "success": True, "source": "openmegatron", "created_at": "2025-06-15T12:00:00Z", "metadata": {}}),
        ]
        scores = self.scorer.predict_batch(features_list)
        self.assertEqual(len(scores), 2)
        for s in scores:
            self.assertGreaterEqual(s, 0.0)
            self.assertLessEqual(s, 1.0)


# ── TestTorchRewardScorer ─────────────────────────────────────────────────────

class TestTorchRewardScorer(unittest.TestCase):
    def setUp(self):
        from reward_model import TorchRewardScorer
        self.scorer = TorchRewardScorer()
        self.store, self.tmpdir = _make_synthetic_store(60)

    def tearDown(self):
        _cleanup_store(self.store, self.tmpdir)

    def test_train_and_predict(self):
        metrics = self.scorer.train(self.store)
        self.assertIn("accuracy", metrics)
        self.assertIn("f1", metrics)
        self.assertIn("backend", metrics)
        self.assertEqual(metrics["backend"], "torch")
        self.assertIn("final_loss", metrics)

        from reward_model import extract_features
        traj = {"session_id": "s1", "user_input": "test", "tool_calls": [
            {"tool": "t1", "args": "{}", "output_preview": "ok", "duration_ms": 100, "status": "success"}
        ], "duration_ms": 100, "success": True, "source": "openmegatron", "created_at": "2025-06-15T12:00:00Z", "metadata": {}}
        features = extract_features(traj)
        score = self.scorer.predict(features)
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 1.0)

    def test_save_and_load(self):
        self.scorer.train(self.store)
        save_path = os.path.join(self.tmpdir, "model.pt")
        self.scorer.save(save_path)
        self.assertTrue(os.path.exists(save_path))

        from reward_model import TorchRewardScorer
        loaded = TorchRewardScorer.load(save_path)
        from reward_model import extract_features
        traj = {"session_id": "s1", "user_input": "test", "tool_calls": [], "duration_ms": 0, "success": True, "source": "openmegatron", "created_at": "2025-06-15T12:00:00Z", "metadata": {}}
        features = extract_features(traj)
        score = loaded.predict(features)
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 1.0)

    def test_empty_store(self):
        from trajectory_store import TrajectoryStore
        empty_store = TrajectoryStore(db_path=os.path.join(self.tmpdir, "empty_torch.db"))
        result = self.scorer.train(empty_store)
        self.assertIn("error", result)
        empty_store.close()

    def test_predict_before_train(self):
        score = self.scorer.predict({"tool_count": 1})
        self.assertEqual(score, 0.5)


# ── TestCreateScorer ──────────────────────────────────────────────────────────

class TestCreateScorer(unittest.TestCase):
    def test_create_sklearn(self):
        from reward_model import create_scorer, SklearnRewardScorer
        scorer = create_scorer("sklearn")
        self.assertIsInstance(scorer, SklearnRewardScorer)

    def test_create_torch(self):
        from reward_model import create_scorer, TorchRewardScorer
        scorer = create_scorer("torch")
        self.assertIsInstance(scorer, TorchRewardScorer)

    def test_create_default(self):
        from reward_model import create_scorer, SklearnRewardScorer
        scorer = create_scorer()
        self.assertIsInstance(scorer, SklearnRewardScorer)

    def test_create_unknown_falls_back_to_sklearn(self):
        from reward_model import create_scorer, SklearnRewardScorer
        scorer = create_scorer("unknown_backend")
        self.assertIsInstance(scorer, SklearnRewardScorer)


# ── TestRewardTrainer ─────────────────────────────────────────────────────────

class TestRewardTrainer(unittest.TestCase):
    def setUp(self):
        from reward_model import create_scorer
        from reward_trainer import RewardTrainer
        self.store, self.tmpdir = _make_synthetic_store(60)
        self.scorer = create_scorer("sklearn")
        self.trainer = RewardTrainer(self.store, self.scorer)

    def tearDown(self):
        _cleanup_store(self.store, self.tmpdir)

    def test_prepare_dataset(self):
        X, y = self.trainer.prepare_dataset()
        self.assertGreater(len(X), 0)
        self.assertGreater(len(y), 0)
        self.assertEqual(len(X), len(y))
        from reward_model import FEATURE_NAMES
        self.assertEqual(X.shape[1], len(FEATURE_NAMES))

    def test_train_produces_metrics(self):
        metrics = self.trainer.train()
        self.assertIn("accuracy", metrics)
        self.assertIn("f1", metrics)
        self.assertIn("train_samples", metrics)
        self.assertIn("test_samples", metrics)

    def test_cross_validate(self):
        result = self.trainer.cross_validate(folds=3)
        self.assertIn("folds", result)
        self.assertEqual(result["folds"], 3)
        self.assertIn("accuracy", result)
        self.assertIn("mean", result["accuracy"])
        self.assertIn("std", result["accuracy"])

    def test_compare_with_baseline(self):
        self.scorer.train(self.store)
        result = self.trainer.compare_with_baseline()
        self.assertIn("pearson_r", result)
        self.assertIn("mae", result)
        self.assertIn("agreement_rate", result)
        self.assertIn("n_samples", result)
        self.assertGreaterEqual(result["agreement_rate"], 0.0)
        self.assertLessEqual(result["agreement_rate"], 1.0)

    def test_insufficient_data(self):
        from trajectory_store import TrajectoryStore
        from reward_model import create_scorer
        from reward_trainer import RewardTrainer
        tiny_store = TrajectoryStore(db_path=os.path.join(self.tmpdir, "tiny.db"))
        tiny_store.store({"session_id": "s1", "user_input": "a", "tool_calls": [], "success": True, "created_at": "2025-01-01T00:00:00Z"})
        tiny_scorer = create_scorer("sklearn")
        tiny_trainer = RewardTrainer(tiny_store, tiny_scorer)
        result = tiny_trainer.train()
        self.assertIn("error", result)
        tiny_store.close()


# ── TestRewardIntegration ─────────────────────────────────────────────────────

class TestRewardIntegration(unittest.TestCase):
    def setUp(self):
        from reward_model import create_scorer
        from reward_integration import RewardIntegration
        self.store, self.tmpdir = _make_synthetic_store(60)
        self.scorer = create_scorer("sklearn")
        self.scorer.train(self.store)
        self.integration = RewardIntegration(self.scorer)

    def tearDown(self):
        _cleanup_store(self.store, self.tmpdir)

    def test_score_trace_matches_format(self):
        """Output must match _score_task_trace() schema."""
        trace = {
            "session_id": "s1",
            "user_goal": "test",
            "selected_skills": ["code"],
            "tool_calls": [
                {"tool": "run", "args": "{}", "raw_output": "ok", "parsed_output": {"status": "success"}, "duration_ms": 500},
            ],
            "success": True,
            "final_answer": "Done",
            "started_at": 1700000000.0,
        }
        result = self.integration.score_trace(trace)
        self.assertIn("reward", result)
        self.assertIn("confidence", result)
        self.assertIn("dimensions", result)
        self.assertGreaterEqual(result["reward"], 0.0)
        self.assertLessEqual(result["reward"], 1.0)
        self.assertIn("success", result["dimensions"])
        self.assertIn("stability", result["dimensions"])
        self.assertIn("speed", result["dimensions"])
        self.assertIn("efficiency", result["dimensions"])
        self.assertIn("tool_count", result["dimensions"])

    def test_score_trace_no_tool_calls(self):
        trace = {
            "session_id": "s1",
            "tool_calls": [],
            "success": False,
            "final_answer": "I cannot do that",
            "started_at": 1700000000.0,
        }
        result = self.integration.score_trace(trace)
        self.assertIn("reward", result)
        self.assertGreaterEqual(result["reward"], 0.0)
        self.assertEqual(result["dimensions"]["tool_count"], 0)

    def test_score_strategy(self):
        score = self.integration.score_strategy("empty_research_broaden", {
            "category": "research",
            "error_type": "empty_result",
            "parameters": {"query": "test"},
        })
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 1.0)

    def test_should_promote(self):
        from guided_evolution import EvolutionState, EvolutionLevel
        state = EvolutionState(
            category="research",
            level=EvolutionLevel.REACTIVE,
            total_executions=15,
            total_failures=2,
            consecutive_successes=5,
        )
        result = self.integration.should_promote(state)
        self.assertIsInstance(result, bool)

    def test_should_promote_new_category(self):
        from guided_evolution import EvolutionState, EvolutionLevel
        state = EvolutionState(
            category="new_cat",
            level=EvolutionLevel.REACTIVE,
            total_executions=2,
            total_failures=2,
            consecutive_successes=0,
        )
        result = self.integration.should_promote(state)
        self.assertFalse(result)  # Too few executions + low success rate

    def test_install_reward_model(self):
        from reward_integration import install_reward_model
        self.scorer.save(os.path.join(self.tmpdir, "test_model.pkl"))

        mock_agent = MagicMock()
        mock_agent._score_task_trace = MagicMock(return_value={"reward": 0.5})

        integration = install_reward_model(
            mock_agent,
            os.path.join(self.tmpdir, "test_model.pkl"),
            backend="sklearn",
        )
        self.assertIsNotNone(integration)
        self.assertTrue(hasattr(mock_agent, "_reward_integration"))
        # _score_task_trace should have been replaced
        self.assertNotEqual(mock_agent._score_task_trace, MagicMock(return_value={"reward": 0.5}))

    def test_install_reward_model_into_evolution(self):
        from reward_integration import install_reward_model_into_evolution
        from guided_evolution import GuidedEvolution
        self.scorer.save(os.path.join(self.tmpdir, "evo_model.pkl"))

        evolution = GuidedEvolution()
        integration = install_reward_model_into_evolution(
            evolution,
            os.path.join(self.tmpdir, "evo_model.pkl"),
        )
        self.assertIsNotNone(integration)
        self.assertTrue(hasattr(evolution, "_reward_integration"))

    def test_fallback_when_model_unavailable(self):
        """When model file doesn't exist, should raise FileNotFoundError."""
        from reward_integration import install_reward_model
        mock_agent = MagicMock()
        with self.assertRaises(FileNotFoundError):
            install_reward_model(mock_agent, "/nonexistent/model.pkl")

    def test_scorer_property(self):
        self.assertIs(self.integration.scorer, self.scorer)


if __name__ == "__main__":
    unittest.main()
