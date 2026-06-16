"""External Agent JSONL transcript parser.

Parses External Agent JSONL session transcript files (JSONL format) and converts them
into trajectory records compatible with TrajectoryStore.

External Agent JSONL transcript format (JSONL, one JSON object per line):
  - {"type":"user","message":{"role":"user","content":[...]}}
  - {"type":"assistant","message":{"role":"assistant","content":[...]}}
  - {"type":"tool_use","message":{"role":"assistant","content":[{"type":"tool_use",...}]}}
  - {"type":"tool_result","message":{"role":"user","content":[{"type":"tool_result",...}]}}
  - {"type":"thinking","message":{...}}  (may appear, skipped for trajectory extraction)
  - {"type":"system","message":{...}}    (may appear, skipped for trajectory extraction)

Usage:
    python -m pysrc.external_agent_parser parse /path/to/transcripts/ --output out.jsonl
    python -m pysrc.external_agent_parser import /path/to/transcripts/ --db .trajectory/trajectories.db
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Optional

from trajectory_store import TrajectoryStore, _now_iso, _make_id

logger = logging.getLogger(__name__)


class ExternalAgentParser:
    """Parser for External Agent JSONL transcript files."""

    def parse_file(self, filepath: str) -> list[dict]:
        """Parse a single JSONL transcript file into raw turns.

        Each turn is a dict with:
          - user_input: the user's message text
          - tool_calls: list of {tool, args, output_preview, status}
          - final_answer: the last assistant text response
          - session_id: derived from the filename
        """
        path = Path(filepath)
        if not path.exists():
            logger.warning("File not found: %s", filepath)
            return []

        lines = []
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        lines.append(json.loads(line))
                    except json.JSONDecodeError:
                        logger.debug("Skipping invalid JSON line in %s", filepath)
                        continue
        except Exception as exc:
            logger.warning("Error reading %s: %s", filepath, exc)
            return []

        return self._lines_to_turns(lines, session_id=path.stem)

    def parse_directory(self, dirpath: str) -> list[dict]:
        """Parse all JSONL files in a directory tree.

        Recursively finds *.jsonl files and parses each one.
        """
        all_turns: list[dict] = []
        base = Path(dirpath)
        if not base.exists():
            logger.warning("Directory not found: %s", dirpath)
            return all_turns

        for jsonl_file in sorted(base.rglob("*.jsonl")):
            turns = self.parse_file(str(jsonl_file))
            all_turns.extend(turns)
            logger.debug("Parsed %d turns from %s", len(turns), jsonl_file)

        return all_turns

    def to_trajectories(self, turns: list[dict], source: str = "external_agent_jsonl") -> list[dict]:
        """Convert parsed turns into trajectory store format."""
        trajectories = []
        for turn in turns:
            tool_calls = turn.get("tool_calls", [])
            total_duration = sum(float(tc.get("duration_ms", 0)) for tc in tool_calls)

            traj = {
                "id": _make_id(),
                "session_id": str(turn.get("session_id", "")),
                "user_input": str(turn.get("user_input", "")),
                "selected_skills": [],
                "tool_calls": tool_calls,
                "reward": 0.5,  # Conservative default for imported data
                "confidence": 0.5,
                "success": bool(turn.get("final_answer")),
                "tool_count": len(tool_calls),
                "duration_ms": total_duration,
                "final_answer": str(turn.get("final_answer", ""))[:2000],
                "source": source,
                "created_at": _now_iso(),
                "metadata": {
                    "imported_from": "external_agent_parser",
                    "original_session_id": str(turn.get("session_id", "")),
                },
            }
            trajectories.append(traj)
        return trajectories

    # ── Internal parsing helpers ──────────────────────────────────────────

    def _lines_to_turns(self, lines: list[dict], session_id: str = "") -> list[dict]:
        """Group raw JSONL lines into user-assistant turns."""
        turns: list[dict] = []
        current_turn: Optional[dict] = None
        pending_tool_uses: list[dict] = []

        for entry in lines:
            entry_type = str(entry.get("type", "")).lower()

            if entry_type == "user":
                # Start a new turn
                if current_turn is not None:
                    self._finalize_turn(current_turn, pending_tool_uses)
                    turns.append(current_turn)
                    pending_tool_uses = []

                user_text = self._extract_text(entry.get("message", {}))
                current_turn = {
                    "session_id": session_id,
                    "user_input": user_text,
                    "tool_calls": [],
                    "final_answer": "",
                }

            elif entry_type in ("assistant",):
                if current_turn is None:
                    current_turn = {
                        "session_id": session_id,
                        "user_input": "",
                        "tool_calls": [],
                        "final_answer": "",
                    }
                msg = entry.get("message", {})
                content = msg.get("content", [])

                if isinstance(content, str):
                    current_turn["final_answer"] = content
                elif isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        block_type = block.get("type", "")
                        if block_type == "tool_use":
                            pending_tool_uses.append({
                                "tool": str(block.get("name", "")),
                                "args": json.dumps(block.get("input", {}), ensure_ascii=False)[:500],
                                "id": str(block.get("id", "")),
                            })
                        elif block_type == "text":
                            text = self._concat_text(block.get("text", ""))
                            if text:
                                current_turn["final_answer"] = text
                        elif block_type == "tool_result":
                            # tool_result can appear inline in assistant messages too
                            tool_id = str(block.get("tool_use_id", ""))
                            result_text = self._concat_text(block.get("content", ""))
                            self._match_tool_result(pending_tool_uses, tool_id, result_text)

            elif entry_type == "tool_result":
                if current_turn is None:
                    continue
                msg = entry.get("message", {})
                content = msg.get("content", [])

                if isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") == "tool_result":
                            tool_id = str(block.get("tool_use_id", ""))
                            result_text = self._concat_text(block.get("content", ""))
                            self._match_tool_result(pending_tool_uses, tool_id, result_text)

            # Skip thinking, system, and other non-conversational types

        # Finalize the last turn
        if current_turn is not None:
            self._finalize_turn(current_turn, pending_tool_uses)
            turns.append(current_turn)

        return turns

    def _finalize_turn(self, turn: dict, pending_tool_uses: list[dict]) -> None:
        """Move any unmatched tool uses into the turn's tool_calls list."""
        for tu in pending_tool_uses:
            turn["tool_calls"].append({
                "tool": tu.get("tool", ""),
                "args": tu.get("args", ""),
                "output_preview": tu.get("output_preview", ""),
                "duration_ms": tu.get("duration_ms", 0.0),
                "status": tu.get("status", "unknown"),
            })

    def _match_tool_result(self, pending: list[dict], tool_id: str, result_text: str) -> None:
        """Match a tool_result to a pending tool_use by ID."""
        for tu in pending:
            if tu.get("id") == tool_id:
                tu["output_preview"] = result_text[:300]
                tu["status"] = "success" if result_text else "empty"
                tu["duration_ms"] = 0.0  # Not available in transcripts
                return
        # If no match found, still record it
        if tool_id:
            pending.append({
                "tool": "unknown",
                "args": "",
                "output_preview": result_text[:300],
                "duration_ms": 0.0,
                "status": "success" if result_text else "empty",
                "id": tool_id,
            })

    @staticmethod
    def _extract_text(message: dict) -> str:
        """Extract text content from a message dict."""
        content = message.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    parts.append(ExternalAgentParser._concat_text(block.get("text", "")))
            return " ".join(parts)
        return ""

    @staticmethod
    def _concat_text(text: Any) -> str:
        """Handle text that may be a string or a list of content blocks."""
        if isinstance(text, str):
            return text
        if isinstance(text, list):
            parts = []
            for item in text:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
            return " ".join(parts)
        return str(text)


# ── CLI entry point ──────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="External Agent JSONL transcript parser — extract trajectories from JSONL logs",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # parse: extract turns and output as JSONL
    parse_cmd = sub.add_parser("parse", help="Parse transcripts and output trajectory JSONL")
    parse_cmd.add_argument("input", help="Path to a .jsonl file or directory of .jsonl files")
    parse_cmd.add_argument("--output", "-o", default="trajectories.jsonl",
                           help="Output JSONL file path (default: trajectories.jsonl)")

    # import: parse and store directly into the trajectory database
    import_cmd = sub.add_parser("import", help="Parse and import into trajectory store")
    import_cmd.add_argument("input", help="Path to a .jsonl file or directory of .jsonl files")
    import_cmd.add_argument("--db", default=".trajectory/trajectories.db",
                            help="SQLite database path (default: .trajectory/trajectories.db)")

    # stats: show what was imported
    stats_cmd = sub.add_parser("stats", help="Show trajectory store statistics")
    stats_cmd.add_argument("--db", default=".trajectory/trajectories.db",
                           help="SQLite database path")

    return parser


def main(argv: list[str] = None) -> None:
    """CLI entry point: python -m pysrc.external_agent_parser <command> ..."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "stats":
        store = TrajectoryStore(db_path=args.db)
        stats = store.stats()
        print(json.dumps(stats, indent=2, ensure_ascii=False))
        store.close()
        return

    cc_parser = ExternalAgentParser()
    input_path = Path(args.input)

    if input_path.is_file():
        turns = cc_parser.parse_file(str(input_path))
    elif input_path.is_dir():
        turns = cc_parser.parse_directory(str(input_path))
    else:
        print(f"Error: input path not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    trajectories = cc_parser.to_trajectories(turns)
    print(f"Parsed {len(trajectories)} trajectory turns from {args.input}")

    if args.command == "parse":
        output_path = args.output
        with open(output_path, "w", encoding="utf-8") as f:
            for traj in trajectories:
                f.write(json.dumps(traj, ensure_ascii=False) + "\n")
        print(f"Exported to {output_path}")

    elif args.command == "import":
        store = TrajectoryStore(db_path=args.db)
        imported = 0
        for traj in trajectories:
            try:
                store.store(traj)
                imported += 1
            except Exception as exc:
                logger.warning("Failed to import trajectory: %s", exc)
        print(f"Imported {imported} trajectories into {args.db}")
        stats = store.stats()
        print(f"Store now has {stats['total']} total trajectories")
        store.close()


if __name__ == "__main__":
    main()
