"""Non-blocking trajectory collector for the OpenMegatron agent loop.

Hooks into the existing task_trace dict at the end of agent.chat() to
persist every request as a trajectory record for future RL training.

Design principles:
  - Fire-and-forget: never blocks or crashes the main agent loop
  - Minimal coupling: extracts from the existing task_trace dict format
  - Graceful degradation: if storage fails, logs at debug and continues
"""

from __future__ import annotations

import logging
from typing import Any

from trajectory_store import TrajectoryStore, _now_iso

logger = logging.getLogger(__name__)


class TrajectoryCollector:
    """Collects agent task traces and persists them to the trajectory store.

    Usage:
        store = TrajectoryStore()
        collector = TrajectoryCollector(store)
        await collector.collect(task_trace)
    """

    def __init__(self, store: TrajectoryStore):
        self._store = store

    async def collect(self, task_trace: dict, source: str = "openmegatron") -> str | None:
        """Persist a task_trace as a trajectory record.

        Args:
            task_trace: The task_trace dict from agent.chat().
                Expected keys: session_id, user_goal, selected_skills,
                tool_calls, success, final_answer, started_at, reward_profile.
            source: Origin label ("openmegatron", "claude_code", "codex").

        Returns:
            The trajectory ID if stored successfully, None otherwise.
            Never raises — all errors are caught and logged.
        """
        try:
            if not task_trace:
                return None
            collected = task_trace.setdefault("_trajectory_collected_ids", {})
            if isinstance(collected, dict) and source in collected:
                return collected[source]

            tool_calls = task_trace.get("tool_calls", []) or []

            # Build compact tool call summaries for storage
            compact_calls = []
            total_duration_ms = 0.0
            for tc in tool_calls:
                duration = float(tc.get("duration_ms", 0.0))
                total_duration_ms += duration

                # Determine status from parsed output
                parsed = tc.get("parsed_output")
                if isinstance(parsed, dict):
                    status = str(parsed.get("status", "")).lower()
                else:
                    status = "unknown"

                compact_calls.append({
                    "tool": str(tc.get("tool", "")),
                    "args": str(tc.get("arguments", ""))[:500],
                    "output_preview": str(tc.get("raw_output", ""))[:300],
                    "duration_ms": duration,
                    "status": status,
                })

            # Extract reward profile (may be set by _learn_from_task_trace)
            reward_profile = task_trace.get("reward_profile", {})
            reward = float(reward_profile.get("reward", 0.5))
            confidence = float(reward_profile.get("confidence", 0.5))

            metadata = {
                "routing_goal": str(task_trace.get("routing_goal", "")),
                "reward_dimensions": reward_profile.get("dimensions", {}),
            }

            # ── Extract implicit user feedback ──
            try:
                from feedback_collector import FeedbackCollector
                fb = FeedbackCollector()
                feedback = fb.collect_from_trace(task_trace)
                if feedback:
                    metadata["feedback"] = feedback
            except Exception:
                pass  # Feedback extraction is best-effort

            trajectory = {
                "session_id": str(task_trace.get("session_id", "")),
                "user_input": str(task_trace.get("user_goal", "")),
                "selected_skills": task_trace.get("selected_skills", []),
                "tool_calls": compact_calls,
                "reward": reward,
                "confidence": confidence,
                "success": bool(task_trace.get("success", False)),
                "tool_count": len(compact_calls),
                "duration_ms": total_duration_ms,
                "final_answer": str(task_trace.get("final_answer", ""))[:2000],
                "source": source,
                "created_at": _now_iso(),
                "metadata": metadata,
            }

            tid = self._store.store(trajectory)
            if isinstance(collected, dict):
                collected[source] = tid
            logger.debug("Trajectory stored: %s (session=%s, tools=%d, success=%s)",
                         tid, trajectory["session_id"], trajectory["tool_count"], trajectory["success"])
            return tid

        except Exception:
            logger.debug("Failed to store trajectory (non-fatal)", exc_info=True)
            return None

    @property
    def store(self) -> TrajectoryStore:
        """Access the underlying store for queries."""
        return self._store


def install_collector(agent: Any, db_path: str = ".trajectory/trajectories.db") -> TrajectoryCollector:
    """Install a trajectory collector on an agent instance.

    Monkey-patches agent._learn_from_task_trace to also persist the
    task_trace to SQLite after the normal learning step.

    Args:
        agent: A YuanGeAgent instance (or compatible).
        db_path: Path to the SQLite database.

    Returns:
        The TrajectoryCollector instance (for testing / direct access).
    """
    store = TrajectoryStore(db_path=db_path)
    collector = TrajectoryCollector(store)

    # Store reference on agent so the chat() hook can find it
    agent._trajectory_collector = collector

    # Also wrap _learn_from_task_trace to collect after learning
    original_learn = agent._learn_from_task_trace

    async def _learn_and_collect(trace: dict) -> None:
        try:
            await original_learn(trace)
        except Exception:
            logger.debug("_learn_from_task_trace raised (non-fatal)", exc_info=True)
        await collector.collect(trace)

    agent._learn_from_task_trace = _learn_and_collect

    logger.info("TrajectoryCollector installed (db=%s)", db_path)
    return collector
