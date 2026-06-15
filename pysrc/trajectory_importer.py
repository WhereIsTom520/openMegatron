"""Multi-source trajectory importer.

Normalizes offline data from Codex logs, OpenMegatron exports, and custom
framework JSON/JSONL into TrajectoryStore-compatible records.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Iterable

from trajectory_store import _make_id, _now_iso

logger = logging.getLogger(__name__)

SUPPORTED_FORMATS = {"auto", "codex", "openmegatron", "generic"}
JSON_SUFFIXES = {".json", ".jsonl", ".ndjson"}
TEXT_SUFFIXES = {".log", ".txt"}


class TrajectoryImporter:
    """Parse local trajectory-like files into normalized trajectory records."""

    def parse_path(
        self,
        path: str,
        *,
        format: str = "auto",
        source: str | None = None,
    ) -> list[dict]:
        """Parse a file or directory of trajectory data.

        Args:
            path: File or directory path.
            format: auto, codex, openmegatron, or generic.
            source: Optional source label to store on each trajectory.
        """
        fmt = format.lower().strip()
        if fmt not in SUPPORTED_FORMATS:
            raise ValueError(f"Unsupported trajectory format: {format}")

        root = Path(path)
        if not root.exists():
            raise FileNotFoundError(f"Input path not found: {path}")

        files = [root] if root.is_file() else self._iter_input_files(root)
        trajectories: list[dict] = []
        for file_path in files:
            try:
                trajectories.extend(self.parse_file(file_path, format=fmt, source=source))
            except Exception as exc:
                logger.warning("Skipped %s: %s", file_path, exc)
        return trajectories

    def parse_file(
        self,
        path: str | Path,
        *,
        format: str = "auto",
        source: str | None = None,
    ) -> list[dict]:
        file_path = Path(path)
        fmt = format.lower().strip()
        if fmt == "auto":
            fmt = self._detect_format(file_path)

        if fmt == "codex":
            return self._parse_codex_file(file_path, source=source or "codex")
        if fmt == "openmegatron":
            return self._parse_json_trajectories(file_path, source=source or "openmegatron")
        return self._parse_json_trajectories(file_path, source=source or "custom")

    def _iter_input_files(self, root: Path) -> list[Path]:
        suffixes = JSON_SUFFIXES | TEXT_SUFFIXES
        return sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in suffixes)

    def _detect_format(self, path: Path) -> str:
        name = path.name.lower()
        if "codex" in name or path.suffix.lower() in TEXT_SUFFIXES:
            return "codex"
        return "generic"

    def _parse_json_trajectories(self, path: Path, *, source: str) -> list[dict]:
        records = self._read_json_records(path)
        trajectories: list[dict] = []
        for index, record in enumerate(records):
            if not isinstance(record, dict):
                continue
            normalized = self.normalize_record(record, source=source, row_index=index)
            if normalized is not None:
                trajectories.append(normalized)
        return trajectories

    def _parse_codex_file(self, path: Path, *, source: str) -> list[dict]:
        if path.suffix.lower() in JSON_SUFFIXES:
            records = self._read_json_records(path)
            direct: list[dict] = []
            events: list[dict] = []
            for index, record in enumerate(records):
                if not isinstance(record, dict):
                    continue
                normalized = self.normalize_record(record, source=source, row_index=index)
                if normalized is not None and self._looks_like_trajectory(record):
                    direct.append(normalized)
                else:
                    events.append(record)
            event_trajectories = self._codex_events_to_trajectories(events, session_id=path.stem, source=source)
            return direct + event_trajectories

        return self._parse_codex_text_log(path, source=source)

    def _read_json_records(self, path: Path) -> list[Any]:
        if path.suffix.lower() in {".jsonl", ".ndjson"}:
            records = []
            with path.open("r", encoding="utf-8") as file:
                for line in file:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        logger.debug("Skipping invalid JSON line in %s", path)
            return records

        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("trajectories", "records", "items", "events", "turns"):
                value = data.get(key)
                if isinstance(value, list):
                    return value
            return [data]
        return []

    def normalize_record(self, record: dict, *, source: str, row_index: int = 0) -> dict | None:
        """Normalize one custom/OpenMegatron trajectory-like record."""
        if not self._looks_like_trajectory(record):
            return None

        reward_profile = record.get("reward_profile") if isinstance(record.get("reward_profile"), dict) else {}
        metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        dimensions = reward_profile.get("dimensions") or metadata.get("reward_dimensions") or {}

        tool_calls = [self._normalize_tool_call(call) for call in self._coerce_list(record.get("tool_calls"))]
        selected_skills = self._coerce_list(record.get("selected_skills") or record.get("skills"))
        user_input = self._first_text(record, "user_input", "user_goal", "prompt", "input", "query", "request")
        final_answer = self._first_text(record, "final_answer", "answer", "output", "response", "result")

        success_value = record.get("success")
        if success_value is None:
            success_value = bool(final_answer) and not any(call.get("status") in {"error", "denied"} for call in tool_calls)

        total_duration = record.get("duration_ms")
        if total_duration is None:
            total_duration = sum(float(call.get("duration_ms") or 0.0) for call in tool_calls)

        normalized_metadata = dict(metadata)
        if dimensions:
            normalized_metadata["reward_dimensions"] = dimensions
        normalized_metadata.setdefault("imported_from", "trajectory_importer")
        normalized_metadata.setdefault("source_row_index", row_index)

        return {
            "id": str(record.get("id") or _make_id()),
            "session_id": str(record.get("session_id") or record.get("conversation_id") or record.get("thread_id") or ""),
            "user_input": user_input,
            "selected_skills": selected_skills,
            "tool_calls": tool_calls,
            "reward": self._clamp01(record.get("reward", reward_profile.get("reward", 0.5))),
            "confidence": self._clamp01(record.get("confidence", reward_profile.get("confidence", 0.5))),
            "success": bool(success_value),
            "tool_count": int(record.get("tool_count") or len(tool_calls)),
            "duration_ms": float(total_duration or 0.0),
            "final_answer": final_answer[:2000],
            "source": str(record.get("source") or source),
            "created_at": str(record.get("created_at") or record.get("timestamp") or _now_iso()),
            "metadata": normalized_metadata,
        }

    def _codex_events_to_trajectories(self, events: Iterable[dict], *, session_id: str, source: str) -> list[dict]:
        turns: list[dict] = []
        current: dict | None = None
        pending_tools: list[dict] = []

        for event in events:
            role = str(event.get("role") or event.get("author") or "").lower()
            event_type = str(event.get("type") or event.get("event") or event.get("kind") or "").lower()

            if role == "user" or event_type in {"user", "user_message", "input"}:
                if current is not None:
                    current["tool_calls"].extend(pending_tools)
                    turns.append(current)
                    pending_tools = []
                current = {
                    "session_id": str(event.get("session_id") or event.get("conversation_id") or session_id),
                    "user_input": self._event_text(event),
                    "selected_skills": [],
                    "tool_calls": [],
                    "final_answer": "",
                    "created_at": str(event.get("created_at") or event.get("timestamp") or _now_iso()),
                }
                continue

            if current is None:
                current = {
                    "session_id": str(event.get("session_id") or event.get("conversation_id") or session_id),
                    "user_input": "",
                    "selected_skills": [],
                    "tool_calls": [],
                    "final_answer": "",
                    "created_at": str(event.get("created_at") or event.get("timestamp") or _now_iso()),
                }

            if role == "assistant" or event_type in {"assistant", "assistant_message", "output"}:
                text = self._event_text(event)
                if text:
                    current["final_answer"] = text
                for call in self._extract_event_tool_calls(event):
                    pending_tools.append(call)
                continue

            if self._is_tool_event(event):
                tool_call = self._normalize_tool_call(event)
                tool_id = str(event.get("tool_call_id") or event.get("call_id") or event.get("id") or "")
                matched = False
                if tool_id:
                    for pending in pending_tools:
                        if str(pending.get("id") or "") == tool_id:
                            pending.update({k: v for k, v in tool_call.items() if v not in ("", None)})
                            matched = True
                            break
                if not matched:
                    pending_tools.append(tool_call)

        if current is not None:
            current["tool_calls"].extend(pending_tools)
            turns.append(current)

        return [self.normalize_record(turn, source=source, row_index=i) for i, turn in enumerate(turns)]

    def _parse_codex_text_log(self, path: Path, *, source: str) -> list[dict]:
        turns: list[dict] = []
        current: dict | None = None
        user_re = re.compile(r"^\s*(?:user|prompt|input)\s*[:>]\s*(.+)$", re.IGNORECASE)
        assistant_re = re.compile(r"^\s*(?:assistant|codex|output)\s*[:>]\s*(.+)$", re.IGNORECASE)
        tool_re = re.compile(r"^\s*(?:tool|command|exec)\s*[:>]\s*(.+)$", re.IGNORECASE)

        with path.open("r", encoding="utf-8", errors="replace") as file:
            for line in file:
                text = line.strip()
                if not text:
                    continue
                user_match = user_re.match(text)
                if user_match:
                    if current is not None:
                        turns.append(current)
                    current = {
                        "session_id": path.stem,
                        "user_input": user_match.group(1),
                        "selected_skills": [],
                        "tool_calls": [],
                        "final_answer": "",
                        "created_at": _now_iso(),
                    }
                    continue
                if current is None:
                    continue
                assistant_match = assistant_re.match(text)
                if assistant_match:
                    current["final_answer"] = assistant_match.group(1)
                    continue
                tool_match = tool_re.match(text)
                if tool_match:
                    current["tool_calls"].append({
                        "tool": "codex_command",
                        "args": tool_match.group(1)[:500],
                        "output_preview": "",
                        "duration_ms": 0.0,
                        "status": "unknown",
                    })

        if current is not None:
            turns.append(current)
        return [self.normalize_record(turn, source=source, row_index=i) for i, turn in enumerate(turns)]

    def _looks_like_trajectory(self, record: dict) -> bool:
        keys = set(record)
        return bool(
            keys
            & {
                "user_input",
                "user_goal",
                "prompt",
                "input",
                "query",
                "request",
                "final_answer",
                "answer",
                "response",
                "tool_calls",
                "selected_skills",
                "reward",
                "reward_profile",
            }
        )

    def _extract_event_tool_calls(self, event: dict) -> list[dict]:
        candidates = []
        for key in ("tool_calls", "tools", "commands"):
            value = event.get(key)
            if isinstance(value, list):
                candidates.extend(value)
        return [self._normalize_tool_call(call) for call in candidates if isinstance(call, dict)]

    def _is_tool_event(self, event: dict) -> bool:
        event_type = str(event.get("type") or event.get("event") or event.get("kind") or "").lower()
        return event_type in {"tool", "tool_call", "tool_result", "command", "exec"} or any(
            key in event for key in ("tool", "tool_name", "command", "function_name")
        )

    def _normalize_tool_call(self, call: dict) -> dict:
        parsed = call.get("parsed_output") if isinstance(call.get("parsed_output"), dict) else {}
        status = str(call.get("status") or parsed.get("status") or "").lower() or "unknown"
        output = call.get("output_preview")
        if output is None:
            output = call.get("raw_output", call.get("output", call.get("result", "")))
        args = call.get("args")
        if args is None:
            args = call.get("arguments", call.get("input", call.get("command", "")))
        if not isinstance(args, str):
            args = json.dumps(args, ensure_ascii=False)
        tool = call.get("tool") or call.get("tool_name") or call.get("name") or call.get("function_name")
        if not tool and call.get("command"):
            tool = "codex_command"
        return {
            "id": str(call.get("id") or call.get("tool_call_id") or call.get("call_id") or ""),
            "tool": str(tool or "unknown"),
            "args": str(args)[:500],
            "output_preview": str(output or "")[:300],
            "duration_ms": float(call.get("duration_ms") or call.get("elapsed_ms") or 0.0),
            "status": status,
        }

    def _event_text(self, event: dict) -> str:
        for key in ("content", "message", "text", "delta", "output", "response"):
            value = event.get(key)
            text = self._stringify_content(value)
            if text:
                return text
        return ""

    @staticmethod
    def _stringify_content(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            if "content" in value:
                return TrajectoryImporter._stringify_content(value.get("content"))
            if "text" in value:
                return TrajectoryImporter._stringify_content(value.get("text"))
            return ""
        if isinstance(value, list):
            parts = []
            for item in value:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    if item.get("type") in {"text", "output_text", "input_text"}:
                        parts.append(TrajectoryImporter._stringify_content(item.get("text") or item.get("content")))
            return " ".join(part for part in parts if part)
        return str(value)

    def _first_text(self, record: dict, *keys: str) -> str:
        for key in keys:
            text = self._stringify_content(record.get(key))
            if text:
                return text
        return ""

    @staticmethod
    def _coerce_list(value: Any) -> list:
        if value is None:
            return []
        if isinstance(value, list):
            return value
        return [value]

    @staticmethod
    def _clamp01(value: Any) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = 0.5
        return max(0.0, min(1.0, number))

