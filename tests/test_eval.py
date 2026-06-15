"""Tests for Phase 4: eval_ab, feedback_collector, learning_dashboard, regression_guard."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pysrc"))


def _make_store(num_samples=50):
    """Create a TrajectoryStore with synthetic data."""
    from trajectory_store import TrajectoryStore
    import random
    random.seed(42)
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "eval_test.db")
    store = TrajectoryStore(db_path=db_path)
    for i in range(num_samples):
        tc = random.randint(1, 6)
        dms = random.uniform(500, 30000)
        has_err = random.random() < 0.25
        ec = random.randint(1, 2) if has_err else 0
        success = not has_err
        tool_calls = []
        for j in range(tc):
            is_err = j < ec
            tool_calls.append({"tool": f"t{j}", "args": "{}", "output_preview": "err" if is_err else "ok",
                               "duration_ms": random.uniform(100, 3000), "status": "error" if is_err else "success"})
        store.store({"session_id": f"s{i}", "user_input": f"Req {i}" + "x" * random.randint(5, 50),
                     "selected_skills": ["code"], "tool_calls": tool_calls, "reward": 0.8 if success else 0.3,
                     "confidence": 0.7, "success": success, "tool_count": tc, "duration_ms": dms,
                     "final_answer": "Answer here", "source": "openmegatron",
                     "created_at": f"2025-06-{i%28+1:02d}T12:00:00Z",
                     "metadata": {"reward_dimensions": {"success": success, "stability": 0.9, "speed": 0.8, "efficiency": 0.7}}})
    return store, tmpdir


def _cleanup(store, tmpdir):
    import shutil
    store.close()
    shutil.rmtree(tmpdir, ignore_errors=True)


def _train_model(store, model_dir):
    from reward_model import create_scorer
    scorer = create_scorer("sklearn")
    scorer.train(store)
    path = os.path.join(model_dir, "test_model.pkl")
    scorer.save(path)
    return path


# ── TestFeedbackCollector ─────────────────────────────────────────────────────

class TestFeedbackCollector(unittest.TestCase):
    def setUp(self):
        from feedback_collector import FeedbackCollector
        self.fb = FeedbackCollector()

    def test_implicit_correction(self):
        trace = {"user_goal": "不对，我要的是Python代码不是JS", "final_answer": "Here is JS code", "success": True,
                 "tool_calls": []}
        fb = self.fb.collect_from_trace(trace)
        self.assertIsNotNone(fb)
        self.assertEqual(fb["label"], 0)
        self.assertGreaterEqual(fb["confidence"], 0.7)
        self.assertEqual(fb["source"], "implicit")

    def test_implicit_retry(self):
        trace = {"user_goal": "换个方式重新生成", "final_answer": "Some answer", "success": False,
                 "tool_calls": []}
        fb = self.fb.collect_from_trace(trace)
        self.assertIsNotNone(fb)
        self.assertEqual(fb["label"], 0)

    def test_implicit_thanks(self):
        trace = {"user_goal": "谢谢，很好", "final_answer": "不客气！", "success": True,
                 "tool_calls": [{"tool": "run", "args": "{}", "output_preview": "ok", "duration_ms": 100, "status": "success"}]}
        fb = self.fb.collect_from_trace(trace)
        self.assertIsNotNone(fb)
        self.assertEqual(fb["label"], 1)
        self.assertGreaterEqual(fb["confidence"], 0.7)

    def test_short_answer_signal(self):
        trace = {"user_goal": "test", "final_answer": "ok", "success": True, "tool_calls": []}
        fb = self.fb.collect_from_trace(trace)
        self.assertIsNotNone(fb)
        self.assertEqual(fb["signal"], "short_answer")
        self.assertEqual(fb["label"], 0)

    def test_no_signal(self):
        trace = {"user_goal": "normal request", "final_answer": "A reasonable response to the query",
                 "success": True, "tool_calls": [{"tool": "run", "args": "{}", "output_preview": "ok", "duration_ms": 500, "status": "success"}]}
        fb = self.fb.collect_from_trace(trace)
        self.assertIsNone(fb)

    def test_explicit_rating(self):
        from feedback_collector import FeedbackCollector
        store, tmpdir = _make_store(20)
        fb2 = FeedbackCollector(store)
        tid = fb2.collect_explicit("s0", rating=5, comment="Great!")
        self.assertIsNotNone(tid)
        _cleanup(store, tmpdir)

    def test_get_labeled_samples(self):
        from feedback_collector import FeedbackCollector
        store, tmpdir = _make_store(20)
        fb2 = FeedbackCollector(store)
        fb2.collect_explicit("s0", rating=4)
        fb2.collect_explicit("s1", rating=1)
        samples = fb2.get_labeled_samples(min_confidence=0.7)
        self.assertGreaterEqual(len(samples), 1)
        _cleanup(store, tmpdir)

    def test_labeled_stats(self):
        from feedback_collector import FeedbackCollector
        store, tmpdir = _make_store(15)
        fb2 = FeedbackCollector(store)
        fb2.collect_explicit("s0", rating=5)
        fb2.collect_explicit("s1", rating=1)
        stats = fb2.labeled_stats()
        self.assertGreaterEqual(stats["total_labeled"], 1)
        self.assertIn("positive", stats)
        _cleanup(store, tmpdir)


# ── TestABComparison ──────────────────────────────────────────────────────────

class TestABComparison(unittest.TestCase):
    def setUp(self):
        self.store, self.store_dir = _make_store(60)
        self.model_dir = tempfile.mkdtemp()
        self.model_path = _train_model(self.store, self.model_dir)

    def tearDown(self):
        _cleanup(self.store, self.store_dir)
        import shutil
        shutil.rmtree(self.model_dir, ignore_errors=True)

    def test_run(self):
        from eval_ab import ABComparison
        ab = ABComparison()
        metrics = ab.run(self.store, self.model_path)
        self.assertIn("n_samples", metrics)
        self.assertGreater(metrics["n_samples"], 0)
        self.assertIn("model_f1", metrics)
        self.assertIn("rule_f1", metrics)
        self.assertIn("winner", metrics)
        self.assertIn("agreement_rate", metrics)

    def test_win_rate(self):
        from eval_ab import ABComparison
        ab = ABComparison()
        metrics = ab.run(self.store, self.model_path)
        wr = metrics["win_rate"]
        self.assertIn("model_wins", wr)
        self.assertIn("rule_wins", wr)
        self.assertIn("ties", wr)
        self.assertEqual(wr["model_wins"] + wr["rule_wins"] + wr["ties"], metrics["n_samples"])

    def test_calibration(self):
        from eval_ab import ABComparison
        ab = ABComparison()
        metrics = ab.run(self.store, self.model_path)
        self.assertIn("calibration", metrics)
        self.assertIn("ece", metrics["calibration"])
        self.assertIn("bins", metrics["calibration"])

    def test_statistical_significance(self):
        from eval_ab import ABComparison
        ab = ABComparison()
        metrics = ab.run(self.store, self.model_path)
        self.assertIn("statistical_significance", metrics)
        self.assertIn("mcnemar_p", metrics["statistical_significance"])

    def test_empty_store(self):
        from trajectory_store import TrajectoryStore
        from eval_ab import ABComparison
        empty_store = TrajectoryStore(db_path=os.path.join(self.store_dir, "empty.db"))
        ab = ABComparison()
        metrics = ab.run(empty_store, self.model_path)
        self.assertIn("error", metrics)
        empty_store.close()

    def test_markdown_output(self):
        from eval_ab import ABComparison
        ab = ABComparison()
        metrics = ab.run(self.store, self.model_path)
        md = ab.to_markdown(metrics)
        self.assertIn("A/B Comparison", md)
        self.assertIn("Model", md)
        self.assertIn("Rule", md)


# ── TestLearningDashboard ─────────────────────────────────────────────────────

class TestLearningDashboard(unittest.TestCase):
    def setUp(self):
        from learning_dashboard import LearningDashboard
        self.store, self.store_dir = _make_store(60)
        self.model_dir = tempfile.mkdtemp()
        self.reg_dir = tempfile.mkdtemp()
        self.model_path = _train_model(self.store, self.model_dir)
        self.dash = LearningDashboard(registry_db=os.path.join(self.reg_dir, "dash.db"))

    def tearDown(self):
        self.dash.close()
        _cleanup(self.store, self.store_dir)
        import shutil
        shutil.rmtree(self.model_dir, ignore_errors=True)
        shutil.rmtree(self.reg_dir, ignore_errors=True)

    def test_record_checkpoint(self):
        result = self.dash.record_checkpoint(self.store, self.model_path, model_id="m1")
        self.assertIn("f1", result)
        self.assertIn("accuracy", result)
        self.assertGreater(result["n_samples"], 0)

    def test_learning_curve(self):
        self.dash.record_checkpoint(self.store, self.model_path, model_id="m1")
        curve = self.dash.get_learning_curve()
        self.assertGreater(len(curve["data_points"]), 0)
        self.assertIn("metrics", curve)
        self.assertIn("f1_current", curve["metrics"])

    def test_compare_models(self):
        self.dash.record_checkpoint(self.store, self.model_path, model_id="m1")
        # Train a second model
        model2 = _train_model(self.store, self.model_dir)
        self.dash.record_checkpoint(self.store, model2, model_id="m2")
        result = self.dash.compare_models()
        self.assertIn("models", result)
        self.assertGreaterEqual(len(result["models"]), 2)
        self.assertIn("best_model", result)

    def test_estimate_milestone(self):
        self.dash.record_checkpoint(self.store, self.model_path, model_id="m1")
        model2 = _train_model(self.store, self.model_dir)
        self.dash.record_checkpoint(self.store, model2, model_id="m2")
        est = self.dash.estimate_next_milestone(target_f1=0.95)
        self.assertIn("target_f1", est)
        self.assertIn("estimated_additional_samples", est)

    def test_text_output(self):
        self.dash.record_checkpoint(self.store, self.model_path, model_id="m1")
        text = self.dash.to_text()
        self.assertIn("LEARNING DASHBOARD", text)
        self.assertIn("F1:", text)


# ── TestRegressionGuard ───────────────────────────────────────────────────────

class TestRegressionGuard(unittest.TestCase):
    def setUp(self):
        from regression_guard import RegressionGuard
        self.store, self.store_dir = _make_store(60)
        self.model_dir = tempfile.mkdtemp()
        self.guard = RegressionGuard(f1_tolerance=0.05)
        # Train two models — second may be slightly different
        self.model1 = _train_model(self.store, self.model_dir)
        self.model2 = _train_model(self.store, self.model_dir)

    def tearDown(self):
        _cleanup(self.store, self.store_dir)
        import shutil
        shutil.rmtree(self.model_dir, ignore_errors=True)

    def test_validate_passes_same_model(self):
        result = self.guard.validate(self.model1, self.model1, self.store)
        self.assertIn("passed", result)
        self.assertIn("checks", result)
        self.assertEqual(len(result["checks"]), 3)
        # F1 gate should pass (same model)
        f1_check = [c for c in result["checks"] if c["name"] == "f1_gate"][0]
        self.assertTrue(f1_check["passed"])

    def test_edge_cases_pass(self):
        result = self.guard.validate(self.model1, self.model1, self.store)
        edge_check = [c for c in result["checks"] if c["name"] == "edge_cases"][0]
        self.assertTrue(edge_check["passed"], f"Edge case failures: {edge_check.get('failures', [])}")

    def test_nonexistent_model(self):
        result = self.guard.validate("/nonexistent/model.pkl", self.model1, self.store)
        self.assertFalse(result["passed"])

    def test_all_checks_present(self):
        result = self.guard.validate(self.model1, self.model1, self.store)
        names = {c["name"] for c in result["checks"]}
        self.assertIn("f1_gate", names)
        self.assertIn("holdout_test", names)
        self.assertIn("edge_cases", names)

    def test_summary(self):
        result = self.guard.validate(self.model1, self.model1, self.store)
        self.assertIn("summary", result)
        self.assertIsInstance(result["summary"], str)
        self.assertGreater(len(result["summary"]), 0)


if __name__ == "__main__":
    unittest.main()
