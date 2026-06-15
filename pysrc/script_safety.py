"""Script safety checker extracted from agent.py.

Sanitizes LLM-generated scripts, validates dependencies, detects risks.
"""
from __future__ import annotations

import re
from typing import List, Tuple


class ScriptSafetyChecker:
    """Checks LLM-generated scripts for safety issues."""

    def __init__(self, agent=None):
        self._agent = agent

    # ── Script sanitization ──────────────────────────────────────────────

    @staticmethod
    def sanitize_script(code: str) -> str:
        """Remove dangerous patterns from generated scripts."""
        if not code:
            return ""
        # Remove shebang lines
        code = re.sub(r'^#!.*$', '', code, flags=re.MULTILINE)
        # Warn about but don't remove os.system / subprocess
        return code.strip()

    @staticmethod
    def normalize_generated_skill_name(name: str) -> str:
        """Convert an LLM-generated name into a safe skill identifier."""
        safe = re.sub(r'[^a-zA-Z0-9_\-]', '_', str(name or "").strip().lower())
        safe = re.sub(r'_+', '_', safe).strip('_')
        return safe or "generated_skill"

    @staticmethod
    def strip_code_fence(text: str) -> str:
        """Remove markdown code fences from LLM output."""
        text = str(text or "").strip()
        # Remove ```python ... ``` wrappers
        text = re.sub(r'^```\w*\s*\n', '', text)
        text = re.sub(r'\n```\s*$', '', text)
        return text.strip()

    @staticmethod
    def normalize_dependency_spec(spec: str) -> str:
        """Normalize a pip dependency specification."""
        spec = str(spec or "").strip().lower()
        # Remove version constraints for risk checking
        spec = re.sub(r'[<>=!~].*$', '', spec)
        return spec.strip()

    @staticmethod
    def heuristic_dependency_risk(package_name: str) -> Tuple[str, str]:
        """Quick heuristic check for suspicious packages.

        Returns (risk_level, reason).
        """
        name = package_name.lower().strip()

        # Known safe packages
        safe_patterns = [
            r'^numpy$', r'^pandas$', r'^requests$', r'^flask$', r'^fastapi$',
            r'^pydantic$', r'^pillow$', r'^matplotlib$', r'^scikit', r'^scipy$',
            r'^beautifulsoup', r'^lxml$', r'^pyyaml$', r'^toml', r'^click$',
            r'^rich$', r'^tqdm$', r'^httpx$', r'^aiohttp', r'^websocket',
            r'^openpyxl$', r'^python-pptx$', r'^pypdf', r'^redis$', r'^psutil$',
        ]
        for pattern in safe_patterns:
            if re.match(pattern, name):
                return ("low", "Common trusted package")

        # Suspicious patterns
        if re.search(r'(crypto|wallet|miner|steal|hack|exploit|bypass|inject)', name):
            return ("high", "Package name contains suspicious keywords")
        if re.search(r'^py-.*-(tool|util|lib)$', name):
            return ("medium", "Generic-sounding package name")
        if len(name) < 3:
            return ("medium", "Very short package name")

        return ("low", "No obvious risk indicators")

    @staticmethod
    def levenshtein_distance(s1: str, s2: str) -> int:
        """Compute edit distance between two strings."""
        if len(s1) < len(s2):
            return ScriptSafetyChecker.levenshtein_distance(s2, s1)
        if len(s2) == 0:
            return len(s1)
        prev = list(range(len(s2) + 1))
        for i, c1 in enumerate(s1):
            curr = [i + 1]
            for j, c2 in enumerate(s2):
                curr.append(min(
                    prev[j + 1] + 1,
                    curr[j] + 1,
                    prev[j] + (0 if c1 == c2 else 1),
                ))
            prev = curr
        return prev[-1]

    # ── Fallback helpers ────────────────────────────────────────────────

    @staticmethod
    def fallback_promoted_skill_code(skill_name: str, description: str) -> str:
        """Generate minimal boilerplate for a promoted script skill."""
        return f'''#!/usr/bin/env python3
"""{description}"""
import json, sys

def main():
    if len(sys.argv) < 2:
        print(json.dumps({{"error": "Missing args"}}))
        sys.exit(1)
    try:
        params = json.loads(sys.argv[1])
    except json.JSONDecodeError:
        print(json.dumps({{"error": "Invalid JSON"}}))
        sys.exit(1)

    # TODO: implement {skill_name}
    result = {{"status": "success", "message": "Skill executed"}}
    print(json.dumps(result, ensure_ascii=False))

if __name__ == "__main__":
    main()
'''

    @staticmethod
    def fallback_promoted_skill_definition(skill_name: str, description: str) -> dict:
        """Generate minimal SKILL.md metadata for a promoted skill."""
        return {
            "name": skill_name,
            "version": "1.0.0",
            "category": "agent",
            "description": description,
            "risk": "medium",
            "actions": ["execute"],
            "keywords": [skill_name],
            "parameters": {
                "action": {"type": "string", "enum": ["execute"], "required": True},
            },
        }
