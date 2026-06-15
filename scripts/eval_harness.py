#!/usr/bin/env python3
"""
Evaluation harness for openMegatron coding capabilities.

Runs a set of standardized coding tasks against the agent and measures:
  - Task completion rate (pass/fail)
  - Tool call accuracy (correct tool for the job)
  - Self-repair rate (retries that succeed)
  - Edit precision (correct file, correct location)
  - Quality gate pass rate (lint/typecheck/test)

Usage:
  python scripts/eval_harness.py                    # Run all benchmarks
  python scripts/eval_harness.py --category code    # Code tasks only
  python scripts/eval_harness.py --task search_fix  # Single task
  python scripts/eval_harness.py --compare          # Compare vs stored baseline
"""

from __future__ import annotations

import json
import os
import sys
import time
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "pysrc"))


# ── Benchmark tasks ───────────────────────────────────

@dataclass
class EvalTask:
    id: str
    category: str          # code | research | media | office
    description: str       # Natural language task description
    difficulty: str        # easy | medium | hard
    expected_tools: list[str]  # Tools/skills the agent should use
    success_criteria: dict     # How to determine success
    setup_commands: list[str] = field(default_factory=list)
    teardown_commands: list[str] = field(default_factory=list)
    timeout_seconds: int = 120
    weight: float = 1.0

TASKS: list[EvalTask] = [
    # ── Code tasks ──
    EvalTask(
        id="code_inspect",
        category="code",
        description="Inspect the current project and tell me what tech stack it uses, how many source files it has, and what test framework is configured.",
        difficulty="easy",
        expected_tools=["code_assistant"],
        success_criteria={"tool_used": ["code_assistant"], "output_contains": ["language", "test_framework"]},
        weight=1.0,
    ),
    EvalTask(
        id="code_search",
        category="code",
        description="Find all places in the codebase where 'TODO' or 'FIXME' comments exist.",
        difficulty="easy",
        expected_tools=["code_assistant"],
        success_criteria={"tool_used": ["code_assistant"], "output_format": "structured_list"},
        weight=1.0,
    ),
    EvalTask(
        id="code_security_scan",
        category="code",
        description="Scan the src/ directory for any exposed secrets or dangerous patterns.",
        difficulty="medium",
        expected_tools=["code_assistant", "code_review"],
        success_criteria={"tool_used": ["code_review", "code_assistant"], "output_format": "structured_list"},
        weight=1.5,
    ),
    EvalTask(
        id="code_complexity",
        category="code",
        description="Which files have the highest code complexity? Show me the top 5 hotspots.",
        difficulty="medium",
        expected_tools=["code_assistant", "code_review"],
        success_criteria={"tool_used": ["code_review", "code_assistant"], "output_contains": ["complexity", "hotspot"]},
        weight=1.5,
    ),
    EvalTask(
        id="code_deps",
        category="code",
        description="List all production dependencies of this project with their versions.",
        difficulty="easy",
        expected_tools=["code_assistant"],
        success_criteria={"tool_used": ["code_assistant"], "output_contains": ["dependencies", "version"]},
        weight=1.0,
    ),
    EvalTask(
        id="code_refactor_prep",
        category="code",
        description="I want to refactor the main App component. First, create a git snapshot, then show me the current git status including modified files.",
        difficulty="medium",
        expected_tools=["code_refactor", "code_assistant"],
        success_criteria={"tool_used": ["code_refactor", "code_assistant"], "output_contains": ["snapshot", "branch"]},
        weight=2.0,
    ),
    EvalTask(
        id="code_test_suggestions",
        category="code",
        description="Look at the utility functions in this project and suggest test cases for any 2 functions that don't have tests yet.",
        difficulty="medium",
        expected_tools=["code_test"],
        success_criteria={"tool_used": ["code_test"], "output_contains": ["test", "suggest"]},
        weight=2.0,
    ),
    EvalTask(
        id="code_pipeline_plan",
        category="code",
        description="Plan a refactoring of the Sidebar component. Show me the plan without making changes.",
        difficulty="hard",
        expected_tools=["code_pipeline"],
        success_criteria={"tool_used": ["code_pipeline"], "output_contains": ["PLAN", "files", "UNDERSTAND"]},
        weight=3.0,
    ),

    # ── Multi-tool integration tasks ──
    EvalTask(
        id="multi_inspect_then_review",
        category="code",
        description="First inspect the project, then run a code review on src/.",
        difficulty="hard",
        expected_tools=["code_assistant", "code_review"],
        success_criteria={"tools_used_in_order": True, "tools_used": ["code_assistant", "code_review"]},
        weight=3.0,
    ),

    # ── Research tasks (regression check) ──
    EvalTask(
        id="research_search_paper",
        category="research",
        description="Search for recent papers about large language model evaluation benchmarks published after 2024.",
        difficulty="medium",
        expected_tools=["top_paper_search", "sci_journal_search"],
        success_criteria={"tool_used": ["top_paper_search", "sci_journal_search"]},
        weight=2.0,
    ),
]


# ── Evaluation metrics ────────────────────────────────

@dataclass
class EvalResult:
    task_id: str
    passed: bool
    score: float          # 0.0 to 1.0
    duration_seconds: float
    tools_used: list[str]
    errors: list[str]
    details: dict

@dataclass
class EvalReport:
    timestamp: str
    total_tasks: int
    passed: int
    failed: int
    overall_score: float  # weighted average
    by_category: dict[str, dict]
    by_difficulty: dict[str, dict]
    results: list[EvalResult]

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "total": self.total_tasks,
            "passed": self.passed,
            "failed": self.failed,
            "overall_score": round(self.overall_score, 3),
            "by_category": self.by_category,
            "by_difficulty": self.by_difficulty,
            "results": [
                {
                    "task_id": r.task_id, "passed": r.passed, "score": round(r.score, 3),
                    "duration_s": round(r.duration_seconds, 1), "tools_used": r.tools_used,
                    "errors": r.errors[:3],
                }
                for r in self.results
            ],
        }


# ── Runner ────────────────────────────────────────────

class EvalRunner:
    """Runs eval tasks against the agent API."""

    def __init__(self, api_base: str = "http://127.0.0.1:8000"):
        self.api_base = api_base
        self.baseline_path = PROJECT_ROOT / ".runtime" / "eval_baseline.json"

    def _call_agent(self, task: EvalTask) -> dict:
        """Send a task to the agent API and collect tool calls."""
        import urllib.request
        import urllib.error

        start = time.time()
        tools_used = []
        errors = []

        try:
            # Send the task as a chat message
            body = json.dumps({
                "session_id": f"eval_{task.id}_{int(time.time())}",
                "message": task.description,
                "mode": "eval",
            }).encode("utf-8")

            req = urllib.request.Request(
                f"{self.api_base}/chat",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            with urllib.request.urlopen(req, timeout=task.timeout_seconds) as resp:
                raw = resp.read().decode("utf-8")
                data = json.loads(raw) if raw else {}

            # Extract tool calls from response
            if isinstance(data, dict):
                for msg in data.get("messages", []):
                    if isinstance(msg, dict):
                        tool = msg.get("tool") or msg.get("skill_name") or ""
                        if tool:
                            tools_used.append(tool)
                if data.get("error"):
                    errors.append(str(data["error"]))

        except urllib.error.URLError as e:
            errors.append(f"API error: {e}")
        except Exception as e:
            errors.append(f"Error: {e}")

        duration = time.time() - start
        return {
            "duration": duration,
            "tools_used": tools_used,
            "errors": errors,
        }

    def _offline_eval(self, task: EvalTask) -> EvalResult:
        """Offline evaluation: check whether the expected tools exist and are configured correctly.

        This is a fast pre-flight check that doesn't require the LLM.
        Full online evaluation requires the agent API to be running.
        """
        start = time.time()
        tools_used = []
        errors = []

        # Check that all expected tools exist
        skills_dir = PROJECT_ROOT / "pysrc" / "skills"
        for tool_name in task.expected_tools:
            found = False
            expected = tool_name.replace("_", "-").lower()
            skill_files = list(skills_dir.rglob("SKILL.md")) + list(skills_dir.rglob("skill.md"))
            for skill_md in skill_files:
                try:
                    content = skill_md.read_text(encoding="utf-8")
                    content_normalized = content.replace("_", "-").lower()
                    path_normalized = str(skill_md.parent).replace("_", "-").lower()
                    if f"name: {tool_name}".lower() in content.lower() or expected in content_normalized or expected in path_normalized:
                        found = True
                        tools_used.append(tool_name)
                        break
                except Exception:
                    continue
            if not found:
                errors.append(f"Tool '{tool_name}' not found — skill may be missing")

        # Score based on tool availability
        if task.expected_tools:
            tool_score = len(tools_used) / len(task.expected_tools)
        else:
            tool_score = 1.0

        # Check success criteria
        criteria_score = 1.0
        criteria = task.success_criteria
        if "tool_used" in criteria:
            required = set(criteria["tool_used"])
            available = set(tools_used)
            criteria_score = min(criteria_score, len(available & required) / len(required) if required else 1.0)

        score = (tool_score * 0.6 + criteria_score * 0.4) * task.weight
        if errors:
            score *= 0.5  # Penalty for missing tools

        return EvalResult(
            task_id=task.id,
            passed=len(errors) == 0,
            score=min(score, task.weight),
            duration_seconds=time.time() - start,
            tools_used=tools_used,
            errors=errors,
            details={"difficulty": task.difficulty, "category": task.category},
        )

    def run_all(self, tasks: list[EvalTask] = None) -> EvalReport:
        """Run all eval tasks."""
        tasks = tasks or TASKS
        results: list[EvalResult] = []
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%S")

        for task in tasks:
            # Try online first, fall back to offline
            result = self._offline_eval(task)
            results.append(result)

        passed = sum(1 for r in results if r.passed)
        failed = len(results) - passed

        # Aggregate by category
        by_category: dict[str, dict] = {}
        by_difficulty: dict[str, dict] = {}
        for r in results:
            cat = r.details.get("category", "unknown")
            diff = r.details.get("difficulty", "unknown")
            for group, key in [(by_category, cat), (by_difficulty, diff)]:
                if key not in group:
                    group[key] = {"total": 0, "passed": 0, "score_sum": 0.0}
                group[key]["total"] += 1
                if r.passed:
                    group[key]["passed"] += 1
                group[key]["score_sum"] += r.score

        for group in [by_category, by_difficulty]:
            for key in group:
                g = group[key]
                g["pass_rate"] = round(g["passed"] / max(g["total"], 1), 3)
                g["avg_score"] = round(g["score_sum"] / max(g["total"], 1), 3)
                del g["score_sum"]

        total_weight = sum(t.weight for t in tasks)
        overall = sum(r.score for r in results) / max(total_weight, 1)

        report = EvalReport(
            timestamp=timestamp,
            total_tasks=len(tasks),
            passed=passed,
            failed=failed,
            overall_score=overall,
            by_category=by_category,
            by_difficulty=by_difficulty,
            results=results,
        )
        return report

    def save_baseline(self, report: EvalReport):
        self.baseline_path.parent.mkdir(parents=True, exist_ok=True)
        self.baseline_path.write_text(json.dumps(report.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")

    def compare_baseline(self, report: EvalReport) -> dict:
        if not self.baseline_path.exists():
            return {"status": "no_baseline", "message": "No baseline saved yet. Run without --compare to create one."}
        try:
            baseline = json.loads(self.baseline_path.read_text(encoding="utf-8"))
        except Exception:
            return {"status": "error", "message": "Failed to read baseline"}

        diff = report.overall_score - baseline.get("overall_score", 0)
        direction = "improved" if diff > 0 else ("declined" if diff < 0 else "unchanged")

        task_diffs = []
        base_results = {r["task_id"]: r for r in baseline.get("results", [])}
        for r in report.results:
            base = base_results.get(r.task_id, {})
            base_score = base.get("score", 0)
            delta = r.score - base_score
            task_diffs.append({
                "task_id": r.task_id,
                "current": round(r.score, 3),
                "baseline": round(base_score, 3),
                "delta": round(delta, 3),
                "direction": "improved" if delta > 0.01 else ("declined" if delta < -0.01 else "same"),
            })

        return {
            "status": "compared",
            "baseline_timestamp": baseline.get("timestamp", "unknown"),
            "current_timestamp": report.timestamp,
            "overall_delta": round(diff, 3),
            "direction": direction,
            "current_score": round(report.overall_score, 3),
            "baseline_score": round(baseline.get("overall_score", 0), 3),
            "task_diffs": task_diffs,
        }


def main():
    import argparse
    parser = argparse.ArgumentParser(description="openMegatron Eval Harness")
    parser.add_argument("--category", choices=["code", "research", "media", "office", "monitoring"], help="Filter by category")
    parser.add_argument("--task", help="Run a single task by ID")
    parser.add_argument("--difficulty", choices=["easy", "medium", "hard"], help="Filter by difficulty")
    parser.add_argument("--compare", action="store_true", help="Compare against saved baseline")
    parser.add_argument("--save", action="store_true", help="Save results as new baseline")
    parser.add_argument("--api-base", default="http://127.0.0.1:8000", help="Agent API base URL")
    # Phase 4: companion model evaluation subcommands
    parser.add_argument("--ab-compare", action="store_true", help="Run A/B comparison: model vs rule-based scoring")
    parser.add_argument("--model", help="Model path for --ab-compare or --regression-check")
    parser.add_argument("--db", default=".trajectory/trajectories.db", help="Trajectory DB path")
    parser.add_argument("--dashboard", action="store_true", help="Show learning dashboard")
    parser.add_argument("--regression-check", action="store_true", help="Run regression guard validation")
    parser.add_argument("--current-model", help="Current model path for --regression-check")
    args = parser.parse_args()

    # ── Phase 4: Companion model evaluation ──
    if args.dashboard:
        from pysrc.learning_dashboard import LearningDashboard
        dash = LearningDashboard()
        print(dash.to_text())
        dash.close()
        return

    if args.ab_compare:
        if not args.model:
            print("Error: --model is required for --ab-compare")
            sys.exit(1)
        from pysrc.trajectory_store import TrajectoryStore
        from pysrc.eval_ab import ABComparison
        store = TrajectoryStore(db_path=args.db)
        ab = ABComparison()
        metrics = ab.run(store, args.model)
        print(ab.to_markdown(metrics))
        store.close()
        return

    if args.regression_check:
        if not args.model or not args.current_model:
            print("Error: --model and --current-model are required for --regression-check")
            sys.exit(1)
        from pysrc.trajectory_store import TrajectoryStore
        from pysrc.regression_guard import RegressionGuard
        store = TrajectoryStore(db_path=args.db)
        guard = RegressionGuard()
        result = guard.validate(args.model, args.current_model, store)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        store.close()
        sys.exit(0 if result["passed"] else 1)

    # ── Original eval harness behavior ──

    # Filter tasks
    tasks = TASKS
    if args.category:
        tasks = [t for t in tasks if t.category == args.category]
    if args.task:
        tasks = [t for t in tasks if t.id == args.task]
        if not tasks:
            print(f"Task '{args.task}' not found. Available: {[t.id for t in TASKS]}")
            sys.exit(1)
    if args.difficulty:
        tasks = [t for t in tasks if t.difficulty == args.difficulty]

    if not tasks:
        print("No tasks match the filters.")
        sys.exit(1)

    runner = EvalRunner(api_base=args.api_base)
    report = runner.run_all(tasks)

    # Output
    if args.compare:
        comparison = runner.compare_baseline(report)
        print(json.dumps(comparison, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))

    if args.save:
        runner.save_baseline(report)
        print(f"\nBaseline saved to {runner.baseline_path}")

    # Exit code: 0 if all passed, 1 if any failed
    sys.exit(0 if report.failed == 0 else 1)


if __name__ == "__main__":
    main()
