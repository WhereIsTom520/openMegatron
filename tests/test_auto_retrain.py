"""Tests for model_registry and auto_retrain.

Covers Phase 3 of the companion model:
  - ModelRegistry: register, activate, get_best, version tracking
  - AutoRetrainLoop: should_retrain, retrain, status, install
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pysrc"))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_synthetic_store(num_samples: int = 60):
    """Create a TrajectoryStore with synthetic data."""
    from trajectory_store import TrajectoryStore
    import random
    random.seed(42)

    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "synth.db")
    store = TrajectoryStore(db_path=db_path)

    for i in range(num_samples):
        tool_count = random.randint(1, 6)
        duration_ms = random.uniform(500, 30000)
        has_error = random.random() < 0.25
        error_count = random.randint(1, 2) if has_error else 0
        success = not has_error

        tool_calls = []
        for j in range(tool_count):
            is_error = j < error_count
            tool_calls.append({
                "tool": f"tool_{j}",
                "args": "{}",
                "output_preview": "error" if is_error else "ok",
                "duration_ms": random.uniform(100, 3000),
                "status": "error" if is_error else "success",
            })

        store.store({
            "session_id": f"s{i}",
            "user_input": f"Request {i}" + "x" * random.randint(5, 80),
            "selected_skills": ["code-assistant-2.0.0"],
            "tool_calls": tool_calls,
            "reward": 0.75 if success else 0.25,
            "confidence": 0.7,
            "success": success,
            "tool_count": tool_count,
            "duration_ms": duration_ms,
            "final_answer": "Done",
            "source": "openmegatron",
            "created_at": f"2025-06-{i % 28 + 1:02d}T12:00:00Z",
            "metadata": {"reward_dimensions": {"success": success, "stability": 0.9, "speed": 0.8, "efficiency": 0.7}},
        })

    return store, tmpdir


def _cleanup(store, tmpdir):
    import shutil
    store.close()
    shutil.rmtree(tmpdir, ignore_errors=True)


# ── TestModelRegistry ─────────────────────────────────────────────────────────

class TestModelRegistry(unittest.TestCase):
    def setUp(self):
        from model_registry import ModelRegistry
        self.tmpdir = tempfile.mkdtemp()
        self.registry = ModelRegistry(db_path=os.path.join(self.tmpdir, "registry.db"))

    def tearDown(self):
        self.registry.close()
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_register_and_get_active(self):
        mid = self.registry.register("model_v1.pkl", "sklearn", 0.85, 0.82, 100)
        self.assertTrue(mid.startswith("model_"))

        active = self.registry.get_active()
        self.assertIsNotNone(active)
        self.assertEqual(active["file_path"], "model_v1.pkl")
        self.assertEqual(active["accuracy"], 0.85)
        self.assertEqual(active["f1"], 0.82)
        self.assertTrue(active["is_active"])

    def test_register_auto_deactivates_previous(self):
        mid1 = self.registry.register("v1.pkl", "sklearn", 0.80, 0.78, 50)
        mid2 = self.registry.register("v2.pkl", "sklearn", 0.88, 0.86, 150)

        active = self.registry.get_active()
        self.assertEqual(active["id"], mid2)
        self.assertEqual(active["file_path"], "v2.pkl")

        # Old model should be inactive
        old = self.registry.get(mid1)
        self.assertFalse(old["is_active"])

    def test_register_inactive_preserves_current_active(self):
        mid1 = self.registry.register("v1.pkl", "sklearn", 0.80, 0.78, 50)
        mid2 = self.registry.register(
            "candidate.pkl",
            "sklearn",
            0.70,
            0.68,
            75,
            activate=False,
            status="candidate",
        )

        active = self.registry.get_active()
        self.assertEqual(active["id"], mid1)
        candidate = self.registry.get(mid2)
        self.assertFalse(candidate["is_active"])
        self.assertEqual(candidate["status"], "candidate")

    def test_get_best(self):
        self.registry.register("v1.pkl", "sklearn", 0.80, 0.78, 50)
        self.registry.register("v2.pkl", "sklearn", 0.88, 0.86, 150)
        self.registry.register("v3.pkl", "sklearn", 0.90, 0.91, 200)

        best = self.registry.get_best()
        self.assertEqual(best["f1"], 0.91)

    def test_activate(self):
        mid1 = self.registry.register("v1.pkl", "sklearn", 0.80, 0.78, 50)
        mid2 = self.registry.register("v2.pkl", "sklearn", 0.88, 0.86, 150)

        # Activate back to v1
        self.assertTrue(self.registry.activate(mid1))
        active = self.registry.get_active()
        self.assertEqual(active["id"], mid1)
        self.assertTrue(self.registry.get(mid1)["is_active"])
        self.assertFalse(self.registry.get(mid2)["is_active"])

    def test_activate_nonexistent(self):
        self.assertFalse(self.registry.activate("nonexistent_id"))

    def test_mark_retired(self):
        mid = self.registry.register("v1.pkl", "sklearn", 0.80, 0.78, 50)
        self.assertTrue(self.registry.mark_retired(mid))
        m = self.registry.get(mid)
        self.assertEqual(m["status"], "retired")
        self.assertFalse(m["is_active"])

    def test_list_all(self):
        self.registry.register("v1.pkl", "sklearn", 0.80, 0.78, 50)
        self.registry.register("v2.pkl", "torch", 0.88, 0.86, 150)
        models = self.registry.list_all()
        self.assertEqual(len(models), 2)

    def test_count(self):
        self.assertEqual(self.registry.count(), 0)
        self.registry.register("v1.pkl", "sklearn", 0.80, 0.78, 50)
        self.assertEqual(self.registry.count(), 1)


# ── TestAutoRetrainLoop ───────────────────────────────────────────────────────

class TestAutoRetrainLoop(unittest.TestCase):
    def setUp(self):
        from auto_retrain import AutoRetrainLoop
        self.store, self.store_dir = _make_synthetic_store(60)
        self.registry_dir = tempfile.mkdtemp()
        self.model_dir = tempfile.mkdtemp()

        self.loop = AutoRetrainLoop(
            trajectory_db=self.store._conn.database.lstrip("file:").split("?")[0]
            if hasattr(self.store._conn, 'database') else "",
            registry_db=os.path.join(self.registry_dir, "registry.db"),
            model_dir=self.model_dir,
            backend="sklearn",
            retrain_threshold=20,
        )
        # Override trajectory_db with a function that creates fresh store each time
        self.loop._trajectory_db = os.path.join(self.store_dir, "synth.db")

    def tearDown(self):
        self.loop.close()
        _cleanup(self.store, self.store_dir)
        import shutil
        shutil.rmtree(self.registry_dir, ignore_errors=True)
        shutil.rmtree(self.model_dir, ignore_errors=True)

    def test_should_retrain_below_threshold(self):
        # Last trained at 0, threshold=20, have 60 → should retrain
        should, total, new = self.loop.should_retrain()
        self.assertTrue(should)
        self.assertEqual(total, 60)
        self.assertEqual(new, 60)

    def test_should_retrain_after_update(self):
        # Simulate having trained at 55
        self.loop._last_train_count = 55
        should, total, new = self.loop.should_retrain()
        self.assertFalse(should)  # Only 5 new, below threshold 20

    def test_retrain_first_model(self):
        result = self.loop.retrain()
        self.assertIn(result["status"], ("deployed", "trained_not_deployed"))
        self.assertIn("model_id", result)
        self.assertIn("accuracy", result)
        self.assertIn("f1", result)
        self.assertGreater(result["n_samples"], 0)
        # First model should be deployed
        if result["status"] == "deployed":
            self.assertIsNone(result["previous_f1"])

    def test_retrain_improvement_deploys(self):
        # Train first model
        result1 = self.loop.retrain()
        self.assertIn("model_id", result1)

        # Train second model — should compare and decide
        result2 = self.loop.retrain()
        self.assertIn("model_id", result2)
        self.assertIn("deploy_reason", result2)
        self.assertIsNotNone(result2["previous_f1"])

    def test_get_status(self):
        status = self.loop.get_status()
        self.assertIn("total_trajectories", status)
        self.assertIn("new_since_last_train", status)
        self.assertIn("retrain_threshold", status)
        self.assertIn("should_retrain", status)
        self.assertEqual(status["retrain_threshold"], 20)

    def test_bind_agent(self):
        mock_agent = MagicMock()
        self.loop.bind_agent(mock_agent)
        self.assertIs(self.loop._agent_ref, mock_agent)

    def test_trigger_retrain_async_deduplicates_inflight_job(self):
        import time

        def slow_retrain():
            time.sleep(0.1)
            return {"status": "ok"}

        with patch.object(self.loop, "retrain", side_effect=slow_retrain) as retrain:
            self.assertTrue(self.loop.trigger_retrain_async())
            self.assertFalse(self.loop.trigger_retrain_async())
            self.loop._retrain_future.result(timeout=2)
            self.assertEqual(retrain.call_count, 1)

    def test_insufficient_data(self):
        from trajectory_store import TrajectoryStore
        from auto_retrain import AutoRetrainLoop
        tiny_dir = tempfile.mkdtemp()
        tiny_db = os.path.join(tiny_dir, "tiny.db")
        tiny_store = TrajectoryStore(db_path=tiny_db)
        tiny_loop = AutoRetrainLoop(
            trajectory_db=tiny_db,
            registry_db=os.path.join(tiny_dir, "reg.db"),
            model_dir=tiny_dir,
            backend="sklearn",
            retrain_threshold=5,
        )
        result = tiny_loop.retrain()
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "insufficient_data")
        tiny_loop.close()
        tiny_store.close()
        import shutil
        shutil.rmtree(tiny_dir, ignore_errors=True)

    def test_retrain_twice_tracks_versions(self):
        self.loop.retrain()
        self.loop.retrain()
        self.assertGreaterEqual(self.loop._registry.count(), 1)

    def test_install_auto_retrain(self):
        from auto_retrain import install_auto_retrain
        mock_agent = MagicMock()
        mock_agent._trajectory_collector = None

        loop = install_auto_retrain(
            mock_agent,
            trajectory_db=os.path.join(self.store_dir, "synth.db"),
            registry_db=os.path.join(self.registry_dir, "reg.db"),
            model_dir=self.model_dir,
            backend="sklearn",
            retrain_threshold=20,
        )
        self.assertIsNotNone(loop)
        self.assertTrue(hasattr(mock_agent, "_auto_retrain"))
        loop.close()

    def test_install_with_existing_collector(self):
        from auto_retrain import install_auto_retrain
        import asyncio

        mock_agent = MagicMock()
        mock_collector = MagicMock()

        async def fake_collect(trace, source="openmegatron"):
            return "traj_id"

        mock_collector.collect = fake_collect
        mock_agent._trajectory_collector = mock_collector

        loop = install_auto_retrain(
            mock_agent,
            trajectory_db=os.path.join(self.store_dir, "synth.db"),
            registry_db=os.path.join(self.registry_dir, "reg.db"),
            model_dir=self.model_dir,
            retrain_threshold=20,
        )

        # Collector should be replaced with wrapped version
        self.assertIsNotNone(mock_agent._trajectory_collector)
        self.assertNotEqual(mock_agent._trajectory_collector.collect, fake_collect)
        loop.close()


if __name__ == "__main__":
    unittest.main()
