"""Unified validator for all skill categories.

Principle: one set of general validators that handle 90% of failure modes
across research, code, media, and monitoring skills. Category-specific
edge cases ("white horse, not a horse") are appended as optional extras.

Failure mode taxonomy (applies universally):

  EMPTY/ABSENT     → result is None, [], {}, or empty string
  STRUCTURAL       → missing required fields, wrong types
  QUALITY          → too few results, suspicious values
  EXECUTION        → exceptions, timeouts, resource exhaustion
  INTEGRITY        → broken files, corrupt data, CLI not found

Each skill also reports its OWN category via the context dict,
so the validator can skip checks that don't apply to that category.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from repair_hook import RepairIssue, Validator


# ── Helpers ──────────────────────────────────────────────────────────────────

def _cat(context: dict) -> str:
    """Extract category from context, defaulting to 'general'."""
    return (context or {}).get("skill_category") or "general"


# ── Unified Validators ───────────────────────────────────────────────────────

async def validate_not_empty(result: Any, context: dict = None) -> List[RepairIssue]:
    """Universal: result is None or an empty collection."""
    if result is None:
        return [RepairIssue(
            severity="error",
            message="No result returned (None)",
            category="empty_result",
        )]
    if isinstance(result, (list, tuple)) and len(result) == 0:
        return [RepairIssue(
            severity="error",
            message="Empty result list",
            category="empty_result",
        )]
    if isinstance(result, dict) and not result:
        return [RepairIssue(
            severity="error",
            message="Empty result dict",
            category="empty_result",
        )]
    if isinstance(result, str) and not result.strip():
        return [RepairIssue(
            severity="error",
            message="Empty result string",
            category="empty_result",
        )]
    return []


async def validate_file_output(result: Any, context: dict = None) -> List[RepairIssue]:
    """Media/Monitoring: check that output file exists and has reasonable size."""
    issues = []
    if _cat(context) not in ("media", "monitoring", "code"):
        return issues
    output_path = _resolve_output_path(result, context)
    if not output_path:
        return issues
    path = Path(output_path)
    if not path.exists():
        return [RepairIssue(
            severity="error",
            message=f"Output file does not exist: {output_path}",
            category="integrity",
        )]
    size = path.stat().st_size
    if size == 0:
        return [RepairIssue(
            severity="error",
            message=f"Output file is empty: {output_path}",
            category="integrity",
        )]
    min_bytes = (context or {}).get("min_file_bytes", 1024)
    if size < min_bytes:
        issues.append(RepairIssue(
            severity="warning",
            message=f"Output file unusually small: {size} bytes (expected >={min_bytes})",
            category="quality_low",
        ))
    return issues


async def validate_cli_available(result: Any, context: dict = None) -> List[RepairIssue]:
    """Monitoring/Media: check that required CLI tools are installed."""
    issues = []
    required_bins = (context or {}).get("required_bins", [])
    if not required_bins:
        return issues
    for bin_name in required_bins:
        if not _which(bin_name):
            issues.append(RepairIssue(
                severity="error",
                message=f"Required CLI tool not found: {bin_name}",
                category="execution_error",
                fix_suggestion=f"Install {bin_name} (see skill docs for install instructions)",
            ))
    return issues


async def validate_build_or_test(result: Any, context: dict = None) -> List[RepairIssue]:
    """Code: if a build/test command was run, check exit code."""
    issues = []
    if _cat(context) != "code":
        return issues
    if not isinstance(result, dict):
        return issues
    returncode = result.get("returncode")
    if returncode is not None and returncode != 0:
        stderr = (result.get("stderr") or "")[:500]
        issues.append(RepairIssue(
            severity="error",
            message=f"Command exited with code {returncode}: {stderr}",
            category="execution_error",
        ))
    return issues


async def validate_papers_have_abstracts(result: list, context: dict = None) -> List[RepairIssue]:
    """Research: check abstract completeness."""
    issues = []
    if _cat(context) != "research":
        return issues
    if not isinstance(result, list):
        return issues
    total = len(result)
    if total == 0:
        return issues
    empty = sum(1 for p in result if isinstance(p, dict) and not p.get("abstract"))
    if empty > total * 0.5:
        issues.append(RepairIssue(
            severity="warning",
            message=f"{empty}/{total} papers missing abstracts",
            category="missing_field",
        ))
    return issues


async def validate_paper_count(result: list, context: dict = None) -> List[RepairIssue]:
    """Research: warn on zero or excessive papers."""
    issues = []
    if _cat(context) != "research":
        return issues
    if not isinstance(result, list):
        return issues
    count = len(result)
    if count == 0:
        issues.append(RepairIssue(severity="error", message="No papers found", category="empty_result"))
    return issues


async def validate_media_output(result: Any, context: dict = None) -> List[RepairIssue]:
    """Media: verify downloaded/rendered output exists."""
    issues = []
    if _cat(context) != "media":
        return issues
    output_path = _resolve_output_path(result, context)
    if not output_path:
        return issues
    path = Path(output_path)
    if not path.exists():
        issues.append(RepairIssue(
            severity="error",
            message=f"Output file missing: {output_path}",
            category="integrity",
        ))
    elif path.stat().st_size == 0:
        issues.append(RepairIssue(
            severity="error",
            message=f"Output file is empty: {output_path}",
            category="integrity",
        ))
    return issues


# ── Unified Validator Generator ──────────────────────────────────────────────

def unified_validators(category: str = "general", **extra: Any) -> List[Validator]:
    """Return the correct set of validators for a given skill category.

    This is the entry point. It selects:
    - Universal validators (applied to every category)
    - Category-specific validators (applied only when category matches)

    Args:
        category: One of "research", "code", "media", "monitoring", "general".
        **extra: Additional context to pass through (e.g. required_bins, min_file_bytes).

    Returns:
        List of Validator callables ready for RepairHook.repair(validators=...).
    """
    base = [validate_not_empty]
    extra_ctx = extra or {}

    if category == "research":
        return base + [
            _ctx_wrap(validate_papers_have_abstracts, category, **extra_ctx),
            _ctx_wrap(validate_paper_count, category, **extra_ctx),
        ]
    elif category == "code":
        return base + [
            _ctx_wrap(validate_build_or_test, category, **extra_ctx),
            _ctx_wrap(validate_file_output, category, **extra_ctx),
        ]
    elif category == "media":
        return base + [
            _ctx_wrap(validate_media_output, category, **extra_ctx),
            _ctx_wrap(validate_cli_available, category, **extra_ctx),
        ]
    elif category == "monitoring":
        return base + [
            _ctx_wrap(validate_cli_available, category, **extra_ctx),
        ]
    else:
        return base


# ── Internals ────────────────────────────────────────────────────────────────

def _ctx_wrap(validator, category: str, **extra: Any) -> Validator:
    """Wrap a validator so it receives the correct context."""
    base_ctx = {"skill_category": category}
    base_ctx.update(extra)

    async def wrapped(result, ctx=None):
        merged = dict(base_ctx)
        if ctx:
            merged.update(ctx)
        return await validator(result, merged)
    return wrapped


def _resolve_output_path(result: Any, context: dict = None) -> Optional[str]:
    """Extract output file path from result or context."""
    ctx = context or {}
    # Priority: context['output_path'] > result dict key > result string
    if ctx.get("output_path"):
        return str(ctx["output_path"])
    if isinstance(result, dict):
        for key in ("output_path", "output", "path", "file", "filename", "saved_to"):
            val = result.get(key)
            if val:
                return str(val)
    if isinstance(result, str) and os.path.isfile(result):
        return result
    return None


def _which(bin_name: str) -> Optional[str]:
    """Check if a binary is available on PATH."""
    import shutil
    return shutil.which(bin_name)

