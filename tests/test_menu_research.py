"""starbat-like menu test for the complete research pipeline.

Simulates: menu option 1 -> research skill -> pipeline -> validators -> citation graph

This tests the full stack:
  1. PredictiveGuard pre-flight checks
  2. RepairHook validate + retry
  3. ExplorationEngine multi-strategy A/B
  4. CrossCategoryLearner records failures
  5. LiteratureGraph generates citation graph
  6. GuidedEvolution tracks maturity level
"""

import sys, json, asyncio, time, unittest
from pathlib import Path

p = str(Path(__file__).resolve().parents[1] / "pysrc")
r = str(Path(__file__).resolve().parents[1] / "pysrc" / "skills" / "research")
if p not in sys.path:
    sys.path.insert(0, p)
if r not in sys.path:
    sys.path.insert(0, r)

from repair_hook import RepairHook, RepairIssue
from predictive_engine import PredictiveGuard, ExplorationEngine, PreFlightIssue
from cross_category_learner import CrossCategoryLearner
from validator_orchestrator import ValidatorOrchestrator
from literature_graph import LiteratureGraph
from guided_evolution import GuidedEvolution
from skills.unified_validators import unified_validators


# ���� Simulated research pipeline (standalone, no external API) ��������������������

class SimResearchPipeline:
    """Simulates the research pipeline end-to-end."""

    def __init__(self, fail_mode: str = None, query: str = "transformer"):
        self.fail_mode = fail_mode  # None, "empty", "quality", "exception"
        self.query = query
        self.call_count = 0

    async def search(self, query: str = None) -> list:
        """Simulate paper search."""
        self.call_count += 1
        q = query or self.query

        if self.fail_mode == "empty":
            return []

        if self.fail_mode == "exception":
            raise RuntimeError("Simulated API failure")

        if self.fail_mode == "quality":
            # Return papers with missing abstracts/DOIs
            return [
                {"title": "Paper A about " + q, "year": 2021, "venue": "NeurIPS",
                 "citations": 0, "doi": "", "authors": "Author A", "abstract": ""},
                {"title": "Paper B about " + q, "year": 2022, "venue": "ICML",
                 "citations": 0, "doi": "", "authors": "Author B", "abstract": ""},
            ]

        # Success case
        return [
            {"title": "Attention Is All You Need", "year": 2017, "venue": "NeurIPS",
             "citations": 50000, "doi": "10.1/attention", "authors": "Vaswani et al.",
             "abstract": "The dominant sequence transduction models are based on complex recurrent or convolutional neural networks."},
            {"title": "BERT: Pre-training of Deep Bidirectional Transformers", "year": 2018, "venue": "NAACL",
             "citations": 30000, "doi": "10.1/bert", "authors": "Devlin et al.",
             "abstract": "We introduce a new language representation model called BERT."},
            {"title": "Language Models are Few-Shot Learners", "year": 2020, "venue": "NeurIPS",
             "citations": 20000, "doi": "10.1/gpt3", "authors": "Brown et al.",
             "abstract": "Recent work has demonstrated substantial gains on many NLP tasks."},
            {"title": "An Image is Worth 16x16 Words", "year": 2021, "venue": "ICLR",
             "citations": 15000, "doi": "10.1/vit", "authors": "Dosovitskiy et al.",
             "abstract": "While the Transformer architecture has become the de-facto standard for natural language processing."},
        ]


# ���� Tests ����������������������������������������������������������������������������������������������������������������������������

class TestResearchMenuSimulation(unittest.TestCase):
    """Simulates selecting option 1 from a starbat menu and running research."""

    def setUp(self):
        self.hook = RepairHook()
        self.guard = PredictiveGuard()
        self.explorer = ExplorationEngine()
        self.learner = CrossCategoryLearner()
        self.orchestrator = ValidatorOrchestrator()
        self.evolution = GuidedEvolution()

    def _make_search_task(self, pipeline: SimResearchPipeline):
        """Create a task callable for RepairHook."""
        async def task():
            result = await pipeline.search()
            return result
        return task

    # ���� Option 1: Happy path (success) ����

    def test_menu_option_1_happy_path(self):
        """Simulate selecting option 1 (research) -> pipeline returns successfully."""
        async def run():
            pipeline = SimResearchPipeline(fail_mode=None, query="transformer attention")
            validators = unified_validators("research")

            # 1. Pre-flight
            can_proceed, pre_issues = await self.guard.inspect("research", {"query": "transformer attention"})
            self.assertTrue(can_proceed, f"Pre-flight blocked: {[i.message for i in pre_issues]}")

            # 2. Execute with RepairHook
            result = await self.hook.repair(
                self._make_search_task(pipeline),
                task_name="research_pipeline",
                context={"skill_category": "research", "query": "transformer attention"},
                validators=validators,
            )
            self.assertEqual(result["status"], "success",
                             f"Pipeline failed: {result.get('trace')}")

            papers = result["result"]
            self.assertTrue(len(papers) >= 3, f"Expected >=3 papers, got {len(papers)}")

            # 3. LiteratureGraph from results
            graph = LiteratureGraph.from_papers(papers)
            md = graph.to_markdown()
            self.assertIn("Citation Graph", md)
            self.assertIn("Top Papers by Citation", md)
            self.assertIn("Attention Is All You Need", md)

            # 4. Record in CrossCategoryLearner
            if result.get("trace"):
                await self.learner.record_failure("research", result["trace"])

            # 5. Update evolution
            self.evolution.report("research", success=True)
            state = self.evolution.get_state("research")
            self.assertEqual(state.total_executions, 1)
            self.assertEqual(state.consecutive_successes, 1)

            return True

        self.assertTrue(asyncio.run(run()))

    # ���� Option 1: Pipeline recovers from empty result ����

    def test_menu_option_1_recovers_from_empty(self):
        """Simulate option 1 where first call returns empty, repair retries and succeeds."""
        class RecoveringPipeline:
            def __init__(self):
                self.call_count = 0
            async def search(self, query: str = None):
                self.call_count += 1
                if self.call_count < 2:
                    return []
                return [
                    {"title": "Recovered Paper", "year": 2023, "venue": "NeurIPS",
                     "citations": 10, "doi": "10.1/r", "authors": "Author R",
                     "abstract": "This paper was recovered after retry."},
                ]

        async def run():
            pipeline = RecoveringPipeline()
            validators = unified_validators("research")

            result = await self.hook.repair(
                self._make_search_task(pipeline),
                task_name="research_recover",
                context={"skill_category": "research"},
                validators=validators,
                max_attempts=3,
            )
            self.assertEqual(result["status"], "success")
            self.assertEqual(pipeline.call_count, 2)

            # Citation graph
            papers = result["result"]
            graph = LiteratureGraph.from_papers(papers)
            self.assertIn("Recovered Paper", graph.to_markdown())

            return True

        self.assertTrue(asyncio.run(run()))

    # ���� Evolution: reactive -> predictive promotion ����

    def test_evolution_promotion(self):
        """Simulate enough successful executions to promote from reactive to predictive."""
        evo = GuidedEvolution()
        state = evo.get_state("research")
        self.assertEqual(state.level.name, "REACTIVE")

        # Simulate many successful runs
        for _ in range(12):
            evo.report("research", success=True)

        state = evo.get_state("research")
        # Should still be REACTIVE because promotion happens during execute()
        self.assertEqual(state.level.name, "REACTIVE")
        self.assertEqual(state.consecutive_successes, 12)

    # ���� Validator orchestrator capability report ����

    def test_validator_orchestrator_capabilities(self):
        """Verify the orchestrator knows which validators apply to research."""
        report = self.orchestrator.validate_sync_report("research")
        self.assertIn("research", report)
        self.assertIn("not_empty", report)
        self.assertIn("abstracts_present", report)

        report_code = self.orchestrator.validate_sync_report("code")
        self.assertIn("code", report_code)
        self.assertNotIn("abstracts_present", report_code)

    # ���� Cross-category learner records and aggregates ����

    def test_cross_category_learner(self):
        """Record failures across categories and discover patterns."""
        async def run():
            # Record failures in research
            for i in range(5):
                trace = self._make_fake_trace("research", "empty_result")
                await self.learner.record_failure("research", trace)

            # Record failures in media
            for i in range(4):
                trace = self._make_fake_trace("media", "execution_error")
                await self.learner.record_failure("media", trace)

            # Aggregate
            patterns = await self.learner.aggregate(force=True)
            self.assertTrue(len(patterns) >= 1, f"Expected patterns, got {len(patterns)}")

            # Suggest pre-checks for a cousin category
            suggestions = self.learner.suggest_pre_checks("code")
            # May have suggestions from research empty_result pattern
            self.assertIsInstance(suggestions, list)

            return True

        self.assertTrue(asyncio.run(run()))

    def _make_fake_trace(self, category: str, issue_cat: str):
        """Create a fake RepairTrace for testing."""
        from repair_hook import RepairTrace, RepairAttempt
        return RepairTrace(
            task_name=f"test_{category}",
            context_snapshot={"skill_category": category},
            total_attempts=1,
            final_success=False,
            attempts=[
                RepairAttempt(
                    attempt=1,
                    issues=[RepairIssue(
                        severity="error",
                        message=f"Simulated {issue_cat} in {category}",
                        category=issue_cat,
                    )],
                    fix_applied="broaden query",
                    duration_ms=100.0,
                    success=False,
                )
            ],
            started_at=time.time() - 1.0,
            ended_at=time.time(),
        )

    # ���� Guided evolution: execute with different modes ����

    def test_guided_evolution_execute_reactive(self):
        """Test execution in reactive mode."""
        async def run():
            evo = GuidedEvolution()
            # Start in reactive mode
            call_count = [0]

            async def simple_task():
                call_count[0] += 1
                return {"status": "ok", "papers": [{"title": "Test", "doi": "10.1/t"}]}

            result = await evo.execute(
                "research",
                simple_task,
                task_name="test_evo",
                parameters={"query": "test"},
                validators=unified_validators("research"),
            )
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["evolution_level"], "reactive")

            return True

        self.assertTrue(asyncio.run(run()))

    # ���� LiteratureGraph from papers with references ����

    def test_literature_graph_with_references(self):
        """Build citation graph with papers that have references."""
        papers = [
            {
                "title": "Main Paper", "year": 2023, "venue": "NeurIPS",
                "citations": 100, "doi": "10.1/main",
                "authors": "Author M",
                "references": [
                    {"title": "Ref A", "year": 2020, "doi": "10.1/ref_a"},
                    {"title": "Ref B", "year": 2019, "doi": "10.1/ref_b"},
                ],
            },
        ]
        graph = LiteratureGraph.from_papers(papers)
        self.assertEqual(len(graph.nodes), 3)  # main + ref_a + ref_b
        self.assertEqual(len(graph.edges), 2)  # main->ref_a, main->ref_b
        md = graph.to_markdown()
        self.assertIn("Ref A", md)
        self.assertIn("Ref B", md)

    # ���� Full pipeline: predict -> validate -> explore ����

    def test_pipeline_with_exploration_on_failure(self):
        """Pre-flight -> fail -> explore -> recover."""
        async def run():
            evo = GuidedEvolution()
            call_log = []

            async def flaky_task():
                call_log.append(len(call_log) + 1)
                if len(call_log) <= 1:
                    return []
                return [
                    {"title": "Success Paper", "year": 2023, "venue": "ICLR",
                     "citations": 50, "doi": "10.1/s", "authors": "Author S",
                     "abstract": "This is the successful paper."},
                ]

            # Force exploration level
            state = evo.get_state("research")
            state.level = type(state.level)(3)  # EXPLORATION
            state.exploration_enabled = True

            result = await evo.execute(
                "research",
                flaky_task,
                task_name="flaky_research",
                parameters={"query": "flaky test"},
                validators=unified_validators("research"),
                max_attempts=4,
            )
            # Should eventually succeed (flaky succeeds on 3rd call)
            self.assertEqual(result["status"], "success", f"Failed: {result}")
            self.assertEqual(len(call_log), 2)  # failed once, recovered once

            return True

        self.assertTrue(asyncio.run(run()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
