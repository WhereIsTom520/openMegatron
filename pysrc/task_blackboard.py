"""
Task Blackboard — Agent progress tracking, checkpoint/resume, and strategy switching.

Inspired by External Agent JSONL's todo list and Agent checkpoint mechanisms:
  - Plans a multi-step task before execution
  - Marks each step as pending → in_progress → completed (or failed → retry)
  - Saves checkpoint state to disk for crash recovery
  - Supports strategy switching: if a step fails, try an alternative approach
  - Produces human-readable progress output for the user

Usage:
    from task_blackboard import TaskBlackboard, Step

    bb = TaskBlackboard("literature_review", save_dir=".blackboard")
    bb.plan([
        Step("search", "Search top-venue papers", strategy="OpenAlex API"),
        Step("filter", "Filter by venue whitelist", strategy="venues.toml"),
        Step("read", "Read and extract paper content", strategy="PyPDF2 + OCR fallback"),
        Step("matrix", "Build evidence matrix", strategy="research_common"),
        Step("review", "Generate literature review", strategy="LLM synthesis"),
        Step("verify", "Verify citations and references", strategy="citation_verifier"),
    ])

    for step in bb.steps:
        bb.start(step.id)
        try:
            result = do_work(step)
            bb.complete(step.id, result=result)
        except Exception as e:
            if bb.can_retry(step.id):
                bb.retry(step.id, error=str(e), new_strategy="fallback approach")
            else:
                bb.fail(step.id, error=str(e))

    print(bb.progress_report())
"""

from __future__ import annotations

import json
import time
import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


class StepStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class Step:
    """A single step in a task plan."""
    id: str
    description: str
    strategy: str = ""           # Primary approach
    fallback_strategy: str = ""  # Alternative if primary fails
    status: StepStatus = StepStatus.PENDING
    started_at: float = 0.0
    completed_at: float = 0.0
    duration_ms: float = 0.0
    error: str = ""
    retry_count: int = 0
    max_retries: int = 2
    result_summary: str = ""     # Brief summary of what was produced
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "description": self.description,
            "strategy": self.strategy,
            "fallback_strategy": self.fallback_strategy,
            "status": self.status.value,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_ms": self.duration_ms,
            "error": self.error,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "result_summary": self.result_summary,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Step":
        return cls(
            id=d["id"],
            description=d["description"],
            strategy=d.get("strategy", ""),
            fallback_strategy=d.get("fallback_strategy", ""),
            status=StepStatus(d.get("status", "pending")),
            started_at=d.get("started_at", 0.0),
            completed_at=d.get("completed_at", 0.0),
            duration_ms=d.get("duration_ms", 0.0),
            error=d.get("error", ""),
            retry_count=d.get("retry_count", 0),
            max_retries=d.get("max_retries", 2),
            result_summary=d.get("result_summary", ""),
            metadata=d.get("metadata", {}),
        )


class TaskBlackboard:
    """Progress tracker with checkpoint/resume and strategy switching.

    Think of this as the agent's "dashboard" — it shows what's planned,
    what's done, what failed, and what's being retried with a new approach.
    """

    def __init__(self, task_id: str, save_dir: str = ".blackboard"):
        self.task_id = task_id
        self.save_dir = Path(save_dir)
        self.save_dir.mkdir(parents=True, exist_ok=True)
        self.steps: list[Step] = []
        self.created_at = time.time()
        self.updated_at = time.time()
        self.metadata: dict = {}
        self._step_map: dict[str, Step] = {}

    # ── Planning ────────────────────────────────────────

    def plan(self, steps: list[Step]) -> "TaskBlackboard":
        """Define the task plan. Call before execution."""
        self.steps = steps
        self._step_map = {s.id: s for s in steps}
        self._save()
        return self

    def add_step(self, step: Step) -> "TaskBlackboard":
        """Add a step to an existing plan."""
        self.steps.append(step)
        self._step_map[step.id] = step
        self._save()
        return self

    # ── Execution tracking ──────────────────────────────

    def start(self, step_id: str) -> Step:
        """Mark a step as in-progress."""
        step = self._get(step_id)
        step.status = StepStatus.IN_PROGRESS
        step.started_at = time.time()
        self.updated_at = time.time()
        self._save()
        return step

    def complete(self, step_id: str, result: Any = None, summary: str = "") -> Step:
        """Mark a step as completed successfully."""
        step = self._get(step_id)
        step.status = StepStatus.COMPLETED
        step.completed_at = time.time()
        step.duration_ms = (step.completed_at - step.started_at) * 1000
        if summary:
            step.result_summary = summary
        elif result is not None:
            step.result_summary = self._summarize_result(result)
        self.updated_at = time.time()
        self._save()
        return step

    def fail(self, step_id: str, error: str) -> Step:
        """Mark a step as failed."""
        step = self._get(step_id)
        step.status = StepStatus.FAILED
        step.completed_at = time.time()
        step.duration_ms = (step.completed_at - step.started_at) * 1000
        step.error = error[:500]
        self.updated_at = time.time()
        self._save()
        return step

    def retry(self, step_id: str, error: str = "", new_strategy: str = "") -> Step:
        """Retry a failed step, optionally with a different strategy."""
        step = self._get(step_id)
        step.retry_count += 1
        if new_strategy:
            step.fallback_strategy = new_strategy
            # Swap strategies for the retry
            step.strategy, step.fallback_strategy = new_strategy, step.strategy
        step.status = StepStatus.PENDING  # Reset for re-execution
        step.error = error[:500]
        step.started_at = 0.0
        step.completed_at = 0.0
        step.duration_ms = 0.0
        self.updated_at = time.time()
        self._save()
        return step

    def skip(self, step_id: str, reason: str = "") -> Step:
        """Skip a step (e.g., not applicable)."""
        step = self._get(step_id)
        step.status = StepStatus.SKIPPED
        step.result_summary = reason
        self.updated_at = time.time()
        self._save()
        return step

    def can_retry(self, step_id: str) -> bool:
        """Check if a step can be retried."""
        step = self._get(step_id)
        return step.retry_count < step.max_retries

    # ── Progress / status ───────────────────────────────

    def progress(self) -> dict:
        """Return progress stats."""
        total = len(self.steps)
        completed = sum(1 for s in self.steps if s.status == StepStatus.COMPLETED)
        failed = sum(1 for s in self.steps if s.status == StepStatus.FAILED)
        in_progress = sum(1 for s in self.steps if s.status == StepStatus.IN_PROGRESS)
        pending = sum(1 for s in self.steps if s.status == StepStatus.PENDING)
        skipped = sum(1 for s in self.steps if s.status == StepStatus.SKIPPED)
        return {
            "total": total,
            "completed": completed,
            "failed": failed,
            "in_progress": in_progress,
            "pending": pending,
            "skipped": skipped,
            "percent": round(completed / max(total, 1) * 100, 1),
        }

    def is_complete(self) -> bool:
        p = self.progress()
        return p["completed"] + p["skipped"] == p["total"]

    def has_failures(self) -> bool:
        return any(s.status == StepStatus.FAILED for s in self.steps)

    def current_step(self) -> Optional[Step]:
        """Get the currently executing step."""
        for s in self.steps:
            if s.status == StepStatus.IN_PROGRESS:
                return s
        # Return first pending step
        for s in self.steps:
            if s.status == StepStatus.PENDING:
                return s
        return None

    # ── Visualization ───────────────────────────────────

    def progress_report(self, lang: str = "zh") -> str:
        """Generate a human-readable progress report."""
        icons = {
            StepStatus.PENDING: "⬜",
            StepStatus.IN_PROGRESS: "🔄",
            StepStatus.COMPLETED: "✅",
            StepStatus.FAILED: "❌",
            StepStatus.SKIPPED: "⏭️",
        }

        p = self.progress()
        if lang == "zh":
            lines = [
                f"📋 任务进度: {self.task_id}",
                f"   进度: {p['completed']}/{p['total']} ({p['percent']}%)",
                f"   ✅ 完成: {p['completed']} | 🔄 进行中: {p['in_progress']} | ⬜ 待执行: {p['pending']} | ❌ 失败: {p['failed']}",
                "",
            ]
        else:
            lines = [
                f"📋 Task Progress: {self.task_id}",
                f"   Progress: {p['completed']}/{p['total']} ({p['percent']}%)",
                f"   ✅ Done: {p['completed']} | 🔄 Active: {p['in_progress']} | ⬜ Pending: {p['pending']} | ❌ Failed: {p['failed']}",
                "",
            ]

        for step in self.steps:
            icon = icons[step.status]
            duration = f" ({step.duration_ms / 1000:.1f}s)" if step.duration_ms > 0 else ""
            line = f"  {icon} {step.description}{duration}"

            if step.status == StepStatus.IN_PROGRESS:
                line += f" [策略: {step.strategy}]" if lang == "zh" else f" [strategy: {step.strategy}]"
            elif step.status == StepStatus.FAILED and step.retry_count > 0:
                retry_info = f"重试 {step.retry_count} 次" if lang == "zh" else f"retried {step.retry_count}x"
                line += f" — {retry_info}"
                if step.error:
                    line += f" — {step.error[:80]}"
            elif step.status == StepStatus.COMPLETED and step.result_summary:
                line += f" — {step.result_summary[:80]}"

            lines.append(line)

        # Strategy switches summary
        switches = [(s.id, s.description, s.strategy, s.fallback_strategy)
                     for s in self.steps if s.retry_count > 0]
        if switches:
            if lang == "zh":
                lines.append("\n🔄 策略切换记录:")
            else:
                lines.append("\n🔄 Strategy Switches:")
            for sid, desc, curr, fallback in switches:
                lines.append(f"  {desc}: {fallback} → {curr}")

        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "steps": [s.to_dict() for s in self.steps],
            "metadata": self.metadata,
            "progress": self.progress(),
        }

    # ── Checkpoint persistence ──────────────────────────

    def _save(self) -> None:
        """Save checkpoint to disk for crash recovery."""
        checkpoint_file = self.save_dir / f"{self.task_id}.json"
        try:
            checkpoint_file.write_text(
                json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning(f"Failed to save checkpoint {self.task_id}: {e}")

    @classmethod
    def resume(cls, task_id: str, save_dir: str = ".blackboard") -> Optional["TaskBlackboard"]:
        """Resume a task from its last checkpoint."""
        checkpoint_file = Path(save_dir) / f"{task_id}.json"
        if not checkpoint_file.exists():
            return None
        try:
            data = json.loads(checkpoint_file.read_text(encoding="utf-8"))
            bb = cls(task_id, save_dir)
            bb.created_at = data.get("created_at", time.time())
            bb.updated_at = data.get("updated_at", time.time())
            bb.metadata = data.get("metadata", {})
            bb.steps = [Step.from_dict(s) for s in data.get("steps", [])]
            bb._step_map = {s.id: s for s in bb.steps}
            # Reset any in-progress steps to pending (crash recovery)
            for step in bb.steps:
                if step.status == StepStatus.IN_PROGRESS:
                    step.status = StepStatus.PENDING
                    step.started_at = 0.0
            bb._save()
            return bb
        except Exception as e:
            logger.warning(f"Failed to resume checkpoint {task_id}: {e}")
            return None

    @classmethod
    def list_checkpoints(cls, save_dir: str = ".blackboard") -> list[dict]:
        """List all saved checkpoints."""
        d = Path(save_dir)
        if not d.exists():
            return []
        checkpoints = []
        for f in sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                p = data.get("progress", {})
                checkpoints.append({
                    "task_id": data.get("task_id", f.stem),
                    "updated_at": data.get("updated_at", 0),
                    "progress": f"{p.get('completed', 0)}/{p.get('total', 0)}",
                    "percent": p.get("percent", 0),
                    "has_failures": any(
                        s.get("status") == "failed"
                        for s in data.get("steps", [])
                    ),
                })
            except Exception:
                pass
        return checkpoints

    # ── Helpers ─────────────────────────────────────────

    def _get(self, step_id: str) -> Step:
        if step_id not in self._step_map:
            raise KeyError(f"Step '{step_id}' not found in plan. Available: {list(self._step_map)}")
        return self._step_map[step_id]

    @staticmethod
    def _summarize_result(result: Any) -> str:
        """Create a brief summary of a result for display."""
        if isinstance(result, dict):
            if "status" in result:
                return f"status={result['status']}"
            if "count" in result:
                return f"count={result['count']}"
            if "papers" in result:
                n = len(result["papers"]) if isinstance(result["papers"], list) else "?"
                return f"papers={n}"
            if "total" in result:
                return f"total={result['total']}"
            keys = list(result.keys())[:3]
            return ", ".join(keys)
        if isinstance(result, list):
            return f"items={len(result)}"
        if isinstance(result, str):
            return result[:60]
        return str(type(result).__name__)
