"""OpenClaw session log importer.

Parses OpenClaw agent session logs and extracts:
  - Text trajectories (user messages, tool calls, answers)
  - Visual trajectories (screenshots, GUI actions, results)

OpenClaw log format:
  - JSONL files with one JSON object per line
  - Each line has a "type" field: "user", "assistant", "tool_use", "tool_result"
  - GUI actions appear as tool_use with names like "computer", "browser", "click"
  - Screenshots may be referenced as file paths or base64 in tool results

Also handles Hermes agent logs (similar format, slightly different field names).
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Tool names that indicate GUI/vision operations
GUI_TOOL_NAMES = {
    "computer", "computer_use", "browser", "browser_use",
    "click", "type", "scroll", "screenshot", "screen_capture",
    "mouse_move", "drag", "hotkey", "press_key",
    "execute_gui_action", "gui_automation",
    "playwright", "puppeteer", "selenium",
    "desktop", "screen", "display",
}

# Fields that may contain screenshot data
SCREENSHOT_FIELDS = {
    "screenshot_path", "screenshot", "image_path", "image",
    "screen_path", "screen", "png_path", "picture",
    "screenshot_before", "screenshot_after",
}


class OpenClawImporter:
    """Import OpenClaw/Hermes agent session logs."""

    def __init__(self, screenshot_dir: str = ".trajectory/screenshots/openclaw"):
        self._screenshot_dir = Path(screenshot_dir)
        self._screenshot_dir.mkdir(parents=True, exist_ok=True)

    # ── File discovery ──────────────────────────────────────────────────────

    def parse_directory(self, dirpath: str) -> dict:
        """Parse all log files in a directory tree.

        Returns:
            dict with keys: text_trajectories, visual_trajectories, stats
        """
        base = Path(dirpath)
        if not base.exists():
            return {"text_trajectories": [], "visual_trajectories": [], "stats": {"error": "dir not found"}}

        all_text: list[dict] = []
        all_visual: list[dict] = []

        for log_file in sorted(base.rglob("*.jsonl")):
            result = self.parse_file(str(log_file))
            all_text.extend(result.get("text_trajectories", []))
            all_visual.extend(result.get("visual_trajectories", []))

        # Also try .log files
        for log_file in sorted(base.rglob("*.log")):
            result = self.parse_text_log(str(log_file))
            all_text.extend(result.get("text_trajectories", []))
            all_visual.extend(result.get("visual_trajectories", []))

        return {
            "text_trajectories": all_text,
            "visual_trajectories": all_visual,
            "stats": {
                "text_count": len(all_text),
                "visual_count": len(all_visual),
                "files_scanned": len(list(base.rglob("*.jsonl"))) + len(list(base.rglob("*.log"))),
            },
        }

    def parse_file(self, filepath: str) -> dict:
        """Parse a single OpenClaw JSONL log file."""
        path = Path(filepath)
        if not path.exists():
            return {"text_trajectories": [], "visual_trajectories": []}

        lines = []
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    lines.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

        return self._lines_to_trajectories(lines, session_id=path.stem)

    def parse_text_log(self, filepath: str) -> dict:
        """Parse a plain-text OpenClaw/Hermes log file."""
        path = Path(filepath)
        if not path.exists():
            return {"text_trajectories": [], "visual_trajectories": []}

        turns: list[dict] = []
        current: dict | None = None
        pending_actions: list[dict] = []

        user_re = re.compile(r"^\s*(?:User|USER|user|Human)\s*[:>]\s*(.+)$")
        agent_re = re.compile(r"^\s*(?:Agent|AGENT|Assistant|External Agent|Holo|Hermes)\s*[:>]\s*(.+)$")
        tool_re = re.compile(r"^\s*(?:Tool|TOOL|Action|Computer)\s*[:>]\s*(.+)$")
        screenshot_re = re.compile(r"(?:screenshot|screen|截图)[:\s]*([^\s]+\.(?:png|jpg|jpeg))", re.IGNORECASE)

        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                text = line.strip()
                if not text:
                    continue

                user_match = user_re.match(text)
                if user_match:
                    if current is not None:
                        current["tool_calls"] = pending_actions
                        turns.append(current)
                        pending_actions = []
                    current = {
                        "session_id": path.stem,
                        "user_input": user_match.group(1),
                        "tool_calls": [],
                        "final_answer": "",
                        "created_at": "",
                    }
                    continue

                if current is None:
                    continue

                agent_match = agent_re.match(text)
                if agent_match:
                    current["final_answer"] = agent_match.group(1)
                    continue

                tool_match = tool_re.match(text)
                if tool_match:
                    action_text = tool_match.group(1)
                    ss_match = screenshot_re.search(text)
                    pending_actions.append({
                        "tool": "gui_action",
                        "args": action_text[:500],
                        "output_preview": "",
                        "duration_ms": 0.0,
                        "status": "unknown",
                        "screenshot": ss_match.group(1) if ss_match else None,
                    })

        if current is not None:
            current["tool_calls"] = pending_actions
            turns.append(current)

        # Split into text and visual
        text_trajs: list[dict] = []
        visual_trajs: list[dict] = []

        for turn in turns:
            has_gui = any(
                tc.get("tool", "") in GUI_TOOL_NAMES or
                tc.get("screenshot") is not None
                for tc in turn.get("tool_calls", [])
            )

            if has_gui:
                visual_trajs.append(self._to_visual_trajectory(turn, path.stem))
            else:
                text_trajs.append(self._to_text_trajectory(turn, path.stem))

        return {"text_trajectories": text_trajs, "visual_trajectories": visual_trajs}

    # ── Internal parsing ────────────────────────────────────────────────────

    def _lines_to_trajectories(self, lines: list[dict], session_id: str) -> dict:
        """Group JSONL lines into user-assistant turns."""
        turns: list[dict] = []
        current: dict | None = None
        pending_tools: list[dict] = []

        for entry in lines:
            entry_type = str(entry.get("type", "")).lower()

            if entry_type == "user":
                if current is not None:
                    current["tool_calls"] = pending_tools
                    turns.append(current)
                    pending_tools = []
                current = {
                    "session_id": session_id,
                    "user_input": self._extract_user_text(entry),
                    "tool_calls": [],
                    "final_answer": "",
                    "created_at": str(entry.get("timestamp", entry.get("created_at", ""))),
                }

            elif entry_type in ("assistant",):
                if current is None:
                    current = {"session_id": session_id, "user_input": "",
                              "tool_calls": [], "final_answer": "", "created_at": ""}
                self._extract_assistant_content(entry, current, pending_tools)

            elif entry_type in ("tool_use", "tool_call", "function_call"):
                if current is None:
                    continue
                tool_info = self._extract_tool_info(entry)
                if tool_info:
                    pending_tools.append(tool_info)

            elif entry_type in ("tool_result", "function_result"):
                if current is None:
                    continue
                self._match_tool_result(entry, pending_tools)

            elif entry_type in ("thinking", "system", "error"):
                pass  # Skip

        if current is not None:
            current["tool_calls"] = pending_tools
            turns.append(current)

        # Split into text and visual
        text_trajs = []
        visual_trajs = []

        for turn in turns:
            has_gui = any(
                tc.get("tool", "") in GUI_TOOL_NAMES or
                tc.get("screenshot") is not None
                for tc in turn.get("tool_calls", [])
            )
            if has_gui:
                visual_trajs.append(self._to_visual_trajectory(turn, session_id))
            text_trajs.append(self._to_text_trajectory(turn, session_id))

        return {"text_trajectories": text_trajs, "visual_trajectories": visual_trajs}

    def _extract_user_text(self, entry: dict) -> str:
        msg = entry.get("message", entry)
        content = msg.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
                elif isinstance(block, str):
                    parts.append(block)
            return " ".join(parts)
        return str(content)

    def _extract_assistant_content(self, entry: dict, turn: dict, pending: list):
        msg = entry.get("message", entry)
        content = msg.get("content", "")
        if isinstance(content, str):
            turn["final_answer"] = content
            return
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                bt = block.get("type", "")
                if bt == "text":
                    turn["final_answer"] = str(block.get("text", ""))
                elif bt in ("tool_use", "tool_call"):
                    tool_info = self._extract_tool_info_from_block(block)
                    if tool_info:
                        pending.append(tool_info)

    def _extract_tool_info(self, entry: dict) -> Optional[dict]:
        msg = entry.get("message", entry)
        name = msg.get("name", msg.get("function_name", msg.get("tool_name", "")))
        args = msg.get("arguments", msg.get("input", msg.get("parameters", {})))
        if isinstance(args, dict):
            args_str = json.dumps(args, ensure_ascii=False)
        else:
            args_str = str(args)

        screenshot = None
        if isinstance(args, dict):
            for field in SCREENSHOT_FIELDS:
                if field in args:
                    screenshot = args[field]
                    break

        return {
            "tool": str(name),
            "args": args_str[:500],
            "id": str(msg.get("id", msg.get("call_id", ""))),
            "output_preview": "",
            "duration_ms": 0.0,
            "status": "unknown",
            "screenshot": screenshot,
        }

    def _extract_tool_info_from_block(self, block: dict) -> Optional[dict]:
        name = block.get("name", "")
        args = block.get("input", block.get("arguments", {}))
        if isinstance(args, dict):
            args_str = json.dumps(args, ensure_ascii=False)
        else:
            args_str = str(args)

        screenshot = None
        if isinstance(args, dict):
            for field in SCREENSHOT_FIELDS:
                if field in args:
                    screenshot = args[field]
                    break

        return {
            "tool": str(name),
            "args": args_str[:500],
            "id": str(block.get("id", block.get("call_id", ""))),
            "output_preview": "",
            "duration_ms": 0.0,
            "status": "unknown",
            "screenshot": screenshot,
        }

    def _match_tool_result(self, entry: dict, pending: list):
        msg = entry.get("message", entry)
        tool_id = str(msg.get("tool_use_id", msg.get("call_id", "")))
        content = msg.get("content", msg.get("result", msg.get("output", "")))
        result_text = self._stringify(content)

        for tu in pending:
            if tu.get("id") == tool_id:
                tu["output_preview"] = result_text[:300]
                tu["status"] = "success" if result_text and "error" not in result_text.lower() else "error"
                tu["duration_ms"] = float(msg.get("duration_ms", msg.get("elapsed", 0)))
                return

    def _stringify(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        return str(value)

    # ── Trajectory construction ─────────────────────────────────────────────

    def _to_text_trajectory(self, turn: dict, session_id: str) -> dict:
        tool_calls = turn.get("tool_calls", [])
        total_duration = sum(float(tc.get("duration_ms", 0)) for tc in tool_calls)
        uid = hashlib.sha256(
            f"{session_id}:{turn.get('user_input', '')}".encode()
        ).hexdigest()[:16]
        return {
            "id": f"oc_{uid}",
            "session_id": session_id,
            "user_input": turn.get("user_input", ""),
            "selected_skills": [],
            "tool_calls": [
                {k: v for k, v in tc.items() if k != "screenshot"}
                for tc in tool_calls
            ],
            "reward": 0.5,
            "confidence": 0.5,
            "success": bool(turn.get("final_answer")),
            "tool_count": len(tool_calls),
            "duration_ms": total_duration,
            "final_answer": str(turn.get("final_answer", ""))[:2000],
            "source": "openclaw",
            "created_at": turn.get("created_at", ""),
            "metadata": {"imported_from": "openclaw_importer"},
        }

    def _to_visual_trajectory(self, turn: dict, session_id: str) -> dict:
        """Build a visual trajectory from a turn with GUI operations."""
        steps = []
        for i, tc in enumerate(turn.get("tool_calls", [])):
            step = {
                "step_index": i,
                "screenshot_before": tc.get("screenshot", ""),
                "screenshot_after": "",
                "action": tc.get("tool", "unknown"),
                "action_params": self._parse_action_params(tc.get("args", "{}")),
                "result": {"status": tc.get("status", "unknown")},
                "elapsed_ms": tc.get("duration_ms", 0),
            }
            # Link screenshots: previous step's after = current step's before
            if i > 0 and steps[i - 1].get("screenshot_before"):
                steps[i]["screenshot_before"] = steps[i - 1]["screenshot_before"]
            steps.append(step)

        total_elapsed = sum(s["elapsed_ms"] for s in steps)
        uid = hashlib.sha256(
            f"{session_id}:{turn.get('user_input', '')}".encode()
        ).hexdigest()[:16]
        return {
            "trajectory_id": f"voc_{uid}",
            "session_id": session_id,
            "user_goal": turn.get("user_input", ""),
            "steps": steps,
            "success": bool(turn.get("final_answer")),
            "final_answer": str(turn.get("final_answer", ""))[:2000],
            "total_elapsed_ms": total_elapsed,
            "metadata": {
                "reward": 0.5,
                "reward_source": "imported",
                "source": "openclaw",
                "created_at": turn.get("created_at", ""),
            },
        }

    def _parse_action_params(self, args_str: str) -> dict:
        try:
            return json.loads(args_str) if isinstance(args_str, str) else args_str
        except (json.JSONDecodeError, TypeError):
            return {"raw": str(args_str)[:200]}

    # ── Export ──────────────────────────────────────────────────────────────

    def export_visual_to_store(self, visual_trajectories: list[dict],
                               store=None) -> int:
        """Export visual trajectories to a VisualTrajectoryStore."""
        if store is None:
            from visual_trajectory_store import VisualTrajectoryStore
            store = VisualTrajectoryStore()
        count = 0
        for traj in visual_trajectories:
            try:
                store.store(traj)
                count += 1
            except Exception as e:
                logger.debug(f"Visual trajectory store failed: {e}")
        return count

    def export_text_to_store(self, text_trajectories: list[dict],
                             store=None) -> int:
        """Export text trajectories to a TrajectoryStore."""
        if store is None:
            from trajectory_store import TrajectoryStore
            store = TrajectoryStore()
        count = 0
        for traj in text_trajectories:
            try:
                store.store(traj)
                count += 1
            except Exception as e:
                logger.debug(f"Text trajectory store failed: {e}")
        return count


# ── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="OpenClaw log importer")
    p.add_argument("input", help="Log file or directory")
    p.add_argument("--text-db", default=".trajectory/trajectories.db")
    p.add_argument("--visual-db", default=".trajectory/visual_trajectories.db")
    args = p.parse_args()

    importer = OpenClawImporter()
    result = importer.parse_directory(args.input) if os.path.isdir(args.input) else importer.parse_file(args.input)

    stats = result["stats"]
    print(f"Text trajectories: {stats.get('text_count', len(result['text_trajectories']))}")
    print(f"Visual trajectories: {stats.get('visual_count', len(result['visual_trajectories']))}")

    if result["text_trajectories"]:
        from trajectory_store import TrajectoryStore
        ts = TrajectoryStore(args.text_db)
        n = importer.export_text_to_store(result["text_trajectories"], ts)
        print(f"Imported {n} text trajectories to {args.text_db}")
        ts.close()

    if result["visual_trajectories"]:
        from visual_trajectory_store import VisualTrajectoryStore
        vs = VisualTrajectoryStore(args.visual_db)
        n = importer.export_visual_to_store(result["visual_trajectories"], vs)
        print(f"Imported {n} visual trajectories to {args.visual_db}")
        vs.close()
