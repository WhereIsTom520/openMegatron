"""Prompt builder extracted from agent.py.

Builds static system prompt and dynamic system prompt for the agent loop.
"""
from __future__ import annotations

from typing import Any, Dict, List


class PromptBuilder:
    """Builds system prompts for the agent."""

    def __init__(self, agent):
        self._agent = agent

    @property
    def domain_meta(self):
        return self._agent.domain_meta

    @property
    def runtime(self):
        return self._agent.runtime

    @property
    def tier_config(self):
        return self._agent.tier_config

    def build_static_system_prompt(self, domain: str) -> str:
        """Build the static system prompt from domain config."""
        meta = self.domain_meta.get(domain, self.domain_meta.get("general", {}))
        return str(meta.get("system_prompt_template", "You are a helpful AI assistant."))

    def build_dynamic_system_prompt(self, allowed_paths_str: str,
                                    core_mem_str: str, past_patterns_str: str,
                                    skill_instructions: str, plan_hint: str) -> str:
        """Build the dynamic system prompt with current context."""
        parts = []

        if allowed_paths_str:
            parts.append(f"Allowed paths: {allowed_paths_str}")

        if core_mem_str and core_mem_str.strip():
            parts.append(f"Core memory:\n{core_mem_str}")

        if past_patterns_str and past_patterns_str.strip():
            parts.append(f"Past workflow patterns:\n{past_patterns_str}")

        if skill_instructions:
            parts.append(f"Available skills:\n{skill_instructions}")

        if plan_hint and plan_hint.strip():
            parts.append(f"Execution plan:\n{plan_hint}")

        # Add execution contract
        runtime = getattr(self, 'runtime', None)
        tier = getattr(self, 'tier_config', None)
        contract = "Complete the task using the available tools. Be thorough but efficient."
        if tier and hasattr(tier, 'execution_contract_mode'):
            if tier.execution_contract_mode == "minimal":
                contract = "Use minimum tool calls. Be concise."
            elif tier.execution_contract_mode == "full":
                contract = "Plan thoroughly. Use all necessary tools. Verify results."

        parts.append(f"Execution Contract: {contract}")
        return "\n\n".join(parts)
