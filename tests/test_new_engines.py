"""Smoke tests for the new modular engine architecture.

Covers the 10 core modules that previously had zero test coverage:
model_tier, skill_router, api, services, predictive_engine,
guided_evolution, graph_engine, memory_ontology, cache_engine, repair_hook.
"""

import sys
import time
import unittest
from pathlib import Path

# Ensure pysrc is on the import path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pysrc"))


# ── model_tier ───────────────────────────────────────────────────────────────

class TestModelTier(unittest.TestCase):
    def setUp(self):
        from model_tier import ModelTier, TIER_COST, TIER_MODELS
        self.ModelTier = ModelTier
        self.TIER_COST = TIER_COST
        self.TIER_MODELS = TIER_MODELS

    def test_tier_enum_values(self):
        self.assertEqual(self.ModelTier.LITE, "lite")
        self.assertEqual(self.ModelTier.STANDARD, "standard")
        self.assertEqual(self.ModelTier.ADVANCED, "advanced")

    def test_tier_cost_mapping(self):
        self.assertIn(self.ModelTier.LITE, self.TIER_COST)
        self.assertIn(self.ModelTier.STANDARD, self.TIER_COST)
        self.assertIn(self.ModelTier.ADVANCED, self.TIER_COST)
        self.assertTrue(self.TIER_COST[self.ModelTier.LITE] < self.TIER_COST[self.ModelTier.ADVANCED])

    def test_tier_models_mapping(self):
        for tier in self.ModelTier:
            self.assertIn(tier, self.TIER_MODELS)
            self.assertIsInstance(self.TIER_MODELS[tier], list)


# ── skill_router ─────────────────────────────────────────────────────────────

class TestSkillRouter(unittest.TestCase):
    def setUp(self):
        from skill_router import RouteResult, SkillRouter
        self.RouteResult = RouteResult
        self.router = SkillRouter()

    def test_route_result_creation(self):
        rr = self.RouteResult(skill_name="test", confidence=0.9, tier="standard")
        self.assertEqual(rr.skill_name, "test")
        self.assertEqual(rr.confidence, 0.9)

    def test_match_returns_route_result(self):
        result = self.router.match("help me write code")
        self.assertIsNotNone(result)
        self.assertTrue(hasattr(result, "skill_name"))
        self.assertTrue(hasattr(result, "confidence"))
        self.assertGreaterEqual(result.confidence, 0.0)

    def test_match_empty_input(self):
        result = self.router.match("")
        self.assertIsNotNone(result)
        self.assertTrue(hasattr(result, "skill_name"))


# ── api ──────────────────────────────────────────────────────────────────────

class TestApiHelpers(unittest.TestCase):
    def test_error_response(self):
        from api import error_response
        resp = error_response("something went wrong")
        self.assertEqual(resp, {"error": "something went wrong", "status": 400})

    def test_error_response_custom_status(self):
        from api import error_response
        resp = error_response("not found", status=404)
        self.assertEqual(resp, {"error": "not found", "status": 404})


# ── services ─────────────────────────────────────────────────────────────────

class TestServices(unittest.TestCase):
    def test_service_registry_register_and_get(self):
        from services import ServiceRegistry
        registry = ServiceRegistry()

        class FakeService:
            pass

        registry.register(FakeService)
        svc = registry.get(FakeService)
        self.assertIsInstance(svc, FakeService)

    def test_service_registry_singleton(self):
        from services import ServiceRegistry
        a = ServiceRegistry()
        b = ServiceRegistry()
        self.assertIs(a, b)


# ── predictive_engine ────────────────────────────────────────────────────────

class TestPredictiveEngine(unittest.TestCase):
    def setUp(self):
        from predictive_engine import PredictiveEngine
        self.engine = PredictiveEngine()

    def test_empty_history_returns_empty(self):
        result = self.engine.predict({"task": "code"})
        self.assertEqual(result, [])

    def test_record_and_predict(self):
        self.engine.record({"task": "write a function"}, "code_assistant", "success")
        self.engine.record({"task": "review this code"}, "code_review", "success")
        result = self.engine.predict({"task": "write a function"})
        self.assertIsInstance(result, list)
        # With matching history, should return predictions
        self.assertGreater(len(result), 0)
        self.assertIn("action", result[0])

    def test_extract_features(self):
        features = self.engine._extract_features({
            "context": {"task": "code", "lang": "python"},
            "action": "assist",
            "outcome": "success",
        })
        self.assertIsInstance(features, set)
        self.assertIn("task:code", features)
        self.assertIn("action:assist", features)


# ── guided_evolution ─────────────────────────────────────────────────────────

class TestGuidedEvolution(unittest.TestCase):
    def setUp(self):
        from guided_evolution import GuidedEvolution
        self.evo = GuidedEvolution(max_generations=5)

    def test_max_generations_enforced(self):
        # With unreachable target, should stop at max_generations
        result = self.evo.evolve(
            seed="test",
            fitness_fn=lambda x: 0.1,  # never reaches 1.0
            target_fitness=1.0,
        )
        # When maxed out, evolve returns None (no optimal result found)
        self.assertLessEqual(self.evo.generation, self.evo.max_generations)

    def test_mutate_returns_string(self):
        result = self.evo._mutate("hello world")
        self.assertIsInstance(result, str)
        self.assertNotEqual(result, "hello world")  # mutation changes the string


# ── graph_engine ─────────────────────────────────────────────────────────────

class TestGraphEngine(unittest.TestCase):
    def setUp(self):
        from graph_engine import GraphEngine
        self.graph = GraphEngine()

    def test_add_and_get_node(self):
        self.graph.add_node("n1", name="Alice")
        node = self.graph.get_node("n1")
        self.assertIsNotNone(node)
        self.assertEqual(node["name"], "Alice")

    def test_add_edge_and_neighbors(self):
        self.graph.add_node("n1")
        self.graph.add_node("n2")
        self.graph.add_edge("n1", "n2", relation="knows")
        neighbors = self.graph.get_neighbors("n1")
        self.assertIn("n2", neighbors)

    def test_shortest_path(self):
        self.graph.add_node("a")
        self.graph.add_node("b")
        self.graph.add_node("c")
        self.graph.add_edge("a", "b", "to")
        self.graph.add_edge("b", "c", "to")
        path = self.graph.shortest_path("a", "c")
        self.assertEqual(path, ["a", "b", "c"])

    def test_shortest_path_no_path(self):
        self.graph.add_node("a")
        self.graph.add_node("b")
        path = self.graph.shortest_path("a", "b")
        self.assertIsNone(path)

    def test_entity_index(self):
        self.graph.add_node("n1", name="Alice")
        nid = self.graph.find_by_entity("Alice")
        self.assertEqual(nid, "n1")

    def test_bfs_from(self):
        for n in ["a", "b", "c", "d"]:
            self.graph.add_node(n)
        self.graph.add_edge("a", "b", "to")
        self.graph.add_edge("a", "c", "to")
        self.graph.add_edge("c", "d", "to")
        reachable = self.graph.bfs_from("a", max_depth=1)
        self.assertIn("a", reachable)
        self.assertIn("b", reachable)
        self.assertIn("c", reachable)
        self.assertNotIn("d", reachable)  # depth 2

    def test_node_edge_count(self):
        self.graph.add_node("a")
        self.graph.add_node("b")
        self.graph.add_edge("a", "b", "to")
        self.assertEqual(self.graph.node_count, 2)
        self.assertEqual(self.graph.edge_count, 1)


# ── memory_ontology ──────────────────────────────────────────────────────────

class TestMemoryOntology(unittest.TestCase):
    def setUp(self):
        from memory_ontology import Ontology, Entity, Relation
        self.Ontology = Ontology
        self.Entity = Entity
        self.Relation = Relation

    def test_entity_creation(self):
        e = self.Entity("test", "user", "hello", metadata={"key": "val"})
        self.assertEqual(e.id, "test")
        self.assertEqual(e.content, "hello")
        self.assertIsInstance(e.timestamp, float)

    def test_entity_to_dict_returns_float_timestamp(self):
        e = self.Entity("test", "user", "hello")
        d = e.to_dict()
        self.assertIsInstance(d["timestamp"], float)

    def test_entity_to_json_returns_iso_timestamp(self):
        e = self.Entity("test", "user", "hello")
        d = e.to_json()
        self.assertIsInstance(d["timestamp"], str)

    def test_relation_creation(self):
        r = self.Relation("src", "tgt", "references")
        self.assertEqual(r.source_id, "src")
        self.assertEqual(r.target_id, "tgt")
        self.assertEqual(r.relation_type, "references")

    def test_ontology_add_entity_and_relation(self):
        ont = self.Ontology()
        ont.add_entity("e1", "person", "Alice")
        ont.add_entity("e2", "person", "Bob")
        rel = ont.add_relation("e1", "e2", "knows")
        self.assertEqual(rel.relation_type, "knows")

    def test_find_relations_with_index(self):
        ont = self.Ontology()
        ont.add_entity("e1", "person", "Alice")
        ont.add_entity("e2", "person", "Bob")
        ont.add_entity("e3", "person", "Carol")
        ont.add_relation("e1", "e2", "knows")
        ont.add_relation("e1", "e3", "knows")
        ont.add_relation("e1", "e2", "works_with")

        # Indexed lookup
        knows = ont.find_relations("e1", relation_type="knows")
        self.assertEqual(len(knows), 2)

        # Unfiltered lookup
        all_rels = ont.find_relations("e1")
        self.assertEqual(len(all_rels), 3)


# ── cache_engine ─────────────────────────────────────────────────────────────

class TestCacheEngine(unittest.TestCase):
    def setUp(self):
        from cache_engine import CacheEngine
        self.cache = CacheEngine(max_size=10, default_ttl=3600)

    def test_set_and_get(self):
        self.cache.set("key1", "value1")
        self.assertEqual(self.cache.get("key1"), "value1")

    def test_miss_returns_none(self):
        self.assertIsNone(self.cache.get("nonexistent"))

    def test_delete(self):
        self.cache.set("key1", "value1")
        self.assertTrue(self.cache.delete("key1"))
        self.assertIsNone(self.cache.get("key1"))
        self.assertFalse(self.cache.delete("key1"))

    def test_clear(self):
        self.cache.set("a", 1)
        self.cache.set("b", 2)
        self.cache.clear()
        self.assertIsNone(self.cache.get("a"))
        self.assertIsNone(self.cache.get("b"))

    def test_get_or_set(self):
        val = self.cache.get_or_set("key", lambda: "computed")
        self.assertEqual(val, "computed")
        self.assertEqual(self.cache.get("key"), "computed")
        # Second call should return cached value
        val2 = self.cache.get_or_set("key", lambda: "should_not_compute")
        self.assertEqual(val2, "computed")

    def test_hits_misses_tracking(self):
        self.cache.set("a", 1)
        self.cache.get("a")
        self.cache.get("a")
        self.cache.get("missing")
        self.assertEqual(self.cache.hits, 2)
        self.assertEqual(self.cache.misses, 1)


# ── repair_hook ──────────────────────────────────────────────────────────────

class TestRepairHook(unittest.TestCase):
    def setUp(self):
        from repair_hook import RepairHook
        self.hook = RepairHook(max_retries=3)

    def test_success_on_first_try(self):
        call_count = [0]

        def maybe_fail():
            call_count[0] += 1
            return "ok"

        result = self.hook.execute(maybe_fail)
        self.assertEqual(result, "ok")
        self.assertEqual(call_count[0], 1)

    def test_retry_and_eventually_succeed(self):
        call_count = [0]

        def fail_twice():
            call_count[0] += 1
            if call_count[0] < 3:
                raise ValueError("transient error")
            return "recovered"

        result = self.hook.execute(fail_twice)
        self.assertEqual(result, "recovered")
        self.assertEqual(call_count[0], 3)

    def test_exhaust_retries(self):
        def always_fail():
            raise RuntimeError("persistent error")

        with self.assertRaises(RuntimeError):
            self.hook.execute(always_fail)

    def test_max_retries_respected(self):
        call_count = [0]
        hook = RepairHook(max_retries=2)

        def always_fail():
            call_count[0] += 1
            raise RuntimeError("fail")

        with self.assertRaises(RuntimeError):
            hook.execute(always_fail)
        self.assertEqual(call_count[0], 2)  # initial + 1 retry


# ── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main(verbosity=2)
