"""GGUF Exporter — convert fine-tuned models to GGUF format for llama.cpp.

Converts HuggingFace/PyTorch models to GGUF format so they can run with
llama.cpp for fast local inference.

Supports:
  1. llama.cpp's built-in convert_hf_to_gguf.py script
  2. Direct PyTorch → GGUF via gguf library
  3. Merging LoRA adapters before conversion

Requirements:
  - Option 1: llama.cpp cloned locally (recommended)
  - Option 2: pip install gguf

Usage:
  python -m pysrc.gguf_exporter convert \
      --model-path .models/companion/checkpoint \
      --output .models/companion/model.gguf \
      --quantize q4_k_m
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


QUANT_METHODS = {
    "q4_0": "4-bit quantization (fastest, moderate quality)",
    "q4_k_m": "4-bit K-quant medium (recommended, good balance)",
    "q5_k_m": "5-bit K-quant medium (higher quality)",
    "q8_0": "8-bit quantization (best quality, largest)",
    "f16": "FP16 (no quantization)",
}

RECOMMENDED_QUANT = "q4_k_m"


@dataclass
class GGUFExportResult:
    status: str  # "success", "error"
    output_path: str = ""
    input_model: str = ""
    quant_method: str = ""
    file_size_mb: float = 0.0
    conversion_time_seconds: float = 0.0
    error: str = ""


class GGUFExporter:
    """Convert fine-tuned models to GGUF format."""

    def __init__(self, llama_cpp_dir: str = None):
        """Initialize the exporter.

        Args:
            llama_cpp_dir: Path to llama.cpp repository. If None, tries to find
                           it via LLAMA_CPP_DIR env var or common locations.
        """
        self._llama_cpp_dir = self._find_llama_cpp(llama_cpp_dir)

    @staticmethod
    def _write_model_info(
        gguf_path: str,
        base_model: str = "unknown",
        task_domain: str = "text",
        quality_score: float = 0.0,
        activate: bool = False,
        metadata: dict = None,
    ) -> None:
        try:
            from companion_model import CompanionModelInfo, CompanionModelLoader
            gguf = Path(gguf_path)
            info = CompanionModelInfo(
                model_id=gguf.stem,
                model_path=str(gguf),
                backend="llama_cpp",
                base_model=base_model,
                task_domain=task_domain,
                accuracy=quality_score,
                f1_score=quality_score,
                n_samples=0,
                is_active=activate,
                metadata=metadata or {},
            )
            CompanionModelLoader(str(gguf.parent)).save_model_info(str(gguf), info)
        except Exception as e:
            logger.debug("Companion GGUF metadata write skipped: %s", e)

    def _find_llama_cpp(self, explicit_path: str = None) -> Optional[str]:
        """Find the llama.cpp installation."""
        if explicit_path and os.path.isdir(explicit_path):
            return explicit_path

        # Check environment variable
        env_path = os.environ.get("LLAMA_CPP_DIR")
        if env_path and os.path.isdir(env_path):
            return env_path

        # Check common locations
        candidates = [
            os.path.expanduser("~/llama.cpp"),
            os.path.expanduser("~/github/llama.cpp"),
            "C:/llama.cpp",
            "/opt/llama.cpp",
        ]
        for path in candidates:
            if os.path.isdir(path):
                return path

        return None

    # ── Export methods ─────────────────────────────────────────────────────

    def convert_hf_to_gguf(self, model_path: str, output_path: str,
                           quantize: str = RECOMMENDED_QUANT,
                           activate: bool = False,
                           quality_score: float = 0.0,
                           task_domain: str = "text") -> GGUFExportResult:
        """Convert a HuggingFace model to GGUF using llama.cpp's converter.

        Args:
            model_path: Path to the HuggingFace model directory.
            output_path: Path for the output GGUF file.
            quantize: Quantization method (q4_0, q4_k_m, q5_k_m, q8_0, f16).

        Returns:
            GGUFExportResult with status and metadata.
        """
        t0 = time.monotonic()

        if not os.path.isdir(model_path):
            return GGUFExportResult(
                status="error",
                error=f"Model path not found: {model_path}",
            )

        if quantize not in QUANT_METHODS:
            return GGUFExportResult(
                status="error",
                error=f"Unknown quant method: {quantize}. Available: {list(QUANT_METHODS)}",
            )

        # ── Method 1: llama.cpp convert_hf_to_gguf.py ──────────────────────
        if self._llama_cpp_dir:
            result = self._convert_via_llama_cpp(model_path, output_path, quantize, t0)
            if result.status == "success":
                self._write_model_info(
                    output_path, model_path, task_domain, quality_score, activate,
                    {"quant_method": quantize, "source": "convert_hf_to_gguf"},
                )
            return result

        # ── Method 2: gguf Python library ──────────────────────────────────
        result = self._convert_via_gguf_lib(model_path, output_path, quantize, t0)
        if result.status == "success":
            self._write_model_info(
                output_path, model_path, task_domain, quality_score, activate,
                {"quant_method": quantize, "source": "convert_hf_to_gguf"},
            )
        return result

    def _convert_via_llama_cpp(self, model_path: str, output_path: str,
                               quantize: str, t0: float) -> GGUFExportResult:
        """Use llama.cpp's built-in converter script."""
        convert_script = os.path.join(self._llama_cpp_dir, "convert_hf_to_gguf.py")
        quantize_exe = os.path.join(self._llama_cpp_dir, "llama-quantize")
        if os.name == "nt":
            quantize_exe += ".exe"

        if not os.path.exists(convert_script):
            return GGUFExportResult(
                status="error",
                error=f"convert_hf_to_gguf.py not found at {convert_script}. "
                      f"Clone: git clone https://github.com/ggerganov/llama.cpp",
            )

        # Step 1: Convert to FP16 GGUF
        fp16_path = output_path.replace(".gguf", "-fp16.gguf")

        logger.info(f"Converting {model_path} to FP16 GGUF...")
        try:
            result = subprocess.run(
                [sys.executable, convert_script, model_path,
                 "--outfile", fp16_path,
                 "--outtype", "f16"],
                capture_output=True, text=True, timeout=600,
            )
            if result.returncode != 0:
                return GGUFExportResult(
                    status="error",
                    error=f"Conversion failed: {result.stderr[:500]}",
                )
        except subprocess.TimeoutExpired:
            return GGUFExportResult(status="error", error="Conversion timed out (10 min)")
        except Exception as e:
            return GGUFExportResult(status="error", error=str(e))

        logger.info(f"FP16 model saved to {fp16_path}")

        # Step 2: Quantize (if requested)
        if quantize != "f16" and os.path.exists(quantize_exe):
            logger.info(f"Quantizing to {quantize}...")
            try:
                result = subprocess.run(
                    [quantize_exe, fp16_path, output_path, quantize],
                    capture_output=True, text=True, timeout=300,
                )
                if result.returncode != 0:
                    logger.warning(f"Quantization failed, keeping FP16: {result.stderr[:200]}")
                    os.rename(fp16_path, output_path)
                elif os.path.exists(fp16_path):
                    os.remove(fp16_path)  # Clean up FP16 intermediate
            except Exception as e:
                logger.warning(f"Quantization error, keeping FP16: {e}")
                os.rename(fp16_path, output_path)
        else:
            # No quantization or quantize tool not found
            if fp16_path != output_path:
                os.rename(fp16_path, output_path)

        elapsed = time.monotonic() - t0
        file_size = os.path.getsize(output_path) if os.path.exists(output_path) else 0

        return GGUFExportResult(
            status="success",
            output_path=output_path,
            input_model=model_path,
            quant_method=quantize,
            file_size_mb=round(file_size / (1024 * 1024), 1),
            conversion_time_seconds=round(elapsed, 1),
        )

    def _convert_via_gguf_lib(self, model_path: str, output_path: str,
                              quantize: str, t0: float) -> GGUFExportResult:
        """Use the gguf Python library for conversion (no llama.cpp needed)."""
        try:
            import gguf
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
        except ImportError:
            return GGUFExportResult(
                status="error",
                error="Neither llama.cpp nor gguf library found. "
                      "Install: pip install gguf  OR  clone llama.cpp",
            )

        logger.info(f"Converting {model_path} to GGUF via gguf library...")

        try:
            # Load model
            model = AutoModelForCausalLM.from_pretrained(
                model_path, torch_dtype=torch.float16,
                device_map="cpu", trust_remote_code=True,
            )
            tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)

            # Create GGUF writer
            os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

            gguf_writer = gguf.GGUFWriter(output_path, "companion_model")

            # Write metadata
            gguf_writer.add_architecture("llama")
            gguf_writer.add_context_length(4096)
            gguf_writer.add_embedding_length(model.config.hidden_size)
            gguf_writer.add_block_count(model.config.num_hidden_layers)
            gguf_writer.add_feed_forward_length(model.config.intermediate_size)
            gguf_writer.add_head_count(model.config.num_attention_heads)
            gguf_writer.add_file_type(1)  # FP16

            # Write tokenizer
            if hasattr(tokenizer, "get_vocab"):
                vocab = tokenizer.get_vocab()
                gguf_writer.add_token_list(list(vocab.keys()))

            # Write tensors
            state_dict = model.state_dict()
            for name, tensor in state_dict.items():
                # Convert to FP16
                tensor_fp16 = tensor.to(torch.float16).cpu().numpy()
                gguf_writer.add_tensor(name, tensor_fp16)

            gguf_writer.write_header_to_file()
            gguf_writer.write_kv_data_to_file()
            gguf_writer.write_tensors_to_file()
            gguf_writer.close()

            elapsed = time.monotonic() - t0
            file_size = os.path.getsize(output_path)

            return GGUFExportResult(
                status="success",
                output_path=output_path,
                input_model=model_path,
                quant_method="f16",
                file_size_mb=round(file_size / (1024 * 1024), 1),
                conversion_time_seconds=round(elapsed, 1),
            )

        except Exception as e:
            return GGUFExportResult(status="error", error=str(e))

    # ── LoRA merge before export ───────────────────────────────────────────

    def merge_lora_and_export(self, base_model: str, lora_adapter: str,
                              output_path: str,
                              quantize: str = RECOMMENDED_QUANT,
                              activate: bool = False,
                              quality_score: float = 0.0,
                              task_domain: str = "text") -> GGUFExportResult:
        """Merge a LoRA adapter into the base model, then export to GGUF.

        This is the standard workflow for exporting a QLoRA-trained companion model.

        Args:
            base_model: HuggingFace base model ID or path.
            lora_adapter: Path to the LoRA adapter (output of qlora_trainer.py).
            output_path: Output GGUF file path.
            quantize: Quantization method.

        Returns:
            GGUFExportResult.
        """
        t0 = time.monotonic()
        logger.info(f"Merging LoRA adapter {lora_adapter} into {base_model}")

        try:
            import torch
            from peft import PeftModel
            from transformers import AutoModelForCausalLM, AutoTokenizer

            # Load base model
            model = AutoModelForCausalLM.from_pretrained(
                base_model,
                torch_dtype=torch.float16,
                device_map="cpu",
                trust_remote_code=True,
            )

            # Load and merge LoRA
            model = PeftModel.from_pretrained(model, lora_adapter)
            model = model.merge_and_unload()

            # Save merged model to temp directory
            merged_dir = os.path.join(os.path.dirname(output_path), "merged_temp")
            model.save_pretrained(merged_dir)
            tokenizer = AutoTokenizer.from_pretrained(base_model, trust_remote_code=True)
            tokenizer.save_pretrained(merged_dir)

            logger.info(f"Merged model saved to {merged_dir}")

            # Convert to GGUF
            result = self.convert_hf_to_gguf(
                merged_dir,
                output_path,
                quantize,
                activate=activate,
                quality_score=quality_score,
                task_domain=task_domain,
            )
            if result.status == "success":
                self._write_model_info(
                    output_path,
                    base_model,
                    task_domain,
                    quality_score,
                    activate,
                    {
                        "quant_method": quantize,
                        "source": "merge_lora_and_export",
                        "lora_adapter": lora_adapter,
                    },
                )

            # Clean up temp directory
            import shutil
            shutil.rmtree(merged_dir, ignore_errors=True)

            return result

        except ImportError as e:
            return GGUFExportResult(status="error", error=f"Missing dependencies: {e}")
        except Exception as e:
            return GGUFExportResult(status="error", error=str(e))

    # ── llama.cpp launch script generator ──────────────────────────────────

    def generate_launch_script(self, gguf_path: str, output_dir: str = None) -> str:
        """Generate a launch script for the exported GGUF model.

        Creates a .bat (Windows) or .sh (Linux/macOS) file that starts
        llama-server with the correct parameters.
        """
        gguf_path = os.path.abspath(gguf_path)
        output_dir = output_dir or os.path.dirname(gguf_path)

        is_windows = os.name == "nt"
        script_name = "start_companion_model.bat" if is_windows else "start_companion_model.sh"
        script_path = os.path.join(output_dir, script_name)

        with open(script_path, "w") as f:
            if is_windows:
                mmproj = gguf_path.replace(".gguf", ".mmproj.gguf")
                mmproj_line = f'  --mmproj "{mmproj}" ^\n' if os.path.exists(mmproj) else ""
                f.write(f"""@echo off
chcp 65001 >nul
title OpenMegatron Companion Model
echo Starting companion model: {os.path.basename(gguf_path)}
echo.
echo API available at: http://127.0.0.1:1234/v1
echo.

llama-server.exe ^
  -m "{gguf_path}" ^
{mmproj_line}  -ngl 999 ^
  -c 8192 ^
  -fa ^
  --cache-type-k q4_0 ^
  --cache-type-v q4_0 ^
  --temp 0.2 ^
  --top-p 0.9 ^
  --host 127.0.0.1 ^
  --port 1234

pause
""")
            else:
                f.write(f"""#!/bin/bash
echo "Starting companion model: {os.path.basename(gguf_path)}"
echo "API available at: http://127.0.0.1:1234/v1"

llama-server \\
  -m "{gguf_path}" \\
  -ngl 999 \\
  -c 8192 \\
  -fa \\
  --cache-type-k q4_0 \\
  --cache-type-v q4_0 \\
  --temp 0.2 \\
  --top-p 0.9 \\
  --host 127.0.0.1 \\
  --port 1234
""")
            os.chmod(script_path, 0o755)

        logger.info(f"Launch script saved to {script_path}")
        return script_path


# ── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="GGUF exporter for companion models")
    sub = p.add_subparsers(dest="command", required=True)

    # convert: direct HuggingFace → GGUF
    convert_cmd = sub.add_parser("convert", help="Convert HuggingFace model to GGUF")
    convert_cmd.add_argument("--model-path", required=True)
    convert_cmd.add_argument("--output", required=True)
    convert_cmd.add_argument("--quantize", default=RECOMMENDED_QUANT,
                            choices=list(QUANT_METHODS))
    convert_cmd.add_argument("--activate", action="store_true")
    convert_cmd.add_argument("--quality-score", type=float, default=0.0)
    convert_cmd.add_argument("--task-domain", default="text", choices=["text", "vision", "both"])

    # merge: LoRA adapter → merged → GGUF
    merge_cmd = sub.add_parser("merge", help="Merge LoRA adapter and export GGUF")
    merge_cmd.add_argument("--base-model", required=True)
    merge_cmd.add_argument("--lora-adapter", required=True)
    merge_cmd.add_argument("--output", required=True)
    merge_cmd.add_argument("--quantize", default=RECOMMENDED_QUANT,
                          choices=list(QUANT_METHODS))
    merge_cmd.add_argument("--activate", action="store_true")
    merge_cmd.add_argument("--quality-score", type=float, default=0.0)
    merge_cmd.add_argument("--task-domain", default="text", choices=["text", "vision", "both"])

    # launch: generate launch script
    launch_cmd = sub.add_parser("launch", help="Generate launch script")
    launch_cmd.add_argument("--gguf-path", required=True)
    launch_cmd.add_argument("--output-dir")

    args = p.parse_args()
    exporter = GGUFExporter()

    if args.command == "convert":
        result = exporter.convert_hf_to_gguf(
            args.model_path,
            args.output,
            args.quantize,
            activate=args.activate,
            quality_score=args.quality_score,
            task_domain=args.task_domain,
        )
    elif args.command == "merge":
        result = exporter.merge_lora_and_export(
            args.base_model,
            args.lora_adapter,
            args.output,
            args.quantize,
            activate=args.activate,
            quality_score=args.quality_score,
            task_domain=args.task_domain,
        )
    elif args.command == "launch":
        path = exporter.generate_launch_script(args.gguf_path, args.output_dir)
        print(f"Launch script: {path}")
        sys.exit(0)

    print(json.dumps({
        "status": result.status,
        "output_path": result.output_path,
        "quant_method": result.quant_method,
        "file_size_mb": result.file_size_mb,
        "conversion_time_seconds": result.conversion_time_seconds,
        "error": result.error,
    }, indent=2, ensure_ascii=False))
