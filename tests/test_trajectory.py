"""Tests for trajectory_store, trajectory_collector, and external_agent_parser.

Covers Phase 1 of the companion model data pipeline:
  - TrajectoryStore: SQLite CRUD, query filters, stats, JSONL export
  - TrajectoryCollector: integration hook, graceful degradation
  - ExternalAgentParser: JSONL transcript parsing, trajectory conversion
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

def _sample_trace(**overrides):
    """Build a minimal task_trace dict for testing."""
    trace = {
        "session_id": "test-session-001",
        "user_goal": "帮我写一段Python代码",
        "routing_goal": "write python code",
        "selected_skills": ["code-assistant-2.0.0"],
        "tool_calls": [
            {
                "tool": "run_skill_script",
                "arguments": '{"skill_name":"code-assistant-2.0.0"}',
                "raw_output": '{"status":"success","message":"Code generated"}',
                "parsed_output": {"status": "success", "message": "Code generated"},
                "started_at": 1700000000.0,
                "duration_ms": 1234.5,
                "timestamp": 1700000001.0,
            }
        ],
        "success": True,
        "final_answer": "这是生成的代码：\n```python\nprint('hello')\n```",
        "started_at": 1700000000.0,
        "reward_profile": {
            "reward": 0.85,
            "confidence": 0.9,
            "dimensions": {
                "success": True,
                "stability": 1.0,
                "speed": 0.95,
                "efficiency": 0.88,
                "tool_count": 1,
                "failures": 0,
                "duration_ms": 1234.5,
            },
        },
    }
    trace.update(overrides)
    return trace


# ── TestTrajectoryStore ───────────────────────────────────────────────────────

class TestTrajectoryStore(unittest.TestCase):
    def setUp(self):
        from trajectory_store import TrajectoryStore
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "test.db")
        self.store = TrajectoryStore(db_path=self.db_path)

    def tearDown(self):
        self.store.close()
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_store_and_retrieve(self):
        traj = {
            "session_id": "s1",
            "user_input": "hello",
            "selected_skills": ["code"],
            "tool_calls": [{"tool": "test_tool", "args": "{}", "output_preview": "ok", "duration_ms": 100, "status": "success"}],
            "reward": 0.9,
            "confidence": 0.8,
            "success": True,
            "tool_count": 1,
            "duration_ms": 100.0,
            "final_answer": "world",
            "source": "openmegatron",
            "created_at": "2025-01-01T00:00:00Z",
            "metadata": {"key": "value"},
        }
        tid = self.store.store(traj)
        self.assertTrue(tid.startswith("traj_"))

        retrieved = self.store.get(tid)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved["session_id"], "s1")
        self.assertEqual(retrieved["user_input"], "hello")
        self.assertEqual(retrieved["selected_skills"], ["code"])
        self.assertEqual(len(retrieved["tool_calls"]), 1)
        self.assertEqual(retrieved["tool_calls"][0]["tool"], "test_tool")
        self.assertAlmostEqual(retrieved["reward"], 0.9)
        self.assertTrue(retrieved["success"])
        self.assertEqual(retrieved["source"], "openmegatron")
        self.assertEqual(retrieved["metadata"], {"key": "value"})

    def test_query_by_session(self):
        self.store.store({"session_id": "abc", "user_input": "a", "tool_calls": [], "success": True, "created_at": "2025-01-01T00:00:00Z"})
        self.store.store({"session_id": "xyz", "user_input": "b", "tool_calls": [], "success": False, "created_at": "2025-01-02T00:00:00Z"})

        results = self.store.query(session_id="abc")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["session_id"], "abc")

        results = self.store.query(session_id="nonexistent")
        self.assertEqual(len(results), 0)

    def test_query_by_date_range(self):
        self.store.store({"session_id": "s1", "user_input": "early", "tool_calls": [], "success": True, "created_at": "2025-01-01T00:00:00Z"})
        self.store.store({"session_id": "s2", "user_input": "mid", "tool_calls": [], "success": True, "created_at": "2025-06-15T00:00:00Z"})
        self.store.store({"session_id": "s3", "user_input": "late", "tool_calls": [], "success": True, "created_at": "2025-12-31T00:00:00Z"})

        results = self.store.query(date_from="2025-06-01T00:00:00Z", date_to="2025-07-01T00:00:00Z")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["session_id"], "s2")

    def test_query_by_success(self):
        self.store.store({"session_id": "s1", "user_input": "ok", "tool_calls": [], "success": True, "created_at": "2025-01-01T00:00:00Z"})
        self.store.store({"session_id": "s2", "user_input": "fail", "tool_calls": [], "success": False, "created_at": "2025-01-02T00:00:00Z"})

        self.assertEqual(len(self.store.query(success=True)), 1)
        self.assertEqual(len(self.store.query(success=False)), 1)

    def test_query_by_source(self):
        self.store.store({"session_id": "s1", "user_input": "a", "tool_calls": [], "success": True, "source": "openmegatron", "created_at": "2025-01-01T00:00:00Z"})
        self.store.store({"session_id": "s2", "user_input": "b", "tool_calls": [], "success": True, "source": "external_agent_jsonl", "created_at": "2025-01-02T00:00:00Z"})

        self.assertEqual(len(self.store.query(source="openmegatron")), 1)
        self.assertEqual(len(self.store.query(source="external_agent_jsonl")), 1)
        self.assertEqual(len(self.store.query(source="agent_text")), 0)

    def test_query_limit_offset(self):
        for i in range(10):
            self.store.store({"session_id": f"s{i}", "user_input": f"msg{i}", "tool_calls": [], "success": True, "created_at": f"2025-01-{i+1:02d}T00:00:00Z"})

        results = self.store.query(limit=3)
        self.assertEqual(len(results), 3)
        # Most recent first
        self.assertEqual(results[0]["session_id"], "s9")

        results = self.store.query(limit=3, offset=3)
        self.assertEqual(len(results), 3)

    def test_count(self):
        self.assertEqual(self.store.count(), 0)
        self.store.store({"session_id": "s1", "user_input": "a", "tool_calls": [], "success": True, "created_at": "2025-01-01T00:00:00Z"})
        self.store.store({"session_id": "s2", "user_input": "b", "tool_calls": [], "success": False, "created_at": "2025-01-02T00:00:00Z"})
        self.assertEqual(self.store.count(), 2)
        self.assertEqual(self.store.count(success=True), 1)
        self.assertEqual(self.store.count(source="openmegatron"), 2)

    def test_stats(self):
        self.store.store({
            "session_id": "s1", "user_input": "a", "tool_calls": [],
            "reward": 0.8, "confidence": 0.9, "success": True,
            "duration_ms": 500.0, "source": "openmegatron",
            "created_at": "2025-01-15T00:00:00Z",
        })
        self.store.store({
            "session_id": "s2", "user_input": "b", "tool_calls": [],
            "reward": 0.4, "confidence": 0.5, "success": False,
            "duration_ms": 1500.0, "source": "external_agent_jsonl",
            "created_at": "2025-01-16T00:00:00Z",
        })

        s = self.store.stats()
        self.assertEqual(s["total"], 2)
        self.assertAlmostEqual(s["success_rate"], 0.5)
        self.assertAlmostEqual(s["avg_reward"], 0.6)
        self.assertAlmostEqual(s["avg_confidence"], 0.7)
        self.assertAlmostEqual(s["avg_duration_ms"], 1000.0)
        self.assertEqual(s["by_source"], {"openmegatron": 1, "external_agent_jsonl": 1})
        self.assertIn("2025-01-15", s["by_date"])
        self.assertIn("2025-01-16", s["by_date"])

    def test_stats_empty(self):
        s = self.store.stats()
        self.assertEqual(s["total"], 0)
        self.assertEqual(s["success_rate"], 0.0)
        self.assertEqual(s["avg_reward"], 0.0)

    def test_export_jsonl(self):
        self.store.store({"session_id": "s1", "user_input": "hello", "tool_calls": [], "success": True, "created_at": "2025-01-01T00:00:00Z"})
        self.store.store({"session_id": "s2", "user_input": "world", "tool_calls": [], "success": False, "created_at": "2025-01-02T00:00:00Z"})

        out_path = os.path.join(self.tmpdir, "export.jsonl")
        count = self.store.export_jsonl(out_path)
        self.assertEqual(count, 2)

        with open(out_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        self.assertEqual(len(lines), 2)
        for line in lines:
            obj = json.loads(line)
            self.assertIn("id", obj)
            self.assertIn("session_id", obj)
            self.assertIn("tool_calls", obj)

    def test_export_empty(self):
        out_path = os.path.join(self.tmpdir, "empty_export.jsonl")
        count = self.store.export_jsonl(out_path)
        self.assertEqual(count, 0)
        self.assertTrue(os.path.exists(out_path))

    def test_delete(self):
        tid = self.store.store({"session_id": "s1", "user_input": "x", "tool_calls": [], "success": True, "created_at": "2025-01-01T00:00:00Z"})
        self.assertTrue(self.store.delete(tid))
        self.assertIsNone(self.store.get(tid))
        self.assertFalse(self.store.delete("nonexistent"))

    def test_store_auto_generates_id(self):
        tid = self.store.store({"session_id": "s1", "user_input": "x", "tool_calls": [], "success": True, "created_at": "2025-01-01T00:00:00Z"})
        self.assertTrue(tid.startswith("traj_"))
        self.assertEqual(len(tid), 21)  # "traj_" + 16 hex chars

    def test_store_preserves_explicit_id(self):
        tid = self.store.store({"id": "my-custom-id", "session_id": "s1", "user_input": "x", "tool_calls": [], "success": True, "created_at": "2025-01-01T00:00:00Z"})
        self.assertEqual(tid, "my-custom-id")


# ── TestTrajectoryCollector ────────────────────────────────────────────────────

class TestTrajectoryCollector(unittest.TestCase):
    def setUp(self):
        from trajectory_store import TrajectoryStore
        from trajectory_collector import TrajectoryCollector
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "test_collector.db")
        self.store = TrajectoryStore(db_path=self.db_path)
        self.collector = TrajectoryCollector(self.store)

    def tearDown(self):
        self.store.close()
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_collect_stores_trajectory(self):
        import asyncio
        trace = _sample_trace()
        tid = asyncio.run(self.collector.collect(trace))
        self.assertIsNotNone(tid)
        self.assertTrue(tid.startswith("traj_"))

        # Verify it's in the store
        stored = self.store.get(tid)
        self.assertIsNotNone(stored)
        self.assertEqual(stored["user_input"], "帮我写一段Python代码")
        self.assertEqual(stored["selected_skills"], ["code-assistant-2.0.0"])
        self.assertEqual(stored["tool_count"], 1)
        self.assertTrue(stored["success"])
        self.assertAlmostEqual(stored["reward"], 0.85)
        self.assertAlmostEqual(stored["confidence"], 0.9)

    def test_collect_handles_missing_fields(self):
        import asyncio
        # Minimal trace with almost nothing
        minimal = {"session_id": "s1"}
        tid = asyncio.run(self.collector.collect(minimal))
        self.assertIsNotNone(tid)

        stored = self.store.get(tid)
        self.assertEqual(stored["user_input"], "")
        self.assertEqual(stored["selected_skills"], [])
        self.assertEqual(stored["tool_count"], 0)
        self.assertFalse(stored["success"])

    def test_collect_is_idempotent_for_same_trace_and_source(self):
        import asyncio
        trace = _sample_trace()

        first_id = asyncio.run(self.collector.collect(trace))
        second_id = asyncio.run(self.collector.collect(trace))

        self.assertEqual(first_id, second_id)
        self.assertEqual(self.store.count(), 1)

    def test_collect_never_raises(self):
        import asyncio
        # Even None should not raise
        tid = asyncio.run(self.collector.collect(None))
        self.assertIsNone(tid)

        # Broken store should not crash
        self.store._conn.close()  # Force DB failure
        tid = asyncio.run(self.collector.collect(_sample_trace()))
        self.assertIsNone(tid)

    def test_install_collector(self):
        import asyncio
        from trajectory_collector import install_collector

        # Create a mock agent
        mock_agent = MagicMock()
        mock_agent._learn_from_task_trace = MagicMock()

        async def fake_learn(trace):
            pass

        mock_agent._learn_from_task_trace = fake_learn

        collector = install_collector(mock_agent, db_path=self.db_path)
        self.assertIsNotNone(collector)
        self.assertTrue(hasattr(mock_agent, "_trajectory_collector"))
        self.assertEqual(mock_agent._trajectory_collector, collector)

        # Verify _learn_from_task_trace was wrapped
        self.assertNotEqual(mock_agent._learn_from_task_trace, fake_learn)
        self.assertTrue(callable(mock_agent._learn_from_task_trace))


# ── TestExternalAgentParser ──────────────────────────────────────────────────────

class TestExternalAgentParser(unittest.TestCase):
    def setUp(self):
        from external_agent_parser import ExternalAgentParser
        self.parser = ExternalAgentParser()
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_jsonl(self, name, lines):
        """Write a list of dicts as JSONL to a temp file."""
        path = os.path.join(self.tmpdir, name)
        with open(path, "w", encoding="utf-8") as f:
            for obj in lines:
                f.write(json.dumps(obj, ensure_ascii=False) + "\n")
        return path

    def test_parse_single_turn(self):
        path = self._write_jsonl("single.jsonl", [
            {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "Hello, External Agent!"}]}},
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Hi! How can I help?"}]}},
        ])

        turns = self.parser.parse_file(path)
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]["user_input"], "Hello, External Agent!")
        self.assertEqual(turns[0]["final_answer"], "Hi! How can I help?")
        self.assertEqual(turns[0]["tool_calls"], [])

    def test_parse_multi_turn(self):
        path = self._write_jsonl("multi.jsonl", [
            {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "What is Python?"}]}},
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Python is a programming language."}]}},
            {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "Show me an example."}]}},
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "print('hello')"}]}},
        ])

        turns = self.parser.parse_file(path)
        self.assertEqual(len(turns), 2)
        self.assertEqual(turns[0]["user_input"], "What is Python?")
        self.assertEqual(turns[1]["user_input"], "Show me an example.")

    def test_parse_with_tool_calls(self):
        path = self._write_jsonl("tools.jsonl", [
            {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "Read the file app.py"}]}},
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "toolu_001", "name": "read_file", "input": {"file_path": "app.py"}},
            ]}},
            {"type": "tool_result", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "toolu_001", "content": "print('hello world')"},
            ]}},
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "The file contains a hello world program."}]}},
        ])

        turns = self.parser.parse_file(path)
        self.assertEqual(len(turns), 1)
        self.assertEqual(len(turns[0]["tool_calls"]), 1)
        self.assertEqual(turns[0]["tool_calls"][0]["tool"], "read_file")
        self.assertIn("print", turns[0]["tool_calls"][0]["output_preview"])
        self.assertEqual(turns[0]["tool_calls"][0]["status"], "success")

    def test_parse_with_thinking(self):
        path = self._write_jsonl("think.jsonl", [
            {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "Complex question"}]}},
            {"type": "thinking", "message": {"thinking": "Let me think about this..."}},
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Here is the answer."}]}},
        ])

        turns = self.parser.parse_file(path)
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]["user_input"], "Complex question")
        self.assertEqual(turns[0]["final_answer"], "Here is the answer.")
        # Thinking blocks should be skipped, not added as tool calls
        self.assertEqual(turns[0]["tool_calls"], [])

    def test_parse_empty_file(self):
        path = self._write_jsonl("empty.jsonl", [])
        turns = self.parser.parse_file(path)
        self.assertEqual(len(turns), 0)

    def test_parse_invalid_json(self):
        path = os.path.join(self.tmpdir, "bad.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            f.write('{"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "ok"}]}}\n')
            f.write('this is not json\n')
            f.write('{"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "response"}]}}\n')

        turns = self.parser.parse_file(path)
        # Should parse the valid lines, skip the bad one
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]["user_input"], "ok")
        self.assertEqual(turns[0]["final_answer"], "response")

    def test_parse_nonexistent_file(self):
        turns = self.parser.parse_file("/nonexistent/path/file.jsonl")
        self.assertEqual(len(turns), 0)

    def test_parse_directory(self):
        # Create two JSONL files in subdirectories
        subdir = os.path.join(self.tmpdir, "sub")
        os.makedirs(subdir, exist_ok=True)

        self._write_jsonl("session1.jsonl", [
            {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "Q1"}]}},
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "A1"}]}},
        ])
        self._write_jsonl(os.path.join("sub", "session2.jsonl"), [
            {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "Q2"}]}},
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "A2"}]}},
        ])

        turns = self.parser.parse_directory(self.tmpdir)
        self.assertEqual(len(turns), 2)

    def test_parse_directory_nonexistent(self):
        turns = self.parser.parse_directory("/nonexistent/directory/")
        self.assertEqual(len(turns), 0)

    def test_to_trajectories(self):
        turns = [
            {
                "session_id": "test-session",
                "user_input": "What is Python?",
                "tool_calls": [
                    {"tool": "web_search", "args": '{"query":"Python"}', "output_preview": "Python is...", "duration_ms": 500.0, "status": "success"},
                ],
                "final_answer": "Python is a programming language.",
            }
        ]

        from external_agent_parser import ExternalAgentParser
        trajectories = ExternalAgentParser().to_trajectories(turns)
        self.assertEqual(len(trajectories), 1)
        t = trajectories[0]
        self.assertEqual(t["source"], "external_agent_jsonl")
        self.assertEqual(t["user_input"], "What is Python?")
        self.assertEqual(t["tool_count"], 1)
        self.assertEqual(t["tool_calls"][0]["tool"], "web_search")
        self.assertAlmostEqual(t["reward"], 0.5)  # Conservative default
        self.assertAlmostEqual(t["confidence"], 0.5)

    def test_to_trajectories_empty(self):
        from external_agent_parser import ExternalAgentParser
        trajectories = ExternalAgentParser().to_trajectories([])
        self.assertEqual(len(trajectories), 0)

    def test_parse_string_content(self):
        """Handle messages where content is a plain string instead of a list."""
        path = self._write_jsonl("string_content.jsonl", [
            {"type": "user", "message": {"role": "user", "content": "Plain string question"}},
            {"type": "assistant", "message": {"role": "assistant", "content": "Plain string answer"}},
        ])

        turns = self.parser.parse_file(path)
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]["user_input"], "Plain string question")
        self.assertEqual(turns[0]["final_answer"], "Plain string answer")


class TestTrajectoryImporter(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_jsonl(self, name, records):
        path = os.path.join(self.tmpdir, name)
        with open(path, "w", encoding="utf-8") as f:
            for record in records:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return path

    def _write_json(self, name, payload):
        path = os.path.join(self.tmpdir, name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        return path

    def test_import_openmegatron_export(self):
        from trajectory_importer import TrajectoryImporter

        path = self._write_jsonl("openmegatron.jsonl", [
            {
                "session_id": "s1",
                "user_goal": "write tests",
                "selected_skills": ["code-test-1.0.0"],
                "tool_calls": [{"tool": "run_skill_script", "status": "success", "duration_ms": 10}],
                "reward_profile": {"reward": 0.9, "confidence": 0.8, "dimensions": {"stability": 1.0}},
                "success": True,
                "final_answer": "done",
            }
        ])

        trajectories = TrajectoryImporter().parse_path(path, format="openmegatron")
        self.assertEqual(len(trajectories), 1)
        self.assertEqual(trajectories[0]["source"], "openmegatron")
        self.assertEqual(trajectories[0]["user_input"], "write tests")
        self.assertAlmostEqual(trajectories[0]["reward"], 0.9)
        self.assertEqual(trajectories[0]["metadata"]["reward_dimensions"], {"stability": 1.0})

    def test_import_custom_framework_json(self):
        from trajectory_importer import TrajectoryImporter

        path = self._write_json("custom.json", {
            "trajectories": [
                {
                    "conversation_id": "c1",
                    "prompt": "summarize paper",
                    "skills": ["paper-reader-1.0.0"],
                    "response": "summary",
                    "reward": 0.7,
                    "success": True,
                }
            ]
        })

        trajectories = TrajectoryImporter().parse_path(path, format="generic", source="my_framework")
        self.assertEqual(len(trajectories), 1)
        self.assertEqual(trajectories[0]["source"], "my_framework")
        self.assertEqual(trajectories[0]["session_id"], "c1")
        self.assertEqual(trajectories[0]["selected_skills"], ["paper-reader-1.0.0"])
        self.assertEqual(trajectories[0]["final_answer"], "summary")

    def test_import_agent_text_jsonl_events(self):
        from trajectory_importer import TrajectoryImporter

        path = self._write_jsonl("agent_text-session.jsonl", [
            {"role": "user", "content": "fix failing tests", "session_id": "agent_text-s1"},
            {"type": "tool_call", "tool": "shell", "arguments": "pytest", "output": "1 failed", "status": "error"},
            {"role": "assistant", "content": "Fixed the failing test."},
        ])

        trajectories = TrajectoryImporter().parse_path(path, format="agent_text")
        self.assertEqual(len(trajectories), 1)
        self.assertEqual(trajectories[0]["source"], "agent_text")
        self.assertEqual(trajectories[0]["session_id"], "agent_text-s1")
        self.assertEqual(trajectories[0]["user_input"], "fix failing tests")
        self.assertEqual(trajectories[0]["tool_calls"][0]["tool"], "shell")
        self.assertEqual(trajectories[0]["final_answer"], "Fixed the failing test.")


if __name__ == "__main__":
    unittest.main()
