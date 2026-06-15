"""DPO Training Pipeline for Visual Agent Fine-tuning.

Uses preference pairs from VisualTrajectoryStore to fine-tune a VLM
(vision-language model) via Direct Preference Optimization.

Two modes:
  1. Export mode: exports preference pairs in standard DPO format (JSONL)
     for use with external trainers (Unsloth, LLaMA-Factory, etc.)
  2. QLoRA mode: in-process QLoRA fine-tuning using peft + transformers
     (requires significant GPU VRAM — 16GB+ for 4B models)

The pipeline integrates with the existing AutoRetrainLoop pattern:
  collect → build preference pairs → train → evaluate → deploy
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── DPO Data Preparation ────────────────────────────────────────────────────


def build_dpo_dataset(visual_store, text_store=None,
                      min_reward_delta: float = 0.2,
                      max_pairs: int = 500) -> dict:
    """Build a DPO training dataset from visual trajectories.

    Combines:
      1. Visual preference pairs (from visual_store)
      2. Text preference pairs (from text_store, if available)
         where high-reward trajectories = chosen, low-reward = rejected

    Args:
        visual_store: VisualTrajectoryStore instance.
        text_store: Optional TrajectoryStore instance.
        min_reward_delta: Minimum reward difference for preference pairs.
        max_pairs: Maximum number of pairs to generate.

    Returns:
        dict with {pairs: [...], stats: {...}}.
    """
    pairs = []

    # 1. Visual preference pairs
    try:
        vp_pairs = visual_store.get_preference_pairs(
            used_for_training=False, limit=max_pairs,
        )
        for vp in vp_pairs:
            pairs.append({
                "chosen": {
                    "screenshot": vp["chosen_screenshot"],
                    "action": vp["chosen_action"],
                },
                "rejected": {
                    "screenshot": vp["rejected_screenshot"],
                    "action": vp["rejected_action"],
                },
                "reward_delta": vp["reward_delta"],
                "source": "visual_preference_pair",
            })
    except Exception as e:
        logger.warning(f"Visual preference pairs skipped: {e}")

    # 2. Text trajectory pairs (high vs low reward for same task)
    if text_store is not None and len(pairs) < max_pairs:
        try:
            all_trajs = text_store.query(limit=1000)
            # Group by user_goal prefix similarity
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
                                "user_input": chosen.get("user_input", ""),
                                "tool_calls": chosen.get("tool_calls_json", "[]"),
                            },
                            "rejected": {
                                "user_input": rejected.get("user_input", ""),
                                "tool_calls": rejected.get("tool_calls_json", "[]"),
                            },
                            "reward_delta": abs(r1 - r2),
                            "source": "text_pair",
                        })
        except Exception as e:
            logger.warning(f"Text preference pairs skipped: {e}")

    return {
        "pairs": pairs[:max_pairs],
        "stats": {
            "total_pairs": len(pairs),
            "visual_pairs": sum(1 for p in pairs if p["source"] == "visual_preference_pair"),
            "text_pairs": sum(1 for p in pairs if p["source"] == "text_pair"),
            "avg_reward_delta": (
                sum(p["reward_delta"] for p in pairs) / len(pairs)
            ) if pairs else 0,
        },
    }


def export_dpo_jsonl(dataset: dict, output_path: str) -> str:
    """Export DPO dataset as JSONL file.

    Standard format compatible with Unsloth, LLaMA-Factory, etc.
    Each line: {"chosen": [...], "rejected": [...], "metadata": {...}}
    """
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    count = 0
    with open(output_path, "w", encoding="utf-8") as f:
        for pair in dataset.get("pairs", []):
            # For vision models: format as conversation with image
            chosen_conv = [
                {"role": "user", "content": _format_vision_content(pair["chosen"])},
                {"role": "assistant", "content": json.dumps(
                    {"action": pair["chosen"].get("action", ""),
                     "params": pair["chosen"].get("params", {})},
                    ensure_ascii=False,
                )},
            ]
            rejected_conv = [
                {"role": "user", "content": _format_vision_content(pair["rejected"])},
                {"role": "assistant", "content": json.dumps(
                    {"action": pair["rejected"].get("action", ""),
                     "params": pair["rejected"].get("params", {})},
                    ensure_ascii=False,
                )},
            ]
            f.write(json.dumps({
                "chosen": json.dumps(chosen_conv, ensure_ascii=False),
                "rejected": json.dumps(rejected_conv, ensure_ascii=False),
                "metadata": {"reward_delta": pair["reward_delta"], "source": pair["source"]},
            }, ensure_ascii=False) + "\n")
            count += 1

    logger.info(f"Exported {count} DPO pairs to {output_path}")
    return output_path


def _format_vision_content(item: dict) -> str:
    """Format a vision DPO item as conversation content."""
    parts = []
    if item.get("screenshot"):
        parts.append(f"[IMAGE: {item['screenshot']}]")
    if item.get("user_input"):
        parts.append(item["user_input"])
    if item.get("tool_calls"):
        parts.append(f"[TOOLS: {item['tool_calls']}]")
    return "\n".join(parts) if parts else ""


# ── QLoRA Training Launcher ──────────────────────────────────────────────────


def train_dpo_qlora(
    dataset_path: str,
    base_model: str = "Holo-3.1-4B",
    output_dir: str = ".models/vision/dpo-checkpoint",
    lora_r: int = 16,
    lora_alpha: int = 32,
    learning_rate: float = 2e-4,
    num_epochs: int = 3,
    batch_size: int = 2,
    gradient_accumulation: int = 4,
    use_4bit: bool = True,
):
    """Launch QLoRA DPO training using the transformers + peft libraries.

    This is a launcher — it constructs and runs the training command.
    For production use, consider using Unsloth or LLaMA-Factory directly.

    Args:
        dataset_path: Path to the exported DPO JSONL file.
        base_model: HuggingFace model ID or local path.
        output_dir: Where to save the LoRA adapter.
        lora_r, lora_alpha: LoRA rank and alpha parameters.
        learning_rate: Peak learning rate.
        num_epochs: Number of training epochs.
        batch_size: Per-device batch size.
        gradient_accumulation: Gradient accumulation steps.
        use_4bit: Use 4-bit quantization (requires bitsandbytes).

    Returns:
        dict with training result metadata.
    """
    try:
        import torch
        from transformers import (
            AutoProcessor,
            AutoModelForVision2Seq,
            TrainingArguments,
        )
        from peft import LoraConfig, get_peft_model, TaskType
        from trl import DPOTrainer
    except ImportError as e:
        return {
            "error": f"Missing dependencies: {e}. "
                     f"Install: pip install torch transformers peft trl accelerate bitsandbytes"
        }

    if not os.path.exists(dataset_path):
        return {"error": f"Dataset not found: {dataset_path}"}

    logger.info(f"Starting QLoRA DPO training on {base_model}")
    logger.info(f"Dataset: {dataset_path}, Output: {output_dir}")

    t0 = time.monotonic()

    try:
        # Load model in 4-bit
        bnb_config = None
        if use_4bit:
            from transformers import BitsAndBytesConfig
            bnb_config = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=torch.bfloat16,
                bnb_4bit_use_double_quant=True,
            )

        model = AutoModelForVision2Seq.from_pretrained(
            base_model,
            quantization_config=bnb_config,
            device_map="auto",
            trust_remote_code=True,
        )

        # LoRA config
        lora_config = LoraConfig(
            r=lora_r,
            lora_alpha=lora_alpha,
            target_modules=["q_proj", "v_proj", "k_proj", "o_proj",
                           "gate_proj", "up_proj", "down_proj"],
            lora_dropout=0.05,
            bias="none",
            task_type=TaskType.CAUSAL_LM,
        )

        model = get_peft_model(model, lora_config)
        model.print_trainable_parameters()

        # Load dataset
        from datasets import load_dataset
        dataset = load_dataset("json", data_files=dataset_path, split="train")

        # Training args
        training_args = TrainingArguments(
            output_dir=output_dir,
            per_device_train_batch_size=batch_size,
            gradient_accumulation_steps=gradient_accumulation,
            num_train_epochs=num_epochs,
            learning_rate=learning_rate,
            fp16=True,
            logging_steps=10,
            save_steps=100,
            save_total_limit=2,
            remove_unused_columns=False,
            report_to="none",
        )

        # DPO Trainer
        trainer = DPOTrainer(
            model=model,
            args=training_args,
            train_dataset=dataset,
            tokenizer=AutoProcessor.from_pretrained(base_model).tokenizer,
        )

        trainer.train()

        # Save adapter
        model.save_pretrained(output_dir)
        logger.info(f"LoRA adapter saved to {output_dir}")

        elapsed = (time.monotonic() - t0) / 60
        return {
            "status": "success",
            "base_model": base_model,
            "output_dir": output_dir,
            "lora_r": lora_r,
            "lora_alpha": lora_alpha,
            "num_epochs": num_epochs,
            "training_time_minutes": round(elapsed, 1),
        }

    except Exception as e:
        logger.error(f"DPO training failed: {e}")
        return {"error": str(e)}


# ── Training Orchestrator ────────────────────────────────────────────────────


class VisualDPOPipeline:
    """Orchestrates the full DPO training cycle for visual agent fine-tuning.

    Usage:
        pipeline = VisualDPOPipeline(visual_store, text_store)
        result = pipeline.run(export_path="dpo_data.jsonl", train=True)
    """

    def __init__(self, visual_store, text_store=None):
        self._visual_store = visual_store
        self._text_store = text_store

    def prepare(self, output_path: str, min_reward_delta: float = 0.2,
                max_pairs: int = 500) -> dict:
        """Build and export the DPO dataset."""
        dataset = build_dpo_dataset(
            self._visual_store, self._text_store,
            min_reward_delta, max_pairs,
        )
        export_path = export_dpo_jsonl(dataset, output_path)
        return {
            "export_path": export_path,
            "stats": dataset["stats"],
        }

    def run(self, export_path: str = ".trajectory/dpo_export.jsonl",
            train: bool = False, base_model: str = "Holo-3.1-4B",
            output_dir: str = ".models/vision/dpo-checkpoint",
            **train_kwargs) -> dict:
        """Run the full prepare → train cycle.

        Args:
            export_path: Where to save the DPO JSONL.
            train: If True, also run QLoRA training.
            base_model: Base VLM to fine-tune.
            output_dir: Where to save the LoRA adapter.
            **train_kwargs: Passed to train_dpo_qlora().

        Returns:
            dict with prepare and (optionally) training results.
        """
        result = {"prepare": self.prepare(export_path)}

        if train and "error" not in result["prepare"]:
            result["train"] = train_dpo_qlora(
                dataset_path=export_path,
                base_model=base_model,
                output_dir=output_dir,
                **train_kwargs,
            )

            # Mark pairs as used
            if "error" not in result["train"]:
                try:
                    pairs = self._visual_store.get_preference_pairs(
                        used_for_training=False, limit=500,
                    )
                    self._visual_store.mark_pairs_trained(
                        [p["id"] for p in pairs]
                    )
                except Exception:
                    pass

        return result


# ── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="DPO training pipeline for visual agent fine-tuning"
    )
    parser.add_argument("--visual-db", default=".trajectory/visual_trajectories.db",
                        help="Visual trajectory database path")
    parser.add_argument("--text-db", default=".trajectory/trajectories.db",
                        help="Text trajectory database path")
    parser.add_argument("--export", default=".trajectory/dpo_export.jsonl",
                        help="DPO export path")
    parser.add_argument("--train", action="store_true",
                        help="Run QLoRA training after export")
    parser.add_argument("--base-model", default="Holo-3.1-4B",
                        help="Base VLM model for fine-tuning")
    parser.add_argument("--output-dir", default=".models/vision/dpo-checkpoint",
                        help="Output directory for LoRA adapter")
    parser.add_argument("--min-delta", type=float, default=0.2,
                        help="Minimum reward delta for preference pairs")
    parser.add_argument("--max-pairs", type=int, default=500,
                        help="Maximum preference pairs")

    args = parser.parse_args()

    from visual_trajectory_store import VisualTrajectoryStore

    visual_store = VisualTrajectoryStore(args.visual_db)
    text_store = None
    if os.path.exists(args.text_db):
        from trajectory_store import TrajectoryStore
        text_store = TrajectoryStore(args.text_db)

    pipeline = VisualDPOPipeline(visual_store, text_store)
    result = pipeline.run(
        export_path=args.export,
        train=args.train,
        base_model=args.base_model,
        output_dir=args.output_dir,
        min_reward_delta=args.min_delta,
        max_pairs=args.max_pairs,
    )

    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
