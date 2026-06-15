"""User feedback collector — implicit + explicit feedback signals.

Extracts gold-standard labels from user interactions without requiring
explicit ratings. Implicit signals (retry, correction, thanks, topic switch)
provide high-confidence labels for evaluating and improving the reward model.

Feedback is stored in the trajectory metadata.feedback field.

Usage:
    fb = FeedbackCollector(store)
    fb.collect_from_trace(task_trace)  # Auto-extract implicit signals
    fb.collect_explicit("session_1", rating=4, comment="Good answer")
    labeled = fb.get_labeled_samples(min_confidence=0.7)
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Feedback signal definitions ──────────────────────────────────────────────

# Patterns that indicate user dissatisfaction / task failure
NEGATIVE_PATTERNS = [
    (re.compile(r"不对|错误|不行|重来|重新|再来|搞错了|弄错了|不是这个"), "explicit_correction", 0.95),
    (re.compile(r"再试|换一种|换个|另一个|别的"), "retry_request", 0.70),
    (re.compile(r"没懂|不理解|没明白|听不懂"), "not_understood", 0.75),
]

# Patterns that indicate user satisfaction / task success
POSITIVE_PATTERNS = [
    (re.compile(r"谢谢|感谢|好的|不错|很好|太好了|完美|OK|ok"), "explicit_thanks", 0.80),
    (re.compile(r"就是|对了|没错|是的|正是"), "confirmation", 0.70),
]

# Min text length for meaningful pattern matching
MIN_TEXT_LEN = 3


class FeedbackCollector:
    """Collects implicit + explicit user feedback as gold labels.

    Implicit signals are extracted from user messages and interaction
    patterns. Explicit signals come from direct user ratings.

    All feedback is stored in trajectory metadata.feedback for persistence.
    """

    def __init__(self, store=None):
        """Initialize with optional TrajectoryStore for persistence."""
        self._store = store

    def collect_from_trace(self, task_trace: dict) -> Optional[dict]:
        """Extract implicit feedback signals from a completed task_trace.

        Analyzes the final_answer content and user_input for signals.

        Args:
            task_trace: The task_trace dict from agent.chat().

        Returns:
            Dict with {signal, confidence, label, source} or None if no signal.
        """
        user_input = str(task_trace.get("user_goal", ""))
        final_answer = str(task_trace.get("final_answer", ""))
        success = bool(task_trace.get("success", False))

        if len(user_input) < MIN_TEXT_LEN and len(final_answer) < MIN_TEXT_LEN:
            return None

        # Check user_input for negative patterns (user expressing dissatisfaction)
        for pattern, signal, confidence in NEGATIVE_PATTERNS:
            if pattern.search(user_input):
                return {
                    "signal": signal,
                    "confidence": confidence,
                    "label": 0,
                    "source": "implicit",
                    "matched_text": user_input[:200],
                    "collected_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }

        # Heuristic: very short final_answer on a "successful" trace = likely low quality
        # (Check before positive patterns to avoid "ok" matching positive regex)
        # But skip if user_input already indicates satisfaction
        user_is_positive = any(p.search(user_input) for p, _, _ in POSITIVE_PATTERNS)
        if success and len(final_answer) < 20 and not user_is_positive:
            return {
                "signal": "short_answer",
                "confidence": 0.40,
                "label": 0,
                "source": "implicit",
                "matched_text": final_answer,
                "collected_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }

        # Check user_input for positive patterns (user expressing satisfaction)
        for pattern, signal, confidence in POSITIVE_PATTERNS:
            if pattern.search(user_input):
                return {
                    "signal": signal,
                    "confidence": confidence,
                    "label": 1,
                    "source": "implicit",
                    "matched_text": user_input[:200],
                    "collected_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }

        # Check final_answer for positive patterns
        for pattern, signal, confidence in POSITIVE_PATTERNS:
            if pattern.search(final_answer):
                return {
                    "signal": signal,
                    "confidence": confidence,
                    "label": 1,
                    "source": "implicit",
                    "matched_text": final_answer[:200],
                    "collected_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }

        # Heuristic: very short final_answer on a "successful" trace = likely low quality
        if success and len(final_answer) < 20:
            return {
                "signal": "short_answer",
                "confidence": 0.40,
                "label": 0,
                "source": "implicit",
                "matched_text": final_answer,
                "collected_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }

        # Heuristic: no tool calls + success → probably a lookup/trivial task
        tool_calls = task_trace.get("tool_calls", []) or []
        if success and len(tool_calls) == 0 and len(final_answer) > 50:
            return {
                "signal": "no_tool_direct_answer",
                "confidence": 0.50,
                "label": 1,
                "source": "implicit",
                "matched_text": final_answer[:200],
                "collected_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }

        return None

    def collect_explicit(self, session_id: str, rating: int, comment: str = "") -> Optional[str]:
        """Record explicit user rating.

        Args:
            session_id: The session to attach feedback to.
            rating: 1-5 star rating (1=bad, 5=excellent).
            comment: Optional free-text comment.

        Returns:
            Trajectory ID if stored, None if no matching session found.
        """
        if not self._store:
            logger.warning("No store configured — explicit feedback not persisted")
            return None

        if not 1 <= rating <= 5:
            logger.warning("Invalid rating %d — must be 1-5", rating)
            return None

        # Find the most recent trajectory for this session
        trajectories = self._store.query(session_id=session_id, limit=1)
        if not trajectories:
            logger.debug("No trajectory found for session %s", session_id)
            return None

        traj = trajectories[0]
        metadata = traj.get("metadata", {}) or {}
        metadata["feedback"] = {
            "signal": "explicit_rating",
            "confidence": 1.0,
            "label": 1 if rating >= 4 else (0 if rating <= 2 else 0.5),
            "source": "explicit",
            "rating": rating,
            "comment": comment[:500],
            "collected_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

        # Update the trajectory
        self._store.store({
            "id": traj["id"],
            "session_id": traj["session_id"],
            "user_input": traj["user_input"],
            "selected_skills": traj["selected_skills"],
            "tool_calls": traj["tool_calls"],
            "reward": traj["reward"],
            "confidence": traj["confidence"],
            "success": traj["success"],
            "tool_count": traj["tool_count"],
            "duration_ms": traj["duration_ms"],
            "final_answer": traj["final_answer"],
            "source": traj["source"],
            "created_at": traj["created_at"],
            "metadata": metadata,
        })

        logger.info("Explicit feedback recorded: session=%s rating=%d", session_id, rating)
        return traj["id"]

    def get_labeled_samples(self, min_confidence: float = 0.7) -> list[dict]:
        """Get high-confidence labeled samples for evaluation.

        Args:
            min_confidence: Minimum feedback confidence to include.

        Returns:
            List of trajectories with feedback, filtered by confidence.
        """
        if not self._store:
            return []

        all_trajs = self._store.query(limit=max(self._store.count(), 1))
        labeled = []
        for traj in all_trajs:
            metadata = traj.get("metadata", {}) or {}
            feedback = metadata.get("feedback")
            if feedback and feedback.get("confidence", 0) >= min_confidence:
                traj["_feedback"] = feedback
                labeled.append(traj)

        return labeled

    def labeled_stats(self) -> dict:
        """Get statistics on labeled data."""
        samples = self.get_labeled_samples(min_confidence=0.0)
        if not samples:
            return {"total_labeled": 0, "positive": 0, "negative": 0, "by_source": {}}

        high_conf = [s for s in samples if s["_feedback"]["confidence"] >= 0.7]
        positive = sum(1 for s in samples if s["_feedback"]["label"] == 1)
        negative = sum(1 for s in samples if s["_feedback"]["label"] == 0)

        by_source = {}
        for s in samples:
            src = s["_feedback"]["source"]
            by_source[src] = by_source.get(src, 0) + 1

        return {
            "total_labeled": len(samples),
            "high_confidence": len(high_conf),
            "positive": positive,
            "negative": negative,
            "neutral": len(samples) - positive - negative,
            "by_source": by_source,
        }
