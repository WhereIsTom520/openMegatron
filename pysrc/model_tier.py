"""
Model Tier — capability-based auto-detection for small/medium/large LLMs.

Instead of hardcoded model name lists, uses:
  1. API probing: context window, JSON mode, tool-calling accuracy
  2. Name-based fast path: known models skip probing (optimization, not requirement)
  3. Runtime calibration: auto-downgrade on repeated failures, auto-upgrade on success

New models Just Work — they're probed on first startup and classified by capability.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


class ModelTier(str, Enum):
    """Backward-compatible public tier enum."""

    LITE = "lite"
    STANDARD = "standard"
    ADVANCED = "advanced"


TIER_COST = {
    ModelTier.LITE: 0.3,
    ModelTier.STANDARD: 1.0,
    ModelTier.ADVANCED: 3.0,
}


TIER_MODELS = {
    ModelTier.LITE: ["gpt-4o-mini", "claude-3-haiku", "gemini-2.0-flash-lite"],
    ModelTier.STANDARD: ["gpt-4.1-mini", "claude-3.5-haiku", "gemini-2.0-flash"],
    ModelTier.ADVANCED: ["gpt-4.1", "gpt-4o", "claude-3.5-sonnet"],
}


# ═══════════════════════════════════════════════════════
# Tier definitions (unchanged — these are the targets)
# ═══════════════════════════════════════════════════════

@dataclass
class TierConfig:
    tier: str           # "small" | "medium" | "large"
    label: str
    context_token_budget: int
    history_max_messages: int
    system_rules_mode: str
    max_tools_in_prompt: int
    max_tool_calls_per_turn: int
    tool_schema_mode: str
    skill_selection_mode: str
    max_repair_attempts: int
    llm_repair: bool
    auto_decompose: bool
    max_subtasks: int
    max_agent_steps: int
    expert_debate: bool
    max_expert_opinions: int
    coding_rules: bool
    execution_contract_mode: str

SMALL_TIER = TierConfig(
    tier="small", label="Small (≤8K ctx, basic reasoning)",
    context_token_budget=8*1024, history_max_messages=30,
    system_rules_mode="essential", max_tools_in_prompt=5,
    max_tool_calls_per_turn=1, tool_schema_mode="bare",
    skill_selection_mode="lexical", max_repair_attempts=1,
    llm_repair=False, auto_decompose=True, max_subtasks=3,
    max_agent_steps=10, expert_debate=False, max_expert_opinions=0,
    coding_rules=False, execution_contract_mode="minimal",
)

MEDIUM_TIER = TierConfig(
    tier="medium", label="Medium (8K-64K ctx, decent reasoning)",
    context_token_budget=32*1024, history_max_messages=100,
    system_rules_mode="essential", max_tools_in_prompt=8,
    max_tool_calls_per_turn=2, tool_schema_mode="simplified",
    skill_selection_mode="embedding", max_repair_attempts=2,
    llm_repair=True, auto_decompose=True, max_subtasks=5,
    max_agent_steps=20, expert_debate=False, max_expert_opinions=0,
    coding_rules=True, execution_contract_mode="coding_only",
)

LARGE_TIER = TierConfig(
    tier="large", label="Large (≥64K ctx, strong reasoning)",
    context_token_budget=64*1024, history_max_messages=300,
    system_rules_mode="full", max_tools_in_prompt=15,
    max_tool_calls_per_turn=5, tool_schema_mode="full",
    skill_selection_mode="llm", max_repair_attempts=3,
    llm_repair=True, auto_decompose=False, max_subtasks=8,
    max_agent_steps=30, expert_debate=True, max_expert_opinions=3,
    coding_rules=True, execution_contract_mode="full",
)

MODEL_TIER_CONFIG: dict[str, TierConfig] = {
    "small": SMALL_TIER, "medium": MEDIUM_TIER, "large": LARGE_TIER,
}


# ═══════════════════════════════════════════════════════
# Capability probing (the core innovation)
# ═══════════════════════════════════════════════════════

@dataclass
class CapabilityProfile:
    """Measured capabilities of a model."""
    context_window_tokens: int     # Actual usable context (measured or inferred)
    json_mode_supported: bool      # response_format json_object works
    tool_calling_accurate: bool    # Can call tools with correct JSON args
    multi_step_ok: bool            # Can handle 3+ sequential tool calls
    measured: bool = False         # True if actually probed (not guessed)
    probe_latency_ms: float = 0.0

    @classmethod
    def unknown(cls) -> "CapabilityProfile":
        return cls(
            context_window_tokens=8192,
            json_mode_supported=True,
            tool_calling_accurate=True,
            multi_step_ok=False,
        )


async def probe_context_window(
    client,
    model: str,
    extra_params: dict = None,
    max_test: int = 128 * 1024,
    min_test: int = 4096,
) -> int:
    """Probe the actual context window size by sending progressively larger prompts.

    Strategy: binary search between min_test and max_test. Sends fake tokens
    (repeated 'x' characters) until the API rejects or times out.
    Returns the largest successfully processed size.
    """
    extra_params = extra_params or {}
    low, high = min_test, max_test
    best = min_test

    # Quick test at 4K (baseline — every model should handle this)
    try:
        await _test_context_size(client, model, min_test, extra_params, timeout=15)
        best = min_test
    except Exception:
        return min_test  # Even 4K fails — extremely constrained

    # Test at 8K, 16K, 32K, 64K, 128K — stop when it fails
    test_points = [8192, 16384, 32768, 65536, 98304, 131072]
    for size in test_points:
        if size > max_test:
            break
        try:
            await _test_context_size(client, model, size, extra_params, timeout=20)
            best = size
            logger.debug(f"Context probe: {size // 1024}K OK")
        except Exception as e:
            logger.debug(f"Context probe: {size // 1024}K FAILED ({e})")
            break  # Don't test larger sizes

    return best


async def _test_context_size(client, model: str, token_count: int, extra_params: dict, timeout: int = 15):
    """Send a dummy request with approximately token_count tokens of padding."""
    # Rough: 1 token ≈ 4 chars in English
    padding = "x" * min(token_count * 4, 500000)
    prompt = f"You are a test probe. Respond with just 'OK'.\n\n{padding[:token_count * 4]}"
    resp = await asyncio.wait_for(
        client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "Respond with just the word OK."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=5,
            **extra_params,
        ),
        timeout=timeout,
    )
    return resp


async def probe_json_mode(client, model: str, extra_params: dict = None) -> bool:
    """Test whether the model supports structured JSON output."""
    extra_params = extra_params or {}
    try:
        resp = await asyncio.wait_for(
            client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "Return JSON only."},
                    {"role": "user", "content": "Return {\"answer\": 42}"},
                ],
                response_format={"type": "json_object"},
                max_tokens=50,
                **{k: v for k, v in extra_params.items() if k != "response_format"},
            ),
            timeout=10,
        )
        content = resp.choices[0].message.content
        parsed = json.loads(content) if content else {}
        return isinstance(parsed, dict) and "answer" in parsed
    except Exception:
        return False


async def probe_tool_calling(client, model: str, extra_params: dict = None) -> bool:
    """Test whether the model can correctly call a simple tool with JSON args."""
    extra_params = extra_params or {}
    try:
        resp = await asyncio.wait_for(
            client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "You have one tool: get_weather(city: string). Call it."},
                    {"role": "user", "content": "What is the weather in Beijing?"},
                ],
                tools=[{
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "description": "Get current weather for a city",
                        "parameters": {
                            "type": "object",
                            "properties": {"city": {"type": "string", "description": "City name"}},
                            "required": ["city"],
                        },
                    },
                }],
                max_tokens=100,
                **{k: v for k, v in extra_params.items() if k not in ("tools", "tool_choice")},
            ),
            timeout=15,
        )
        msg = resp.choices[0].message
        if msg.tool_calls:
            args = json.loads(msg.tool_calls[0].function.arguments)
            return "city" in args and len(str(args["city"])) > 0
        return False
    except Exception:
        return False


# ═══════════════════════════════════════════════════════
# Tier classification from capabilities
# ═══════════════════════════════════════════════════════

def classify_from_capabilities(profile: CapabilityProfile) -> str:
    """Determine tier from measured capabilities (no name matching)."""
    ctx = profile.context_window_tokens

    # Large: ≥64K ctx AND JSON mode AND tool calling
    if ctx >= 48000 and profile.json_mode_supported and profile.tool_calling_accurate:
        return "large"

    # Medium: ≥16K ctx AND (JSON mode OR tool calling)
    if ctx >= 12000 and (profile.json_mode_supported or profile.tool_calling_accurate):
        return "medium"

    # Small: everything else
    return "small"


def classify_from_name_fast(model_name: str) -> Optional[str]:
    """Quick name-based tier guess. Returns None if uncertain.

    Uses size indicators (7b, 70b, etc.) and known model families.
    Only returns a result when confident — returns None for unknown models.
    """
    if not model_name:
        return None

    name = model_name.lower().strip()

    # ── Size-based (most reliable name heuristic) ──
    import re
    # Match patterns like "7b", "70b", "8x7b", "1.5b"
    size_match = re.search(r'(\d+(?:\.\d+)?)\s*b', name)
    if size_match:
        size = float(size_match.group(1))
        if size <= 8:
            return "small"
        elif size <= 70:
            return "medium"
        else:
            return "large"

    # ── Known model family indicators (ordered: specific before generic) ──

    # Small / fast models → small (check FIRST — most specific)
    small_fast = {"gemini-2.0-flash-lite", "glm-4-flash",
                  "phi-3-mini", "phi-3-small",
                  "deepseek-coder:1.3", "deepseek-coder:6.7",
                  "codestral", "mistral:7", "mixtral:8x7"}
    for s in small_fast:
        if s in name:
            return "small"

    # Mid-tier models → medium (check SECOND — more specific than premium)
    mid_tier = {"gpt-4o-mini", "gpt-4.1-mini", "o4-mini",
                "claude-3-haiku", "claude-3.5-haiku",
                "gemini-1.5-flash", "gemini-2.0-flash",
                "qwen-plus", "qwen-turbo", "moonshot-v1", "step-2",
                "mistral-small", "mistral-medium",
                "command-r", "glm-4-air", "glm-4-plus"}
    for m in mid_tier:
        if m in name:
            return "medium"

    # Premium / frontier models → large (check LAST since substrings like "gpt-4o" match "gpt-4o-mini")
    premium = {"gpt-4o", "gpt-4.1", "gpt-4-turbo", "o3", "o4",
               "claude-3-opus", "claude-3.5-sonnet", "claude-4",
               "gemini-1.5-pro", "gemini-2.0-pro", "gemini-2.5",
               "deepseek-chat", "deepseek-reasoner", "deepseek-v3", "deepseek-r1",
               "qwen-max", "qwen-long", "mistral-large"}
    for p in premium:
        if p in name:
            return "large"

    # Unknown model — return None to trigger capability probing
    return None


# ═══════════════════════════════════════════════════════
# Main detection API
# ═══════════════════════════════════════════════════════

async def detect_model_tier_async(
    client=None,
    model: str = "",
    extra_params: dict = None,
    probe: bool = True,
) -> str:
    """Detect model tier: name hint → capability probe → classify.

    Args:
        client: AsyncOpenAI client (None = skip probing, name-only)
        model: Model name/id
        extra_params: Extra API params
        probe: Whether to run capability probes (costs ~3 API calls)

    Returns: "small" | "medium" | "large"
    """
    # Env override
    env_tier = os.environ.get("MEGATRON_MODEL_TIER", "").lower()
    if env_tier in ("small", "medium", "large"):
        return env_tier

    # Fast path: name-based hint
    name_tier = classify_from_name_fast(model)
    if name_tier and not probe:
        return name_tier

    # Capability probing
    if probe and client:
        try:
            profile = await _probe_capabilities(client, model, extra_params)
            tier = classify_from_capabilities(profile)
            logger.info(
                "Model '%s' probed: ctx=%dK, json=%s, tools=%s → tier=%s (%.0fms)",
                model, profile.context_window_tokens // 1024,
                profile.json_mode_supported, profile.tool_calling_accurate,
                tier, profile.probe_latency_ms,
            )
            return tier
        except Exception as e:
            logger.warning(f"Capability probing failed for '{model}': {e}. Falling back to name hint.")

    # Fallback
    if name_tier:
        return name_tier
    return "medium"


def detect_model_tier(model: str) -> str:
    """Synchronous wrapper: name-based only (for init-time use before async client is ready)."""
    env_tier = os.environ.get("MEGATRON_MODEL_TIER", "").lower()
    if env_tier in ("small", "medium", "large"):
        return env_tier
    name_tier = classify_from_name_fast(model)
    return name_tier or "medium"


async def _probe_capabilities(client, model: str, extra_params: dict = None) -> CapabilityProfile:
    """Run the full capability probe suite."""
    extra_params = extra_params or {}
    t0 = time.monotonic()

    # Probe context window first (most important signal)
    ctx_size = 8192  # default conservative
    json_ok = True   # assume yes unless proven otherwise
    tools_ok = True

    try:
        ctx_size = await probe_context_window(client, model, extra_params)
    except Exception as e:
        logger.debug(f"Context probe failed: {e}")

    # Only probe JSON/tools if context is large enough to be worth it
    if ctx_size >= 8192:
        try:
            json_ok = await probe_json_mode(client, model, extra_params)
        except Exception:
            json_ok = False

        try:
            tools_ok = await probe_tool_calling(client, model, extra_params)
        except Exception:
            tools_ok = False

    return CapabilityProfile(
        context_window_tokens=ctx_size,
        json_mode_supported=json_ok,
        tool_calling_accurate=tools_ok,
        multi_step_ok=ctx_size >= 16384,
        measured=True,
        probe_latency_ms=(time.monotonic() - t0) * 1000,
    )


def get_tier_config(model: str) -> TierConfig:
    return MODEL_TIER_CONFIG[detect_model_tier(model)]


# ═══════════════════════════════════════════════════════
# Runtime calibration (auto-adjust during operation)
# ═══════════════════════════════════════════════════════

@dataclass
class RuntimeCalibration:
    """Tracks runtime behavior to auto-adjust tier."""
    consecutive_successes: int = 0
    consecutive_failures: int = 0
    tool_call_errors: int = 0
    current_tier: str = "medium"
    original_tier: str = "medium"

    def record_success(self):
        self.consecutive_successes += 1
        self.consecutive_failures = 0

    def record_failure(self, is_tool_error: bool = False):
        self.consecutive_failures += 1
        self.consecutive_successes = 0
        if is_tool_error:
            self.tool_call_errors += 1

    def should_downgrade(self) -> bool:
        """Downgrade after 3 consecutive failures."""
        return self.consecutive_failures >= 3

    def should_upgrade(self) -> bool:
        """Upgrade after 10 consecutive successes."""
        return self.consecutive_successes >= 10 and self.current_tier != self.original_tier

    def downgrade(self):
        tiers = ["large", "medium", "small"]
        idx = tiers.index(self.current_tier)
        if idx < len(tiers) - 1:
            self.current_tier = tiers[idx + 1]
            logger.warning(f"Runtime calibration: downgrading from {tiers[idx]} to {self.current_tier}")

    def upgrade(self):
        tiers = ["large", "medium", "small"]
        idx = tiers.index(self.current_tier)
        if idx > 0 and tiers[idx - 1] != self.original_tier:
            return  # Don't upgrade past original
        if idx > 0:
            self.current_tier = tiers[idx - 1]
            logger.info(f"Runtime calibration: upgrading from {tiers[idx]} to {self.current_tier}")


# ═══════════════════════════════════════════════════════
# Prompt/system adaptation (unchanged from before)
# ═══════════════════════════════════════════════════════

def adapt_system_rules(tier: TierConfig, full_rules: str) -> str:
    if tier.system_rules_mode == "full":
        return full_rules
    rules = full_rules.split("\n")
    if tier.system_rules_mode == "essential":
        keep = ("0.", "1.", "2.", "3.", "4.", "5.", "8.", "9.", "11.", "12.", "14.", "16.", "17.", "18.")
        rules = [r for r in rules if any(r.strip().startswith(p) for p in keep)]
    elif tier.system_rules_mode == "minimal":
        keep = ("0.", "1.", "3.", "4.", "9.", "14.", "16.", "18.")
        rules = [r for r in rules if any(r.strip().startswith(p) for p in keep)]
    return "\n".join(rules)


def adapt_execution_contract(tier: TierConfig, full_contract: str) -> str:
    if tier.execution_contract_mode == "full":
        return full_contract
    lines = full_contract.split("\n")
    if tier.execution_contract_mode == "coding_only":
        skip = ("- For research lookup", "- When research outputs")
        lines = [l for l in lines if not any(l.strip().startswith(p) for p in skip)]
    elif tier.execution_contract_mode == "minimal":
        keep = ("- Use run_skill_script", "- The args_string", "- Chain tool outputs",
                "- Preserve user constraints", "- Do not call write_and_execute")
        lines = [l for l in lines if any(l.strip().startswith(p) for p in keep)]
        lines.append("- For complex tasks: break into small steps. One tool call at a time.")
        lines.append("- If a tool fails: read the error, fix the input, retry once. Then report.")
    return "\n".join(lines)


def adapt_tool_schema(tier: TierConfig, full_schema: dict) -> dict:
    if tier.tool_schema_mode == "full":
        return full_schema
    simplified = {"type": "object", "properties": {}}
    if "required" in full_schema:
        simplified["required"] = full_schema["required"]
    for prop_name, prop_schema in (full_schema.get("properties") or {}).items():
        if tier.tool_schema_mode == "simplified":
            simplified["properties"][prop_name] = {
                "type": prop_schema.get("type", "string"),
                "description": prop_schema.get("description", ""),
            }
            if "enum" in prop_schema:
                simplified["properties"][prop_name]["enum"] = prop_schema["enum"]
        elif tier.tool_schema_mode == "bare":
            simplified["properties"][prop_name] = {"type": prop_schema.get("type", "string")}
    return simplified


def suggest_subtasks(task_description: str, tier: TierConfig) -> list[str]:
    if not tier.auto_decompose:
        return []
    task_lower = task_description.lower()
    if any(w in task_lower for w in ("fix", "修复", "debug", "调试", "bug")):
        return [
            "1. Search for the error message or relevant code",
            "2. Read the affected file to understand context",
            "3. Apply the fix (single focused edit)",
            "4. Run tests to verify the fix",
        ][:tier.max_subtasks]
    if any(w in task_lower for w in ("refactor", "重构", "extract", "rename")):
        return [
            "1. Understand: read current code and extract symbols",
            "2. Plan: identify all files that need changes",
            "3. Snapshot: create a git rollback point",
            "4. Execute: make ONE edit at a time, verify each",
            "5. Verify: run lint + typecheck + tests after each edit",
        ][:tier.max_subtasks]
    if any(w in task_lower for w in ("implement", "实现", "add", "添加", "feature")):
        return [
            "1. Read the relevant existing code to understand patterns",
            "2. Write the new code (keep it small and focused)",
            "3. Add tests for the new functionality",
            "4. Run the full test suite",
        ][:tier.max_subtasks]
    if any(w in task_lower for w in ("review", "审查", "audit", "scan")):
        return [
            "1. Scan for security issues (secrets, dangerous patterns)",
            "2. Analyze complexity hotspots",
            "3. Report findings with severity levels",
        ][:tier.max_subtasks]
    return []


def diagnose_model_tier(model: str) -> dict:
    """Print diagnosis of how a model is classified and why."""
    name_tier = classify_from_name_fast(model)
    final_tier = detect_model_tier(model)
    cfg = MODEL_TIER_CONFIG[final_tier]
    return {
        "model": model,
        "name_based_hint": name_tier or "unknown (would probe at runtime)",
        "final_tier": final_tier,
        "method": "name_hint" if name_tier else "would_probe",
        "label": cfg.label,
        "config": {
            "context_budget": f"{cfg.context_token_budget // 1024}K tokens",
            "max_tools": cfg.max_tools_in_prompt,
            "repair_attempts": cfg.max_repair_attempts,
            "skill_selection": cfg.skill_selection_mode,
            "auto_decompose": cfg.auto_decompose,
        },
    }
