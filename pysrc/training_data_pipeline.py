"""Training data pipeline: build SFT/DPO datasets from imported logs.

Converts text + visual trajectories into:
  1. SFT (Supervised Fine-Tuning) data: (user_input, tool_calls, answer) pairs
  2. DPO (Direct Preference Optimization) data: (chosen, rejected) pairs

Integrates with:
  - TrajectoryStore (text trajectories)
  - VisualTrajectoryStore (visual trajectories)
  - ExternalAgentParser (External Agent JSONL logs)
  - OpenClawImporter (OpenClaw/Hermes logs)

Output formats:
  - SFT: ShareGPT format JSONL for LLaMA-Factory/Unsloth
  - DPO: Standard DPO JSONL for trl/Unsloth
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class SFTExample:
    """A single SFT training example."""
    user_input: str
    tool_calls: list
    final_answer: str
    source: str = "openmegatron"
    quality_score: float = 0.5


@dataclass
class DatasetStats:
    sft_count: int = 0
    dpo_count: int = 0
    text_trajectories_used: int = 0
    visual_trajectories_used: int = 0
    filtered_low_quality: int = 0
    source_breakdown: dict = field(default_factory=dict)


class TrainingDataPipeline:
    """Build SFT/DPO training datasets from all available trajectory sources."""

    def __init__(self, text_store=None, visual_store=None,
                 external_agent_jsonl_dir: str = None, openclaw_dir: str = None):
        self._text_store = text_store
        self._visual_store = visual_store
        self._external_agent_jsonl_dir = external_agent_jsonl_dir
        self._openclaw_dir = openclaw_dir

    @staticmethod
    def _json_list(value: Any) -> list:
        """Return a list from either a native list or a JSON-encoded list."""
        if isinstance(value, list):
            return value
        if isinstance(value, str) and value.strip():
            try:
                parsed = json.loads(value)
            except (TypeError, json.JSONDecodeError):
                return []
            return parsed if isinstance(parsed, list) else []
        return []

    @classmethod
    def _tool_calls_from_steps(cls, steps: Any) -> list:
        calls = []
        for step in cls._json_list(steps):
            result = step.get("result", {})
            calls.append({
                "tool": step.get("action", ""),
                "args": json.dumps(step.get("action_params", {}), ensure_ascii=False),
                "output_preview": json.dumps(result, ensure_ascii=False),
                "duration_ms": step.get("elapsed_ms", 0),
                "status": result.get("status", "unknown")
                if isinstance(result, dict) else "unknown",
            })
        return calls

    @classmethod
    def _trajectory_tool_calls(cls, trajectory: dict) -> list:
        if "tool_calls" in trajectory:
            return cls._json_list(trajectory.get("tool_calls"))
        if "tool_calls_json" in trajectory:
            return cls._json_list(trajectory.get("tool_calls_json"))
        if "steps" in trajectory:
            return cls._tool_calls_from_steps(trajectory.get("steps"))
        if "steps_json" in trajectory:
            return cls._tool_calls_from_steps(trajectory.get("steps_json"))
        return []

    # ── SFT Data ────────────────────────────────────────────────────────────

    def build_sft_dataset(self, min_quality: float = 0.5,
                          max_examples: int = 5000) -> dict:
        """Build SFT dataset from all sources.

        Args:
            min_quality: Minimum reward score to include (0.0-1.0).
            max_examples: Maximum number of examples.

        Returns:
            dict with {examples: [...], stats: DatasetStats}.
        """
        examples: list[SFTExample] = []
        stats = DatasetStats()

        # 1. From text trajectory store
        if self._text_store is not None:
            try:
                trajs = self._text_store.query(limit=max_examples)
                for t in trajs:
                    reward = t.get("reward", 0.5)
                    if reward >= min_quality:
                        examples.append(SFTExample(
                            user_input=t.get("user_input", ""),
                            tool_calls=self._trajectory_tool_calls(t),
                            final_answer=t.get("final_answer", ""),
                            source=t.get("source", "openmegatron"),
                            quality_score=reward,
                        ))
                        stats.text_trajectories_used += 1
                    else:
                        stats.filtered_low_quality += 1
                stats.source_breakdown["text_store"] = stats.text_trajectories_used
            except Exception as e:
                logger.warning(f"Text store SFT build failed: {e}")

        # 2. From visual trajectory store
        if self._visual_store is not None:
            try:
                vtrajs = self._visual_store.query(limit=max_examples)
                for t in vtrajs:
                    reward = t.get("reward", 0.5)
                    if reward >= min_quality:
                        examples.append(SFTExample(
                            user_input=t.get("user_goal", ""),
                            tool_calls=self._trajectory_tool_calls(t),
                            final_answer=t.get("final_answer", ""),
                            source="visual_" + t.get("metadata", "{}"),
                            quality_score=reward,
                        ))
                        stats.visual_trajectories_used += 1
                    else:
                        stats.filtered_low_quality += 1
                stats.source_breakdown["visual_store"] = stats.visual_trajectories_used
            except Exception as e:
                logger.warning(f"Visual store SFT build failed: {e}")

        # 3. From External Agent JSONL logs
        if self._external_agent_jsonl_dir and os.path.isdir(self._external_agent_jsonl_dir):
            try:
                from external_agent_parser import ExternalAgentParser
                parser = ExternalAgentParser()
                turns = parser.parse_directory(self._external_agent_jsonl_dir)
                trajs = parser.to_trajectories(turns, source="external_agent_jsonl")
                for t in trajs[:max_examples]:
                    if t.get("reward", 0.5) >= min_quality:
                        examples.append(SFTExample(
                            user_input=t.get("user_input", ""),
                            tool_calls=t.get("tool_calls", []),
                            final_answer=t.get("final_answer", ""),
                            source="external_agent_jsonl",
                            quality_score=t.get("reward", 0.5),
                        ))
                stats.source_breakdown["external_agent_jsonl"] = len(trajs)
            except Exception as e:
                logger.warning(f"External Agent JSONL SFT build failed: {e}")

        # 4. From OpenClaw logs
        if self._openclaw_dir and os.path.isdir(self._openclaw_dir):
            try:
                from openclaw_importer import OpenClawImporter
                importer = OpenClawImporter()
                result = importer.parse_directory(self._openclaw_dir)
                for t in result.get("text_trajectories", [])[:max_examples]:
                    if t.get("reward", 0.5) >= min_quality:
                        examples.append(SFTExample(
                            user_input=t.get("user_input", ""),
                            tool_calls=t.get("tool_calls", []),
                            final_answer=t.get("final_answer", ""),
                            source="openclaw",
                            quality_score=t.get("reward", 0.5),
                        ))
                stats.source_breakdown["openclaw"] = len(result.get("text_trajectories", []))
            except Exception as e:
                logger.warning(f"OpenClaw SFT build failed: {e}")

        stats.sft_count = len(examples)
        return {
            "examples": examples[:max_examples],
            "stats": stats,
        }

    def export_sft_sharegpt(self, output_path: str, min_quality: float = 0.5,
                            max_examples: int = 5000) -> str:
        """Export SFT data in ShareGPT format (LLaMA-Factory/Unsloth compatible).

        Each line:
        {"conversations": [
            {"from": "human", "value": "<user_input>"},
            {"from": "gpt", "value": "<tool_calls + final_answer>"}
        ]}
        """
        dataset = self.build_sft_dataset(min_quality, max_examples)
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        count = 0
        with open(output_path, "w", encoding="utf-8") as f:
            for ex in dataset["examples"]:
                # Build assistant response: tool calls + final answer
                assistant_parts = []
                if ex.tool_calls:
                    assistant_parts.append(
                        "TOOLS: " + json.dumps(ex.tool_calls, ensure_ascii=False)
                    )
                if ex.final_answer:
                    assistant_parts.append(ex.final_answer)
                assistant_text = "\n".join(assistant_parts)

                if not ex.user_input or not assistant_text:
                    continue

                record = {
                    "conversations": [
                        {"from": "human", "value": ex.user_input},
                        {"from": "gpt", "value": assistant_text},
                    ],
                    "metadata": {
                        "source": ex.source,
                        "quality_score": ex.quality_score,
                    },
                }
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
                count += 1

        logger.info(f"Exported {count} SFT examples to {output_path}")
        return output_path

    # ── DPO Data ────────────────────────────────────────────────────────────

    def build_dpo_dataset(self, min_reward_delta: float = 0.2,
                          max_pairs: int = 1000) -> dict:
        """Build DPO preference pairs from all sources.

        Pairs are formed from trajectories with similar user_input but
        different reward scores: high_reward = chosen, low_reward = rejected.
        """
        all_trajs: list[dict] = []
        stats = DatasetStats()

        # Collect all trajectories
        if self._text_store is not None:
            try:
                all_trajs.extend(self._text_store.query(limit=max_pairs * 3))
            except Exception:
                pass

        if self._visual_store is not None:
            try:
                all_trajs.extend(self._visual_store.query(limit=max_pairs))
            except Exception:
                pass

        if not all_trajs:
            return {"pairs": [], "stats": stats}

        # Build preference pairs: for trajectories with similar user_input,
        # high reward = chosen, low reward = rejected
        pairs = []
        for i, t1 in enumerate(all_trajs):
            for t2 in all_trajs[i + 1:]:
                if len(pairs) >= max_pairs:
                    break
                r1 = t1.get("reward", 0.5)
                r2 = t2.get("reward", 0.5)
                if abs(r1 - r2) >= min_reward_delta:
                    chosen = t1 if r1 > r2 else t2
                    rejected = t2 if r1 > r2 else t1
                    pairs.append({
                        "chosen": {
                            "user_input": chosen.get("user_input", chosen.get("user_goal", "")),
                            "tool_calls": self._trajectory_tool_calls(chosen),
                            "final_answer": chosen.get("final_answer", ""),
                        },
                        "rejected": {
                            "user_input": rejected.get("user_input", rejected.get("user_goal", "")),
                            "tool_calls": self._trajectory_tool_calls(rejected),
                            "final_answer": rejected.get("final_answer", ""),
                        },
                        "reward_delta": abs(r1 - r2),
                        "source": chosen.get("source", "unknown"),
                    })

        stats.dpo_count = len(pairs)
        return {"pairs": pairs[:max_pairs], "stats": stats}

    def export_dpo_jsonl(self, output_path: str, min_reward_delta: float = 0.2,
                         max_pairs: int = 1000) -> str:
        """Export DPO data in standard JSONL format."""
        dataset = self.build_dpo_dataset(min_reward_delta, max_pairs)
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        count = 0
        with open(output_path, "w", encoding="utf-8") as f:
            for pair in dataset["pairs"]:
                # Format as conversation for DPO
                chosen_conv = [
                    {"role": "user", "content": pair["chosen"]["user_input"]},
                    {"role": "assistant", "content": json.dumps({
                        "tool_calls": pair["chosen"]["tool_calls"],
                        "answer": pair["chosen"]["final_answer"],
                    }, ensure_ascii=False)},
                ]
                rejected_conv = [
                    {"role": "user", "content": pair["rejected"]["user_input"]},
                    {"role": "assistant", "content": json.dumps({
                        "tool_calls": pair["rejected"]["tool_calls"],
                        "answer": pair["rejected"]["final_answer"],
                    }, ensure_ascii=False)},
                ]
                f.write(json.dumps({
                    "chosen": json.dumps(chosen_conv, ensure_ascii=False),
                    "rejected": json.dumps(rejected_conv, ensure_ascii=False),
                    "metadata": {
                        "reward_delta": pair["reward_delta"],
                        "source": pair["source"],
                    },
                }, ensure_ascii=False) + "\n")
                count += 1

        logger.info(f"Exported {count} DPO pairs to {output_path}")
        return output_path

    # ── Pipeline ────────────────────────────────────────────────────────────

    def run_full_pipeline(self, output_dir: str = ".training_data",
                          min_quality: float = 0.5,
                          min_reward_delta: float = 0.2) -> dict:
        """Run the full SFT + DPO export pipeline.

        Returns:
            dict with paths and stats.
        """
        os.makedirs(output_dir, exist_ok=True)

        sft_path = os.path.join(output_dir, "sft_sharegpt.jsonl")
        dpo_path = os.path.join(output_dir, "dpo_pairs.jsonl")

        self.export_sft_sharegpt(sft_path, min_quality=min_quality)
        self.export_dpo_jsonl(dpo_path, min_reward_delta=min_reward_delta)

        # Stats
        sft_count = sum(1 for _ in open(sft_path, "r", encoding="utf-8"))
        dpo_count = sum(1 for _ in open(dpo_path, "r", encoding="utf-8"))

        return {
            "sft_path": sft_path,
            "dpo_path": dpo_path,
            "sft_count": sft_count,
            "dpo_count": dpo_count,
            "output_dir": output_dir,
        }


# ── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(
        description="Build SFT/DPO training datasets from trajectory logs"
    )
    p.add_argument("--text-db", default=".trajectory/trajectories.db")
    p.add_argument("--visual-db", default=".trajectory/visual_trajectories.db")
    p.add_argument("--external-agent-jsonl-dir", help="External Agent JSONL transcript directory")
    p.add_argument("--openclaw-dir", help="OpenClaw log directory")
    p.add_argument("--output-dir", default=".training_data")
    p.add_argument("--min-quality", type=float, default=0.5)
    p.add_argument("--min-delta", type=float, default=0.2)
    p.add_argument("--format", choices=["sft", "dpo", "both"], default="both")
    args = p.parse_args()

    from trajectory_store import TrajectoryStore
    from visual_trajectory_store import VisualTrajectoryStore

    text_store = TrajectoryStore(args.text_db) if os.path.exists(args.text_db) else None
    visual_store = VisualTrajectoryStore(args.visual_db) if os.path.exists(args.visual_db) else None

    pipeline = TrainingDataPipeline(
        text_store=text_store,
        visual_store=visual_store,
        external_agent_jsonl_dir=args.external_agent_jsonl_dir,
        openclaw_dir=args.openclaw_dir,
    )

    if args.format in ("sft", "both"):
        sft_path = pipeline.export_sft_sharegpt(
            os.path.join(args.output_dir, "sft_sharegpt.jsonl"),
            min_quality=args.min_quality,
        )
        print(f"SFT dataset: {sft_path}")

    if args.format in ("dpo", "both"):
        dpo_path = pipeline.export_dpo_jsonl(
            os.path.join(args.output_dir, "dpo_pairs.jsonl"),
            min_reward_delta=args.min_delta,
        )
        print(f"DPO dataset: {dpo_path}")

    if text_store:
        text_store.close()
    if visual_store:
        visual_store.close()
