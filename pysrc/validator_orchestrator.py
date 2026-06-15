"""UnifiedValidatorOrchestrator - One unified validator for all categories.

Solves the "white horse not a horse" problem by having a SINGLE set of
validators that are parameterized by category rather than duplicated.

Principle:
  - Universal validators apply to ALL categories (not empty, no None)
  - Category-aware validators check BOTH whether the check applies AND
    whether the result passes
  - edge cases (e.g. "research papers must have DOIs") are handled by
    registering per-category overrides via the extra registry
  - The orchestrator ensures no validator is duplicated or conflicting

No "white horse not a horse" issue because:
  - We never check "is this a research result? then apply research validator"
  - Instead: "does this result need a DOI check? only if DOI is relevant"
  - The orchestrator maintains a capability matrix of what each validator
    applies to, and questions that are "white horse" category-specific are
    handled by the per-category extra check slot
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from repair_hook import RepairIssue, Validator, RepairHook


@dataclass
class ValidatorCapability:
    """Describes what a validator checks and which categories it applies to."""
    name: str
    description: str
    applies_to: List[str]  # categories this applies to, or ["all"]
    severity: str  # "error", "warning", "info"
    universal: bool  # True if it applies regardless of category


# The canonical capability matrix
_CAPABILITY_MATRIX: List[ValidatorCapability] = [
    ValidatorCapability("not_empty", "Result is not None or empty", ["all"], "error", True),
    ValidatorCapability("file_exists", "Output file exists on disk", ["media", "monitoring", "code"], "error", False),
    ValidatorCapability("cli_available", "Required CLI binary exists on PATH", ["media", "monitoring"], "error", False),
    ValidatorCapability("build_succeeds", "Build/test command exits with 0", ["code"], "error", False),
    ValidatorCapability("abstracts_present", "Papers have abstracts", ["research"], "warning", False),
    ValidatorCapability("paper_count", "Paper count is within range", ["research"], "error", False),
    ValidatorCapability("doi_present", "Papers have DOIs", ["research"], "warning", False),
    ValidatorCapability("no_duplicates", "No duplicate papers", ["research"], "info", False),
    ValidatorCapability("citations_nonzero", "Papers have non-zero citations", ["research"], "warning", False),
]


class ValidatorOrchestrator:
    """One orchestrator that knows which validators apply to which categories.

    Usage:
        orchestrator = ValidatorOrchestrator()
        issues = await orchestrator.validate(category="research", result=papers)
        # Returns ALL relevant issues in one pass
    """

    def __init__(self):
        self._extra_validators: Dict[str, List[Validator]] = {}

    def register_extra(self, category: str, validator: Validator) -> None:
        """Register an extra category-specific validator (edge case override)."""
        self._extra_validators.setdefault(category, []).append(validator)

    async def validate(
        self,
        category: str,
        result: Any,
        context: dict = None,
    ) -> List[RepairIssue]:
        """Run ALL validators applicable to the given category in one pass."""
        ctx = dict(context or {})
        ctx["skill_category"] = category
        all_issues: List[RepairIssue] = []

        # Universal validators
        for cap in _CAPABILITY_MATRIX:
            if cap.universal or category in cap.applies_to:
                validator = self._get_validator_fn(cap.name)
                if validator:
                    try:
                        issues = await validator(result, ctx)
                        if issues:
                            all_issues.extend(issues)
                    except Exception as exc:
                        all_issues.append(RepairIssue(
                            severity="info",
                            message=f"Validator {cap.name} raised: {exc}",
                            category="validator_error",
                        ))

        # Extra validators for this category
        for validator in self._extra_validators.get(category, []):
            try:
                issues = await validator(result, ctx)
                if issues:
                    all_issues.extend(issues)
            except Exception as exc:
                all_issues.append(RepairIssue(
                    severity="info",
                    message=f"Extra validator raised: {exc}",
                    category="validator_error",
                ))

        return all_issues

    def capability_matrix(self) -> List[ValidatorCapability]:
        """Return the full capability matrix for inspection."""
        return list(_CAPABILITY_MATRIX)

    def validate_sync_report(self, category: str) -> str:
        """Generate a human-readable report of what will be validated."""
        applicable = [c for c in _CAPABILITY_MATRIX if c.universal or category in c.applies_to]
        extras = self._extra_validators.get(category, [])
        lines = [
            f"=== Validator Report for category: {category} ===",
            f"Universal validators: {sum(1 for c in applicable if c.universal)}",
            f"Category-specific validators: {sum(1 for c in applicable if not c.universal)}",
            f"Extra (registered) validators: {len(extras)}",
            "",
            "Applicable validators:",
        ]
        for cap in applicable:
            lines.append(f"  - [{cap.severity}] {cap.name}: {cap.description}")
        if extras:
            lines.append(f"  - {len(extras)} extra validator(s) registered")
        return chr(10).join(lines)

    def _get_validator_fn(self, name: str) -> Optional[Validator]:
        """Resolve a validator name to an async function."""
        # Import on demand to avoid circular imports
        import skills.unified_validators as uv
        mapper = {
            "not_empty": uv.validate_not_empty,
            "file_exists": uv.validate_file_output,
            "cli_available": uv.validate_cli_available,
            "build_succeeds": uv.validate_build_or_test,
            "abstracts_present": uv.validate_papers_have_abstracts,
            "paper_count": uv.validate_paper_count,
        }
        return mapper.get(name)
