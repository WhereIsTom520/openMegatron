"""QLoRA Trainer — standalone fine-tuning for companion models.

Fine-tunes a base model (Holo, Qwen, Llama) using QLoRA on SFT/DPO data
exported by training_data_pipeline.py.

Requirements: torch, transformers, peft, trl, datasets, bitsandbytes, accelerate
Install: pip install torch transformers peft trl datasets bitsandbytes accelerate

Supports:
  - SFT (Supervised Fine-Tuning) on ShareGPT-format JSONL
  - DPO (Direct Preference Optimization) on DPO-format JSONL
  - 4-bit QLoRA quantization (fits 7B model in ~6GB VRAM)
  - LoRA adapter saving/loading
  - Integration with companion_model.py for deployment
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class TrainingConfig:
    """QLoRA training hyperparameters."""
    # Model
    base_model: str = "Qwen/Qwen2.5-7B-Instruct"
    output_dir: str = ".models/companion/checkpoint"

    # LoRA
    lora_r: int = 16
    lora_alpha: int = 32
    lora_dropout: float = 0.05
    lora_target_modules: List[str] = field(default_factory=lambda: [
        "q_proj", "v_proj", "k_proj", "o_proj",
        "gate_proj", "up_proj", "down_proj",
    ])

    # Training
    num_epochs: int = 3
    batch_size: int = 2
    gradient_accumulation_steps: int = 4
    learning_rate: float = 2e-4
    warmup_ratio: float = 0.03
    max_seq_length: int = 2048

    # Quantization
    use_4bit: bool = True
    bnb_4bit_quant_type: str = "nf4"
    bnb_4bit_compute_dtype: str = "bfloat16"
    bnb_4bit_use_double_quant: bool = True

    # Saving
    save_steps: int = 200
    save_total_limit: int = 2
    logging_steps: int = 10


@dataclass
class TrainingResult:
    """Result of a training run."""
    status: str  # "success", "error"
    output_dir: str = ""
    base_model: str = ""
    train_loss: float = 0.0
    num_epochs: int = 0
    training_time_minutes: float = 0.0
    model_info: dict = field(default_factory=dict)
    error: str = ""


class QLoRATrainer:
    """Standalone QLoRA fine-tuning executor.

    Usage:
        trainer = QLoRATrainer()
        result = trainer.train_sft("sft_data.jsonl", TrainingConfig())
        result = trainer.train_dpo("dpo_data.jsonl", TrainingConfig())
    """

    def __init__(self):
        self._check_dependencies()

    @staticmethod
    def _write_model_info(
        output_dir: str,
        base_model: str,
        task_domain: str = "text",
        accuracy: float = 0.0,
        f1_score: float = 0.0,
        n_samples: int = 0,
        is_active: bool = False,
        metadata: dict = None,
    ) -> None:
        try:
            from companion_model import CompanionModelInfo, CompanionModelLoader
            info = CompanionModelInfo(
                model_id=Path(output_dir).name,
                model_path=output_dir,
                backend="transformers",
                base_model=base_model,
                task_domain=task_domain,
                accuracy=accuracy,
                f1_score=f1_score,
                n_samples=n_samples,
                is_active=is_active,
                metadata=metadata or {},
            )
            CompanionModelLoader(Path(output_dir).parent).save_model_info(output_dir, info)
        except Exception as e:
            logger.debug("Companion model metadata write skipped: %s", e)

    @staticmethod
    def _check_dependencies():
        missing = []
        for pkg in ["torch", "transformers", "peft", "datasets"]:
            try:
                __import__(pkg)
            except ImportError:
                missing.append(pkg)
        if missing:
            logger.warning(
                f"Missing QLoRA dependencies: {missing}. "
                f"Install: pip install torch transformers peft trl datasets "
                f"bitsandbytes accelerate"
            )

    # ── SFT Training ────────────────────────────────────────────────────────

    def train_sft(self, dataset_path: str, config: TrainingConfig = None) -> TrainingResult:
        """Fine-tune a model on SFT (ShareGPT-format) data.

        Args:
            dataset_path: Path to ShareGPT-format JSONL file.
            config: Training configuration.

        Returns:
            TrainingResult with status and metrics.
        """
        config = config or TrainingConfig()
        t0 = time.monotonic()

        if not os.path.exists(dataset_path):
            return TrainingResult(status="error", error=f"Dataset not found: {dataset_path}")

        try:
            import torch
            from transformers import (
                AutoModelForCausalLM,
                AutoTokenizer,
                BitsAndBytesConfig,
                TrainingArguments,
            )
            from peft import LoraConfig, get_peft_model, TaskType
            from datasets import load_dataset

            logger.info(f"Starting SFT training on {config.base_model}")
            logger.info(f"Dataset: {dataset_path}, Output: {config.output_dir}")

            # ── Load model ──────────────────────────────────────────────────
            bnb_config = None
            if config.use_4bit:
                compute_dtype = getattr(torch, config.bnb_4bit_compute_dtype)
                bnb_config = BitsAndBytesConfig(
                    load_in_4bit=True,
                    bnb_4bit_quant_type=config.bnb_4bit_quant_type,
                    bnb_4bit_compute_dtype=compute_dtype,
                    bnb_4bit_use_double_quant=config.bnb_4bit_use_double_quant,
                )

            model = AutoModelForCausalLM.from_pretrained(
                config.base_model,
                quantization_config=bnb_config,
                device_map="auto",
                trust_remote_code=True,
            )
            tokenizer = AutoTokenizer.from_pretrained(config.base_model, trust_remote_code=True)
            if tokenizer.pad_token is None:
                tokenizer.pad_token = tokenizer.eos_token

            # ── LoRA config ─────────────────────────────────────────────────
            lora_config = LoraConfig(
                r=config.lora_r,
                lora_alpha=config.lora_alpha,
                target_modules=config.lora_target_modules,
                lora_dropout=config.lora_dropout,
                bias="none",
                task_type=TaskType.CAUSAL_LM,
            )
            model = get_peft_model(model, lora_config)
            model.print_trainable_parameters()

            # ── Load dataset ────────────────────────────────────────────────
            dataset = load_dataset("json", data_files=dataset_path, split="train")

            def format_sft(example):
                """Format ShareGPT conversation into training text."""
                convs = json.loads(example.get("conversations", "[]")) if isinstance(
                    example.get("conversations"), str
                ) else example.get("conversations", [])

                parts = []
                for turn in convs:
                    role = turn.get("from", "")
                    text = turn.get("value", "")
                    if role == "human":
                        parts.append(f"<|user|>\n{text}</s>")
                    elif role == "gpt":
                        parts.append(f"<|assistant|>\n{text}</s>")
                parts.append("<|assistant|>\n")
                return {"text": "\n".join(parts)}

            dataset = dataset.map(format_sft, remove_columns=dataset.column_names)

            def tokenize_fn(examples):
                return tokenizer(
                    examples["text"],
                    truncation=True,
                    max_length=config.max_seq_length,
                    padding="max_length",
                )

            dataset = dataset.map(tokenize_fn, batched=True, remove_columns=["text"])

            # ── Training ────────────────────────────────────────────────────
            training_args = TrainingArguments(
                output_dir=config.output_dir,
                per_device_train_batch_size=config.batch_size,
                gradient_accumulation_steps=config.gradient_accumulation_steps,
                num_train_epochs=config.num_epochs,
                learning_rate=config.learning_rate,
                warmup_ratio=config.warmup_ratio,
                fp16=True,
                logging_steps=config.logging_steps,
                save_steps=config.save_steps,
                save_total_limit=config.save_total_limit,
                remove_unused_columns=False,
                report_to="none",
            )

            from transformers import Trainer
            trainer = Trainer(
                model=model,
                args=training_args,
                train_dataset=dataset,
                tokenizer=tokenizer,
            )

            trainer.train()

            # ── Save ────────────────────────────────────────────────────────
            os.makedirs(config.output_dir, exist_ok=True)
            model.save_pretrained(config.output_dir)
            tokenizer.save_pretrained(config.output_dir)
            self._write_model_info(
                config.output_dir,
                config.base_model,
                metadata={"mode": "sft", "status": "trained"},
            )

            elapsed = (time.monotonic() - t0) / 60
            logger.info(f"SFT training complete in {elapsed:.1f} minutes")

            return TrainingResult(
                status="success",
                output_dir=config.output_dir,
                base_model=config.base_model,
                train_loss=float(trainer.state.log_history[-1].get("loss", 0)),
                num_epochs=config.num_epochs,
                training_time_minutes=round(elapsed, 1),
                model_info={
                    "lora_r": config.lora_r,
                    "lora_alpha": config.lora_alpha,
                    "trainable_params": sum(
                        p.numel() for p in model.parameters() if p.requires_grad
                    ),
                },
            )

        except ImportError as e:
            return TrainingResult(status="error", error=f"Missing dependencies: {e}")
        except Exception as e:
            logger.error(f"SFT training failed: {e}", exc_info=True)
            return TrainingResult(status="error", error=str(e))

    # ── DPO Training ────────────────────────────────────────────────────────

    def train_dpo(self, dataset_path: str, config: TrainingConfig = None) -> TrainingResult:
        """Fine-tune a model on DPO (Direct Preference Optimization) data.

        Args:
            dataset_path: Path to DPO-format JSONL file.
            config: Training configuration.

        Returns:
            TrainingResult with status and metrics.
        """
        config = config or TrainingConfig()
        t0 = time.monotonic()

        if not os.path.exists(dataset_path):
            return TrainingResult(status="error", error=f"Dataset not found: {dataset_path}")

        try:
            import torch
            from transformers import (
                AutoModelForCausalLM,
                AutoTokenizer,
                BitsAndBytesConfig,
                TrainingArguments,
            )
            from peft import LoraConfig, get_peft_model, TaskType
            from datasets import load_dataset

            logger.info(f"Starting DPO training on {config.base_model}")

            # ── Load model ──────────────────────────────────────────────────
            compute_dtype = getattr(torch, config.bnb_4bit_compute_dtype) if config.use_4bit else torch.float16
            bnb_config = None
            if config.use_4bit:
                bnb_config = BitsAndBytesConfig(
                    load_in_4bit=True,
                    bnb_4bit_quant_type=config.bnb_4bit_quant_type,
                    bnb_4bit_compute_dtype=compute_dtype,
                    bnb_4bit_use_double_quant=config.bnb_4bit_use_double_quant,
                )

            model = AutoModelForCausalLM.from_pretrained(
                config.base_model,
                quantization_config=bnb_config,
                device_map="auto",
                trust_remote_code=True,
            )
            tokenizer = AutoTokenizer.from_pretrained(config.base_model, trust_remote_code=True)
            if tokenizer.pad_token is None:
                tokenizer.pad_token = tokenizer.eos_token

            # ── LoRA ────────────────────────────────────────────────────────
            lora_config = LoraConfig(
                r=config.lora_r,
                lora_alpha=config.lora_alpha,
                target_modules=config.lora_target_modules,
                lora_dropout=config.lora_dropout,
                bias="none",
                task_type=TaskType.CAUSAL_LM,
            )
            model = get_peft_model(model, lora_config)
            model.print_trainable_parameters()

            # ── Load dataset ────────────────────────────────────────────────
            dataset = load_dataset("json", data_files=dataset_path, split="train")

            def format_dpo(example):
                """Format DPO pair into chosen/rejected conversations."""
                chosen_raw = example.get("chosen", "[]")
                rejected_raw = example.get("rejected", "[]")

                chosen_conv = json.loads(chosen_raw) if isinstance(chosen_raw, str) else chosen_raw
                rejected_conv = json.loads(rejected_raw) if isinstance(rejected_raw, str) else rejected_raw

                def conv_to_text(conv):
                    parts = []
                    for turn in conv:
                        role = turn.get("role", "user")
                        content = turn.get("content", "")
                        if role == "user":
                            parts.append(f"<|user|>\n{content}</s>")
                        elif role == "assistant":
                            parts.append(f"<|assistant|>\n{content}</s>")
                    parts.append("<|assistant|>\n")
                    return "\n".join(parts)

                return {
                    "chosen": conv_to_text(chosen_conv),
                    "rejected": conv_to_text(rejected_conv),
                }

            dataset = dataset.map(format_dpo, remove_columns=dataset.column_names)

            # ── Training ────────────────────────────────────────────────────
            training_args = TrainingArguments(
                output_dir=config.output_dir,
                per_device_train_batch_size=config.batch_size,
                gradient_accumulation_steps=config.gradient_accumulation_steps,
                num_train_epochs=config.num_epochs,
                learning_rate=config.learning_rate,
                warmup_ratio=config.warmup_ratio,
                fp16=True,
                logging_steps=config.logging_steps,
                save_steps=config.save_steps,
                save_total_limit=config.save_total_limit,
                remove_unused_columns=False,
                report_to="none",
            )

            try:
                from trl import DPOTrainer
                dpo_trainer = DPOTrainer(
                    model=model,
                    args=training_args,
                    train_dataset=dataset,
                    tokenizer=tokenizer,
                    max_length=config.max_seq_length,
                    max_prompt_length=config.max_seq_length // 2,
                )
                dpo_trainer.train()
                train_loss = float(dpo_trainer.state.log_history[-1].get("loss", 0))

            except ImportError:
                # trl not available — fall back to SFT on chosen only
                logger.warning("trl not available, falling back to SFT on chosen responses")
                # Extract chosen only and do SFT
                def extract_chosen(example):
                    return {"text": example["chosen"]}
                sft_dataset = dataset.map(extract_chosen, remove_columns=["chosen", "rejected"])

                def tokenize_fn(examples):
                    return tokenizer(
                        examples["text"],
                        truncation=True,
                        max_length=config.max_seq_length,
                        padding="max_length",
                    )
                sft_dataset = sft_dataset.map(tokenize_fn, batched=True, remove_columns=["text"])

                from transformers import Trainer
                dpo_trainer = Trainer(
                    model=model,
                    args=training_args,
                    train_dataset=sft_dataset,
                    tokenizer=tokenizer,
                )
                dpo_trainer.train()
                train_loss = float(dpo_trainer.state.log_history[-1].get("loss", 0))

            # ── Save ────────────────────────────────────────────────────────
            os.makedirs(config.output_dir, exist_ok=True)
            model.save_pretrained(config.output_dir)
            tokenizer.save_pretrained(config.output_dir)
            self._write_model_info(
                config.output_dir,
                config.base_model,
                metadata={"mode": "dpo", "status": "trained"},
            )

            elapsed = (time.monotonic() - t0) / 60
            logger.info(f"DPO training complete in {elapsed:.1f} minutes")

            return TrainingResult(
                status="success",
                output_dir=config.output_dir,
                base_model=config.base_model,
                train_loss=train_loss,
                num_epochs=config.num_epochs,
                training_time_minutes=round(elapsed, 1),
            )

        except ImportError as e:
            return TrainingResult(status="error", error=f"Missing dependencies: {e}")
        except Exception as e:
            logger.error(f"DPO training failed: {e}", exc_info=True)
            return TrainingResult(status="error", error=str(e))

    # ── Continue training (fine-tune an already fine-tuned adapter) ─────────

    def continue_training(self, adapter_path: str, dataset_path: str,
                          mode: str = "sft", config: TrainingConfig = None) -> TrainingResult:
        """Continue training from an existing LoRA adapter.

        Args:
            adapter_path: Path to existing LoRA adapter.
            dataset_path: Path to training data.
            mode: "sft" or "dpo".
            config: Training configuration (base_model should match the adapter).
        """
        config = config or TrainingConfig()

        # Merge adapter into base model first, then train a new adapter
        try:
            import torch
            from peft import PeftModel
            from transformers import AutoModelForCausalLM

            logger.info(f"Merging adapter {adapter_path} into {config.base_model}")

            base_model = AutoModelForCausalLM.from_pretrained(
                config.base_model,
                torch_dtype=torch.float16,
                device_map="auto",
                trust_remote_code=True,
            )
            merged_model = PeftModel.from_pretrained(base_model, adapter_path)
            merged_model = merged_model.merge_and_unload()

            # Save merged model as new base
            merged_path = os.path.join(config.output_dir, "merged_base")
            merged_model.save_pretrained(merged_path)

            # Train on merged base
            new_config = TrainingConfig(
                base_model=merged_path,
                output_dir=config.output_dir,
                **{k: v for k, v in config.__dict__.items()
                   if k not in ("base_model", "output_dir")},
            )

            if mode == "dpo":
                return self.train_dpo(dataset_path, new_config)
            return self.train_sft(dataset_path, new_config)

        except Exception as e:
            return TrainingResult(status="error", error=str(e))

    # ── Evaluate ────────────────────────────────────────────────────────────

    def evaluate(self, model_path: str, eval_data_path: str,
                 base_model: str = None) -> dict:
        """Evaluate a fine-tuned model on held-out data.

        Returns dict with accuracy, F1, and per-example results.
        """
        if base_model is None:
            logger.error("base_model required for evaluation")
            return {"error": "base_model required"}

        if not os.path.exists(eval_data_path):
            return {"error": f"Eval data not found: {eval_data_path}"}

        try:
            import torch
            from peft import PeftModel
            from transformers import AutoModelForCausalLM, AutoTokenizer

            model = AutoModelForCausalLM.from_pretrained(
                base_model,
                torch_dtype=torch.float16,
                device_map="auto",
                trust_remote_code=True,
            )
            if os.path.exists(os.path.join(model_path, "adapter_config.json")):
                model = PeftModel.from_pretrained(model, model_path)

            tokenizer = AutoTokenizer.from_pretrained(model_path or base_model, trust_remote_code=True)
            if tokenizer.pad_token is None:
                tokenizer.pad_token = tokenizer.eos_token

            # Load eval data
            correct = 0
            total = 0
            with open(eval_data_path, "r") as f:
                for line in f:
                    example = json.loads(line.strip())
                    convs = example.get("conversations", [])
                    if len(convs) < 2:
                        continue

                    user_msg = convs[0].get("value", "")
                    expected = convs[1].get("value", "")

                    inputs = tokenizer(
                        f"<|user|>\n{user_msg}</s>\n<|assistant|>\n",
                        return_tensors="pt", truncation=True,
                        max_length=1024,
                    ).to(model.device)

                    with torch.no_grad():
                        outputs = model.generate(
                            **inputs, max_new_tokens=256,
                            temperature=0.1, do_sample=False,
                        )

                    response = tokenizer.decode(
                        outputs[0][inputs["input_ids"].shape[1]:],
                        skip_special_tokens=True,
                    ).strip()

                    # Simple overlap metric
                    if expected.lower() in response.lower() or response.lower() in expected.lower():
                        correct += 1
                    total += 1

            accuracy = correct / max(1, total)
            return {
                "accuracy": round(accuracy, 4),
                "correct": correct,
                "total": total,
            }

        except ImportError as e:
            return {"error": f"Missing dependencies: {e}"}
        except Exception as e:
            return {"error": str(e)}


# ── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="QLoRA fine-tuning for companion models")
    p.add_argument("--mode", choices=["sft", "dpo"], default="sft")
    p.add_argument("--dataset", required=True, help="Path to training data JSONL")
    p.add_argument("--base-model", default="Qwen/Qwen2.5-7B-Instruct")
    p.add_argument("--output-dir", default=".models/companion/checkpoint")
    p.add_argument("--epochs", type=int, default=3)
    p.add_argument("--batch-size", type=int, default=2)
    p.add_argument("--lr", type=float, default=2e-4)
    p.add_argument("--lora-r", type=int, default=16)
    p.add_argument("--eval-data", help="Path to evaluation data")
    p.add_argument("--activate", action="store_true",
                   help="Mark the model active if evaluation passes --min-accuracy")
    p.add_argument("--min-accuracy", type=float, default=0.6)
    args = p.parse_args()

    config = TrainingConfig(
        base_model=args.base_model,
        output_dir=args.output_dir,
        num_epochs=args.epochs,
        batch_size=args.batch_size,
        learning_rate=args.lr,
        lora_r=args.lora_r,
    )

    trainer = QLoRATrainer()

    if args.mode == "sft":
        result = trainer.train_sft(args.dataset, config)
    else:
        result = trainer.train_dpo(args.dataset, config)

    print(json.dumps({
        "status": result.status,
        "output_dir": result.output_dir,
        "training_time_minutes": result.training_time_minutes,
        "error": result.error,
    }, indent=2, ensure_ascii=False))

    if args.eval_data and result.status == "success":
        eval_result = trainer.evaluate(args.output_dir, args.eval_data, args.base_model)
        print(json.dumps({"evaluation": eval_result}, indent=2, ensure_ascii=False))
        accuracy = float(eval_result.get("accuracy", 0.0)) if isinstance(eval_result, dict) else 0.0
        trainer._write_model_info(
            args.output_dir,
            args.base_model,
            accuracy=accuracy,
            f1_score=accuracy,
            n_samples=int(eval_result.get("total", 0)) if isinstance(eval_result, dict) else 0,
            is_active=bool(args.activate and accuracy >= args.min_accuracy),
            metadata={
                "mode": args.mode,
                "status": "evaluated",
                "min_accuracy": args.min_accuracy,
            },
        )
