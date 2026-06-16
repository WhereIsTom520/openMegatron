"""Companion Model — load and run small models for agent inference.

Replaces the large cloud model (GPT-4/External Agent) with a locally fine-tuned
small model (Holo 3.1, Qwen 2.5, Llama 3.2, etc.) for specific task domains.

Supports three backends:
  1. llama.cpp (via HTTP API — OpenAI compatible)
  2. transformers (HuggingFace pipeline — direct Python)
  3. vLLM (high-throughput serving — OpenAI compatible)

The companion model is NOT loaded by default. It is only activated when:
  - A trained checkpoint exists in .models/companion/
  - The task domain matches (text, vision, or auto-detect)
  - The model passes quality gates (RegressionGuard)
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

# ── Model Registry ───────────────────────────────────────────────────────────


@dataclass
class CompanionModelInfo:
    """Metadata for a trained companion model checkpoint."""
    model_id: str
    model_path: str
    backend: str  # "llama_cpp", "transformers", "vllm"
    base_model: str  # e.g. "Holo-3.1-4B", "Qwen2.5-7B"
    task_domain: str  # "text", "vision", "both"
    f1_score: float = 0.0
    accuracy: float = 0.0
    n_samples: int = 0
    created_at: str = ""
    is_active: bool = False
    metadata: dict = field(default_factory=dict)


# ── Companion Model Loader ──────────────────────────────────────────────────


class CompanionModelLoader:
    """Load and manage companion model instances.

    Usage:
        loader = CompanionModelLoader()
        model = loader.load("path/to/checkpoint", backend="transformers")
        response = model.generate("Hello, what can you do?")
    """

    def __init__(self, models_dir: str = ".models/companion"):
        self._models_dir = Path(models_dir)
        self._models_dir.mkdir(parents=True, exist_ok=True)
        self._loaded_model = None
        self._loaded_tokenizer = None
        self._loaded_backend = None
        self._active_info: Optional[CompanionModelInfo] = None

    # ── Discovery ──────────────────────────────────────────────────────────

    def discover_models(self) -> List[CompanionModelInfo]:
        """Scan the models directory for trained checkpoints."""
        models = []
        if not self._models_dir.exists():
            return models

        candidate_dirs = [self._models_dir]
        candidate_dirs.extend(
            child for child in sorted(self._models_dir.iterdir(), reverse=True)
            if child.is_dir()
        )
        seen = set()

        for checkpoint_dir in candidate_dirs:
            if not checkpoint_dir.is_dir():
                continue

            info_path = checkpoint_dir / "model_info.json"
            if info_path.exists():
                try:
                    info = self._load_model_info(info_path, checkpoint_dir)
                    if info.model_id not in seen:
                        models.append(info)
                        seen.add(info.model_id)
                    continue
                except Exception as e:
                    logger.debug(f"Failed to load model info from {checkpoint_dir}: {e}")

            # Also detect by file extension
            pt_files = list(checkpoint_dir.glob("*.pt")) + list(checkpoint_dir.glob("*.pth"))
            safetensors = list(checkpoint_dir.glob("*.safetensors"))
            gguf_files = list(checkpoint_dir.glob("*.gguf"))
            config_json = list(checkpoint_dir.glob("config.json"))

            if pt_files or safetensors or config_json:
                backend = "transformers"
            elif gguf_files:
                backend = "llama_cpp"
            else:
                continue

            model_id = checkpoint_dir.name
            # Check if already added via model_info.json
            if model_id in seen:
                continue

            models.append(CompanionModelInfo(
                model_id=model_id,
                model_path=str(checkpoint_dir),
                backend=backend,
                base_model="unknown",
                task_domain="text",
                is_active=False,
            ))
            seen.add(model_id)

        return models

    def _load_model_info(self, info_path: Path, checkpoint_dir: Path) -> CompanionModelInfo:
        with open(info_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("model_id", checkpoint_dir.name)
        data.setdefault("model_path", str(checkpoint_dir))
        data.setdefault("backend", self._detect_backend(data["model_path"]))
        data.setdefault("base_model", "unknown")
        data.setdefault("task_domain", "text")
        allowed = CompanionModelInfo.__dataclass_fields__.keys()
        return CompanionModelInfo(**{k: v for k, v in data.items() if k in allowed})

    def get_best_model(self, task_domain: str = "text",
                       min_f1: float = 0.6) -> Optional[CompanionModelInfo]:
        """Find the best active model for a given task domain."""
        models = self.discover_models()
        candidates = [
            m for m in models
            if m.is_active
            and m.task_domain in (task_domain, "both")
            and m.f1_score >= min_f1
        ]
        if not candidates:
            # Fall back to any model with sufficient F1
            candidates = [
                m for m in models
                if m.task_domain in (task_domain, "both")
                and m.f1_score >= min_f1
            ]
        candidates.sort(key=lambda m: m.f1_score, reverse=True)
        return candidates[0] if candidates else None

    # ── Loading ────────────────────────────────────────────────────────────

    def load(self, model_path: str = None, backend: str = None,
             task_domain: str = "text") -> Optional[Any]:
        """Load a companion model for inference.

        Args:
            model_path: Path to checkpoint. If None, auto-discovers best model.
            backend: "llama_cpp", "transformers", or "vllm".
            task_domain: "text" or "vision".

        Returns:
            A callable model object, or None if no model is available.
        """
        # Auto-discover if no path given
        if model_path is None:
            best = self.get_best_model(task_domain)
            if best is None:
                logger.info(f"No companion model found for domain={task_domain}")
                return None
            model_path = best.model_path
            backend = best.backend

        if backend is None:
            backend = self._detect_backend(model_path)

        if backend == "llama_cpp":
            return self._load_llama_cpp(model_path)
        elif backend == "transformers":
            return self._load_transformers(model_path)
        elif backend == "vllm":
            return self._load_vllm_client(model_path)
        else:
            logger.error(f"Unknown backend: {backend}")
            return None

    def unload(self):
        """Free GPU memory by unloading the current model."""
        self._loaded_model = None
        self._loaded_tokenizer = None
        self._loaded_backend = None
        self._active_info = None
        import gc
        gc.collect()
        try:
            import torch
            torch.cuda.empty_cache()
        except ImportError:
            pass
        logger.info("Companion model unloaded")

    def is_loaded(self) -> bool:
        return self._loaded_model is not None

    # ── Backend-specific loading ────────────────────────────────────────────

    def _detect_backend(self, path: str) -> str:
        p = Path(path)
        if p.is_file() and p.suffix.lower() == ".gguf":
            return "llama_cpp"
        if p.is_dir() and list(p.glob("*.gguf")):
            return "llama_cpp"
        if p.is_dir() and (
            list(p.glob("*.pt")) or list(p.glob("*.safetensors")) or list(p.glob("config.json"))
        ):
            return "transformers"
        return "llama_cpp"

    def _load_llama_cpp(self, path: str):
        """Connect to a llama.cpp server instance.

        llama.cpp must be running separately, e.g.:
          llama-server -m model.gguf --port 1234
        """
        try:
            from openai import OpenAI

            # Default: assume llama.cpp is running on localhost:1234
            base_url = os.environ.get("LLAMA_CPP_URL", "http://127.0.0.1:1234/v1")
            client = OpenAI(base_url=base_url, api_key="not-needed")

            # Test connection
            models = client.models.list()
            model_name = models.data[0].id if models.data else "local-model"

            self._loaded_model = client
            self._loaded_backend = "llama_cpp"
            self._active_info = CompanionModelInfo(
                model_id=Path(path).name,
                model_path=path,
                backend="llama_cpp",
                base_model=model_name,
                task_domain="text",
                is_active=True,
            )
            logger.info(f"Connected to llama.cpp: {model_name} at {base_url}")
            return client

        except ImportError:
            logger.error("openai package required for llama_cpp backend. pip install openai")
            return None
        except Exception as e:
            logger.warning(f"llama.cpp connection failed: {e}")
            return None

    def _load_transformers(self, path: str):
        """Load a model directly via HuggingFace transformers."""
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer

            logger.info(f"Loading transformers model from {path}...")
            t0 = time.monotonic()

            # Try 4-bit quantization first (saves VRAM)
            try:
                from transformers import BitsAndBytesConfig
                bnb_config = BitsAndBytesConfig(
                    load_in_4bit=True,
                    bnb_4bit_quant_type="nf4",
                    bnb_4bit_compute_dtype=torch.bfloat16,
                )
                model = AutoModelForCausalLM.from_pretrained(
                    path,
                    quantization_config=bnb_config,
                    device_map="auto",
                    trust_remote_code=True,
                )
            except (ImportError, Exception):
                # Fall back to fp16
                model = AutoModelForCausalLM.from_pretrained(
                    path,
                    torch_dtype=torch.float16,
                    device_map="auto",
                    trust_remote_code=True,
                )

            tokenizer = AutoTokenizer.from_pretrained(path, trust_remote_code=True)
            if tokenizer.pad_token is None:
                tokenizer.pad_token = tokenizer.eos_token

            elapsed = time.monotonic() - t0
            self._loaded_model = model
            self._loaded_tokenizer = tokenizer
            self._loaded_backend = "transformers"
            self._active_info = CompanionModelInfo(
                model_id=Path(path).name,
                model_path=path,
                backend="transformers",
                base_model=str(path),
                task_domain="text",
                is_active=True,
            )
            logger.info(f"Loaded transformers model in {elapsed:.1f}s: {path}")
            return model

        except ImportError as e:
            logger.error(f"Missing dependencies: {e}. pip install torch transformers accelerate")
            return None
        except Exception as e:
            logger.error(f"Failed to load model: {e}")
            return None

    def _load_vllm_client(self, path: str):
        """Connect to a vLLM server instance."""
        try:
            from openai import OpenAI
            base_url = os.environ.get("VLLM_URL", "http://127.0.0.1:8000/v1")
            client = OpenAI(base_url=base_url, api_key="not-needed")
            models = client.models.list()
            model_name = models.data[0].id if models.data else "vllm-model"

            self._loaded_model = client
            self._loaded_backend = "vllm"
            logger.info(f"Connected to vLLM: {model_name}")
            return client
        except Exception as e:
            logger.warning(f"vLLM connection failed: {e}")
            return None

    # ── Inference ───────────────────────────────────────────────────────────

    def generate(self, messages: List[dict], tools: List[dict] = None,
                 max_tokens: int = 1024, temperature: float = 0.7) -> str:
        """Generate a response from the companion model.

        Args:
            messages: OpenAI-format message list.
            tools: Optional tool definitions for function calling.
            max_tokens: Maximum tokens to generate.
            temperature: Sampling temperature.

        Returns:
            Generated text string.
        """
        if self._loaded_model is None:
            return ""

        if self._loaded_backend in ("llama_cpp", "vllm"):
            # OpenAI-compatible API
            kwargs = {
                "model": "local-model",
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"

            try:
                resp = self._loaded_model.chat.completions.create(**kwargs)
                msg = resp.choices[0].message

                # Handle tool calls
                if msg.tool_calls:
                    return json.dumps({
                        "tool_calls": [
                            {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            }
                            for tc in msg.tool_calls
                        ]
                    }, ensure_ascii=False)

                return msg.content or ""

            except Exception as e:
                logger.error(f"Companion model inference failed: {e}")
                return ""

        elif self._loaded_backend == "transformers":
            # Direct transformers inference
            import torch

            # Build prompt from messages
            prompt = self._build_prompt(messages)
            if tools:
                prompt += "\n\nAvailable tools:\n" + json.dumps(tools, indent=2, ensure_ascii=False)
                prompt += "\n\nRespond with a JSON tool call if needed."

            inputs = self._loaded_tokenizer(
                prompt, return_tensors="pt", truncation=True,
                max_length=4096,
            ).to(self._loaded_model.device)

            with torch.no_grad():
                outputs = self._loaded_model.generate(
                    **inputs,
                    max_new_tokens=max_tokens,
                    temperature=temperature,
                    do_sample=temperature > 0,
                    pad_token_id=self._loaded_tokenizer.pad_token_id,
                )

            response = self._loaded_tokenizer.decode(
                outputs[0][inputs["input_ids"].shape[1]:],
                skip_special_tokens=True,
            )
            return response.strip()

        return ""

    def _build_prompt(self, messages: List[dict]) -> str:
        """Build a text prompt from messages for transformers backend."""
        parts = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            if isinstance(content, list):
                # Multimodal content — extract text parts only
                texts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        texts.append(block.get("text", ""))
                    elif isinstance(block, str):
                        texts.append(block)
                content = " ".join(texts)

            if role == "system":
                parts.append(f"<|system|>\n{content}</s>")
            elif role == "user":
                parts.append(f"<|user|>\n{content}</s>")
            elif role == "assistant":
                parts.append(f"<|assistant|>\n{content}</s>")
            elif role == "tool":
                parts.append(f"<|tool|>\n{content}</s>")

        parts.append("<|assistant|>\n")
        return "\n".join(parts)

    # ── Model Info ──────────────────────────────────────────────────────────

    def save_model_info(self, model_path: str, info: CompanionModelInfo):
        """Save model metadata to model_info.json."""
        info_dir = Path(model_path)
        if info_dir.suffix:
            info_dir = info_dir.parent
        info_dir.mkdir(parents=True, exist_ok=True)
        info_path = info_dir / "model_info.json"
        with open(info_path, "w", encoding="utf-8") as f:
            json.dump({
                "model_id": info.model_id,
                "model_path": info.model_path,
                "backend": info.backend,
                "base_model": info.base_model,
                "task_domain": info.task_domain,
                "f1_score": info.f1_score,
                "accuracy": info.accuracy,
                "n_samples": info.n_samples,
                "created_at": info.created_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "is_active": info.is_active,
                "metadata": info.metadata,
            }, f, indent=2, ensure_ascii=False)

    def set_active(self, model_path: str, active: bool = True):
        """Activate or deactivate a companion model."""
        info_dir = Path(model_path)
        if info_dir.suffix:
            info_dir = info_dir.parent
        info_path = info_dir / "model_info.json"
        if info_path.exists():
            with open(info_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            data["is_active"] = active
            with open(info_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            logger.info(f"Model {info_dir.name}: is_active={active}")
