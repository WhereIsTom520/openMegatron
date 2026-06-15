"""Visual trajectory collector for GUI automation.

Records (screenshot_before, action, screenshot_after, reward) tuples
as visual trajectories for training a vision-based reward model and
eventually DPO fine-tuning of VLM agents.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Screenshot storage directory
DEFAULT_SCREENSHOT_DIR = ".trajectory/screenshots"


@dataclass
class VisualStep:
    """A single step in a GUI automation sequence."""
    step_index: int
    screenshot_before_path: str
    action: str  # "click", "type", "scroll", "drag", etc.
    action_params: dict = field(default_factory=dict)
    screenshot_after_path: str = ""
    action_result: dict = field(default_factory=dict)
    elapsed_ms: float = 0.0


@dataclass
class VisualTrajectory:
    """A complete GUI automation task trace."""
    trajectory_id: str
    session_id: str
    user_goal: str
    steps: List[VisualStep] = field(default_factory=list)
    success: bool = False
    final_answer: str = ""
    total_elapsed_ms: float = 0.0
    metadata: dict = field(default_factory=dict)
    created_at: str = ""


class VisualTrajectoryCollector:
    """Collects visual trajectories during GUI automation tasks.

    Hooks into the agent loop when GUI tools (screenshot, execute_gui_action)
    are used. Saves screenshots to disk and builds structured trajectory records.

    Usage:
        collector = VisualTrajectoryCollector()
        collector.start_task(session_id, user_goal)

        # Before each GUI action:
        collector.record_step(screenshot_before_path, action, params)

        # After action completes:
        collector.complete_step(screenshot_after_path, result)

        # When task finishes:
        trajectory = collector.finish_task(success=True, final_answer="Done")
    """

    def __init__(self, screenshot_dir: str = DEFAULT_SCREENSHOT_DIR,
                 max_screenshots_per_task: int = 100):
        self._screenshot_dir = Path(screenshot_dir)
        self._screenshot_dir.mkdir(parents=True, exist_ok=True)
        self._max_screenshots = max_screenshots_per_task
        self._active_task: Optional[VisualTrajectory] = None
        self._step_counter: int = 0

    def start_task(self, session_id: str, user_goal: str) -> str:
        """Begin collecting a new visual trajectory.

        Returns:
            trajectory_id string.
        """
        traj_id = f"vt_{hashlib.sha256(f'{session_id}:{time.time()}'.encode()).hexdigest()[:16]}"
        self._active_task = VisualTrajectory(
            trajectory_id=traj_id,
            session_id=session_id,
            user_goal=user_goal,
        )
        self._step_counter = 0
        logger.debug(f"Visual trajectory started: {traj_id}")
        return traj_id

    def record_step(self, screenshot_before: str, action: str,
                    action_params: dict = None) -> int:
        """Record the start of a GUI action step.

        Args:
            screenshot_before: Path to pre-action screenshot.
            action: Action name (click, type, scroll, etc.).
            action_params: Action parameters dict.

        Returns:
            Step index number.
        """
        if self._active_task is None:
            return -1
        if self._step_counter >= self._max_screenshots:
            logger.warning(f"Max screenshots ({self._max_screenshots}) reached for task")
            return -1

        step = VisualStep(
            step_index=self._step_counter,
            screenshot_before_path=screenshot_before,
            action=action,
            action_params=action_params or {},
        )
        self._active_task.steps.append(step)
        self._step_counter += 1
        return step.step_index

    def complete_step(self, step_index: int, screenshot_after: str = "",
                      result: dict = None, elapsed_ms: float = 0.0):
        """Complete a previously recorded step with post-action data."""
        if self._active_task is None:
            return
        if 0 <= step_index < len(self._active_task.steps):
            step = self._active_task.steps[step_index]
            step.screenshot_after_path = screenshot_after
            step.action_result = result or {}
            step.elapsed_ms = elapsed_ms

    def capture_and_record(self, action: str, action_params: dict = None) -> dict:
        """Capture screenshot, record step, and return info for the agent.

        Convenience method that combines screenshot capture with step recording.
        Returns a dict the agent can use.
        """
        from screen_capture import capture_fullscreen

        t0 = time.monotonic()
        screenshot = capture_fullscreen()
        screenshot_path = self._save_screenshot(
            screenshot["base64"],
            f"step_{self._step_counter}_before",
        )

        step_idx = self.record_step(screenshot_path, action, action_params)

        return {
            "step_index": step_idx,
            "screenshot_path": screenshot_path,
            "data_uri": f"data:image/png;base64,{screenshot['base64']}",
            "width": screenshot["width"],
            "height": screenshot["height"],
        }

    def finish_task(self, success: bool = False, final_answer: str = "",
                    metadata: dict = None) -> Optional[VisualTrajectory]:
        """Complete the active task and return the trajectory."""
        if self._active_task is None:
            return None
        self._active_task.success = success
        self._active_task.final_answer = final_answer
        self._active_task.metadata = metadata or {}
        self._active_task.total_elapsed_ms = sum(
            s.elapsed_ms for s in self._active_task.steps
        )
        self._active_task.created_at = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
        )
        trajectory = self._active_task
        self._active_task = None
        logger.info(
            f"Visual trajectory {trajectory.trajectory_id}: "
            f"{len(trajectory.steps)} steps, success={success}"
        )
        return trajectory

    def _save_screenshot(self, base64_data: str, name: str) -> str:
        """Save a base64 screenshot to disk and return the file path."""
        task_dir = self._screenshot_dir / (
            self._active_task.trajectory_id if self._active_task else "unknown"
        )
        task_dir.mkdir(parents=True, exist_ok=True)
        filepath = task_dir / f"{name}.png"
        with open(filepath, "wb") as f:
            f.write(base64.b64decode(base64_data))
        return str(filepath)

    def to_dict(self, trajectory: VisualTrajectory = None) -> dict:
        """Serialize a visual trajectory to a JSON-compatible dict."""
        traj = trajectory or self._active_task
        if traj is None:
            return {}
        return {
            "trajectory_id": traj.trajectory_id,
            "session_id": traj.session_id,
            "user_goal": traj.user_goal,
            "steps": [
                {
                    "step_index": s.step_index,
                    "screenshot_before": s.screenshot_before_path,
                    "screenshot_after": s.screenshot_after_path,
                    "action": s.action,
                    "action_params": s.action_params,
                    "result": s.action_result,
                    "elapsed_ms": s.elapsed_ms,
                }
                for s in traj.steps
            ],
            "success": traj.success,
            "final_answer": traj.final_answer,
            "total_elapsed_ms": traj.total_elapsed_ms,
            "metadata": traj.metadata,
            "created_at": traj.created_at,
        }
