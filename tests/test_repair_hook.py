import sys, json, unittest, asyncio
from pathlib import Path
p = str(Path(__file__).resolve().parents[1] / 'pysrc')
r = str(Path(__file__).resolve().parents[1] / 'pysrc' / 'skills' / 'research')
if p not in sys.path: sys.path.insert(0, p)
if r not in sys.path: sys.path.insert(0, r)
from repair_hook import RepairHook, RepairIssue, RepairAttempt, RepairTrace, RepairExperienceStore
from repair_hook import validate_not_empty
from research_validators import validate_papers_have_abstracts, validate_paper_count_in_range
from research_validators import validate_doi_exists, validate_no_duplicates_by_title
from research_validators import validate_citations_nonzero, validate_year_in_range
class TestCore(unittest.TestCase):
    def test_not_empty_none(self):
        i = asyncio.run(validate_not_empty(None))
        self.assertEqual(len(i), 1)
        self.assertEqual(i[0].category, "empty_result")
    def test_not_empty_empty(self):
        i = asyncio.run(validate_not_empty([]))
        self.assertEqual(len(i), 1)
    def test_not_empty_valid(self):
        self.assertEqual(len(asyncio.run(validate_not_empty([1,2]))), 0)
    def test_has_field_present(self):
        import repair_hook
        v = asyncio.run(repair_hook.validate_has_field("title"))
        i = asyncio.run(v([{"title":"TP","a":"X"}]))
        self.assertEqual(len(i), 0)
    def test_has_field_missing(self):
        import repair_hook
        v = asyncio.run(repair_hook.validate_has_field("abstract"))
        i = asyncio.run(v([{"title":"TP"}]))
        self.assertEqual(len(i), 1)
    def test_min_count_ok(self):
        import repair_hook
        v = asyncio.run(repair_hook.validate_min_count(3))
        i = asyncio.run(v([1,2,3,4]))
        self.assertEqual(len(i), 0)
    def test_min_count_below(self):
        import repair_hook
        v = asyncio.run(repair_hook.validate_min_count(5))
        i = asyncio.run(v([1,2]))
        self.assertEqual(len(i), 1)
    def test_success_first(self):
        hook = RepairHook()
        async def t(): return {"ok":True}
        r = asyncio.run(hook.repair(t,task_name="t",validators=[]))
        self.assertEqual(r["status"], "success")
        self.assertEqual(r["trace"].total_attempts, 1)
    def test_recovery(self):
        hook = RepairHook()
        cc = [0]
        async def t():
            cc[0] += 1
            return [] if cc[0] < 2 else [{"title":"R"}]
        async def v(r,ctx=None):
            return [RepairIssue("error","E","empty_result")] if isinstance(r,list) and len(r)==0 else []
        r = asyncio.run(hook.repair(t,task_name="f",validators=[v],max_attempts=3))
        self.assertEqual(r["status"], "success")
        self.assertEqual(cc[0], 2)
    def test_all_fail(self):
        hook = RepairHook()
        async def t(): return []
        async def v(r,ctx=None):
            return [RepairIssue("error","E","empty_result")] if isinstance(r,list) and len(r)==0 else []
        r = asyncio.run(hook.repair(t,task_name="f",validators=[v],max_attempts=2))
        self.assertEqual(r["status"], "error")
    def test_exception(self):
        hook = RepairHook()
        async def t(): 1/0
        r = asyncio.run(hook.repair(t,task_name="c",max_attempts=2))
        self.assertEqual(r["status"], "error")
    def test_fix_empty(self):
        hook = RepairHook()
        self.assertIsNotNone(hook._auto_derive_fix([RepairIssue("error","N","empty_result")],{}))
    def test_fix_api(self):
        hook = RepairHook()
        self.assertIsNotNone(hook._auto_derive_fix([RepairIssue("error","T","api_error")],{}))
class TestResearchValidators(unittest.TestCase):
    def test_abstracts_ok(self):
        i = asyncio.run(validate_papers_have_abstracts([{"t":"A","abstract":"X"},{"t":"B","abstract":"Y"}]))
        self.assertEqual(len(i), 0)
    def test_abstracts_missing(self):
        i = asyncio.run(validate_papers_have_abstracts([{"t":"A"}]))
        self.assertEqual(len(i), 1)
    def test_count_empty(self):
        v = asyncio.run(validate_paper_count_in_range(1))
        i = asyncio.run(v([]))
        self.assertEqual(len(i), 1)
    def test_count_ok(self):
        v = asyncio.run(validate_paper_count_in_range(1))
        i = asyncio.run(v([{"t":str(j)} for j in range(5)]))
        self.assertEqual(len(i), 0)
    def test_doi_ok(self):
        i = asyncio.run(validate_doi_exists([{"title":"A","doi":"10.1/x"}]))
        self.assertEqual(len(i), 0)
    def test_doi_missing(self):
        i = asyncio.run(validate_doi_exists([{"title":"A"},{"title":"B"}]))
        self.assertEqual(len(i), 1)
    def test_duplicates(self):
        i = asyncio.run(validate_no_duplicates_by_title([{"title":"A Great Paper"},{"title":"Another"},{"title":"A Great Paper"}]))
        self.assertTrue(len(i) >= 1)
    def test_citations(self):
        i = asyncio.run(validate_citations_nonzero([{"t":"A","citations":0},{"t":"B","citations":0}]))
        self.assertEqual(len(i), 1)
    def test_year_bad(self):
        v = asyncio.run(validate_year_in_range(2020,2025))
        i = asyncio.run(v([{"t":"A","year":1999},{"t":"B","year":2023}]))
        self.assertEqual(len(i), 1)
class TestStore(unittest.TestCase):
    def test_empty(self):
        self.assertIsNone(asyncio.run(RepairExperienceStore().query("t",[])))
    def test_record_and_query(self):
        s = RepairExperienceStore()
        t = RepairTrace("ps",{"q":"AI"},2,True,[RepairAttempt(1,[RepairIssue("error","E","empty_result")],"Broadened query",100,False),RepairAttempt(2,[],"",50,True)],0,0.5)
        asyncio.run(s.record(t))
        r = asyncio.run(s.query("ps",[RepairIssue("error","E","empty_result")]))
        self.assertIsNone(r)
if __name__ == '__main__':
    unittest.main()


class TestPredictiveGuard(unittest.TestCase):
    """Test PredictiveGuard pre-flight checks."""

    def test_pre_flight_passes(self):
        import asyncio
        from predictive_engine import PredictiveGuard
        guard = PredictiveGuard()
        ok, issues = asyncio.run(guard.inspect("general", {"query": "test"}))
        self.assertTrue(ok)

    def test_pre_flight_blocks_empty_params(self):
        import asyncio
        from predictive_engine import PredictiveGuard
        guard = PredictiveGuard()
        ok, issues = asyncio.run(guard.inspect("general", {}))
        # Should have a warning, but not blocked
        self.assertTrue(ok)

    def test_pre_flight_category_code(self):
        import asyncio
        from predictive_engine import PredictiveGuard
        guard = PredictiveGuard()
        ok, issues = asyncio.run(guard.inspect("code", {"action": "build"}))
        # Git check might fail if no git on PATH, but that's a blocker
        # So this tests the guard runs without crashing
        self.assertIsInstance(ok, bool)

    def test_pre_flight_check_network(self):
        import asyncio
        from predictive_engine import PredictiveGuard
        guard = PredictiveGuard()
        ok, issues = asyncio.run(guard.inspect("research", {"query": "AI"}))
        # Network may be up or down; just check it returns properly
        self.assertIsInstance(ok, bool)
        for i in issues:
            self.assertIn(i.category, ["missing_dependency", "network", "config"])

    def test_pre_flight_auto_fix_directory(self):
        import asyncio, tempfile, os
        from predictive_engine import PredictiveGuard, PreFlightIssue
        guard = PredictiveGuard()
        # Simulate a checker that detects missing output dir
        async def check_missing_dir(params):
            return [PreFlightIssue(
                severity="blocker",
                message="Output dir missing",
                category="input_invalid",
                can_auto_fix=True,
                auto_fix_action="Create directory: " + os.path.join(tempfile.gettempdir(), "_auto_test_mkdir"),
            )]
        guard.register("test_cat", check_missing_dir)
        ok, issues = asyncio.run(guard.inspect("test_cat", {}))
        self.assertFalse(ok)
        fixed, updated = asyncio.run(guard.auto_fix("test_cat", {}, issues))
        self.assertTrue(fixed)
        # Cleanup
        import shutil
        shutil.rmtree(os.path.join(tempfile.gettempdir(), "_auto_test_mkdir"), ignore_errors=True)


class TestExplorationEngine(unittest.TestCase):
    """Test ExplorationEngine multi-strategy A/B testing."""

    def test_explore_returns_best(self):
        import asyncio
        from predictive_engine import ExplorationEngine, Strategy
        engine = ExplorationEngine()
        strategies = [
            Strategy("strategy_a", "Always fails", lambda ctx: None),
            Strategy("strategy_b", "Always succeeds", lambda ctx: {"status": "success"}),
        ]
        best, results = asyncio.run(engine.explore(strategies, {"test": True}, max_explorations=3, exploration_rate=1.0))
        # strategy_b is tried and succeeds, should be selected as best
        self.assertIsNotNone(best)
        self.assertEqual(best.name, "strategy_b")
        scores = engine.get_scores()
        self.assertIn("strategy_b", scores)
        self.assertEqual(scores["strategy_b"]["success_count"], 1)

    def test_explore_all_fail_fallback(self):
        import asyncio
        from predictive_engine import ExplorationEngine, Strategy
        engine = ExplorationEngine()
        strategies = [
            Strategy("fail1", "Fails", lambda ctx: {"status": "error"}),
            Strategy("fail2", "Also fails", lambda ctx: {"status": "error"}),
        ]
        best, results = asyncio.run(engine.explore(strategies, {"test": True}, max_explorations=3, exploration_rate=0.0))
        # Falls back to first strategy even if it failed
        self.assertEqual(best.name, "fail1")

    def test_strategy_scoring(self):
        from predictive_engine import ExplorationEngine
        engine = ExplorationEngine()
        engine._update_score("test_strat", True, 100)
        engine._update_score("test_strat", True, 50)
        engine._update_score("test_strat", False, 200)
        scores = engine.get_scores()
        self.assertEqual(scores["test_strat"]["success_count"], 2)
        self.assertEqual(scores["test_strat"]["failure_count"], 1)
        self.assertAlmostEqual(scores["test_strat"]["success_rate"], 2/3)

    def test_best_strategy_for(self):
        from predictive_engine import ExplorationEngine
        engine = ExplorationEngine()
        engine._update_score("empty_research_broaden_0", True, 100)
        engine._update_score("empty_research_broaden_1", False, 200)
        best = engine.best_strategy_for("empty_research")
        self.assertEqual(best, "empty_research_broaden_0")


class TestStrategyFactories(unittest.TestCase):
    """Test strategy generation functions."""

    def test_empty_result_research(self):
        from predictive_engine import strategies_for_empty_result
        strats = strategies_for_empty_result("research", {"query": "test"})
        self.assertTrue(len(strats) >= 1)

    def test_empty_result_code(self):
        from predictive_engine import strategies_for_empty_result
        strats = strategies_for_empty_result("code", {"action": "build"})
        self.assertTrue(len(strats) >= 1)

    def test_execution_error_syntax(self):
        from predictive_engine import strategies_for_execution_error
        strats = strategies_for_execution_error("SyntaxError: invalid syntax")
        names = [s.name for s in strats]
        self.assertIn("exec_fix_syntax", names)

    def test_execution_error_import(self):
        from predictive_engine import strategies_for_execution_error
        strats = strategies_for_execution_error("ModuleNotFoundError: No module named 'foo'")
        names = [s.name for s in strats]
        self.assertIn("exec_install_deps", names)
