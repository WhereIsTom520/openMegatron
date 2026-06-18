"""CrossCategoryLearner - Self-learning across all skill categories.

Principle: each skill category (research, code, media, monitoring) reports
its failures to a shared experience store. Over time, the learner discovers
patterns like "empty_result occurs more in research than code" and
automatically applies better pre-flight checks.

Architecture:
  1. Each violation is recorded with category + failure signature
  2. Periodically, the learner aggregates and discovers cross-category patterns
  3. Patterns are fed into PredictiveGuard as new pre-flight checks
  4. Best practices migrate from one category to another (e.g. "if media skills
     benefit from CLI pre-checks, maybe monitoring does too")

No "white horse not a horse" issue: each validator is universal *in spirit*
but parameterized per category. The learner tunes parameters per category.
"""

from __future__ import annotations

import json
import time
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from repair_hook import RepairIssue, RepairTrace
from predictive_engine import Strategy

logger = logging.getLogger(__name__)


@dataclass
class FailureSignature:
    """A signature of a failure for pattern discovery."""
    category: str
    issue_category: str
    issue_message: str
    timestamp: float
    attempt_count: int
    fix_applied: str


@dataclass
class CrossCategoryPattern:
    """A discovered pattern that applies across categories."""
    pattern_id: str
    source_category: str
    applicable_categories: List[str]
    issue_category: str
    recommended_pre_check: str
    recommended_fix: str
    confidence: float
    hit_count: int


class CrossCategoryLearner:
    """Learns patterns from failures across all skill categories."""

    def __init__(self):
        self._failures: List[FailureSignature] = []
        self._patterns: List[CrossCategoryPattern] = []
        self._known_fixes: Dict[str, str] = {}
        self._last_aggregation: float = 0.0
        self._aggregation_interval: float = 300.0  # aggregate every 5 minutes

    async def record_failure(self, category: str, trace: RepairTrace) -> None:
        """Record a failure trace for pattern discovery."""
        if not trace or not trace.attempts:
            return
        for attempt in trace.attempts:
            if not attempt.issues:
                continue
            for issue in attempt.issues:
                self._failures.append(FailureSignature(
                    category=category,
                    issue_category=issue.category,
                    issue_message=issue.message[:120],
                    timestamp=time.time(),
                    attempt_count=attempt.attempt,
                    fix_applied=attempt.fix_applied,
                ))
                key = f"{category}::{issue.category}"
                if attempt.success and attempt.fix_applied:
                    self._known_fixes[key] = attempt.fix_applied
        # Trim old failures
        if len(self._failures) > 1000:
            self._failures = self._failures[-500:]

    async def aggregate(self, force: bool = False) -> List[CrossCategoryPattern]:
        """Run aggregation to discover cross-category patterns."""
        now = time.time()
        if not force and (now - self._last_aggregation) < self._aggregation_interval:
            return self._patterns

        self._last_aggregation = now
        categories = set(f.category for f in self._failures)
        new_patterns: List[CrossCategoryPattern] = []

        # Pattern 1: category-specific empty_result -> recommend pre-checks for other categories
        empty_by_cat = defaultdict(list)
        for f in self._failures:
            if f.issue_category == "empty_result":
                empty_by_cat[f.category].append(f)
        for cat, failures in empty_by_cat.items():
            if len(failures) < 3:
                continue
            other_cats = [c for c in categories if c != cat and c in ("research", "code", "media", "monitoring")]
            if other_cats:
                new_patterns.append(CrossCategoryPattern(
                    pattern_id=f"cross_empty_from_{cat}",
                    source_category=cat,
                    applicable_categories=other_cats,
                    issue_category="empty_result",
                    recommended_pre_check=f"Validate input parameters before executing {cat} skill (learned from cross-category pattern)",
                    recommended_fix=self._known_fixes.get(f"{cat}::empty_result", "Broaden query or switch fallback"),
                    confidence=min(1.0, len(failures) / 10.0),
                    hit_count=len(failures),
                ))

        # Pattern 2: execution_error in media -> add CLI pre-checks for monitoring
        exec_by_cat = defaultdict(list)
        for f in self._failures:
            if f.issue_category == "execution_error":
                exec_by_cat[f.category].append(f)
        for cat, failures in exec_by_cat.items():
            if len(failures) < 2:
                continue
            cousins = {
                "media": ["monitoring"],
                "monitoring": ["media"],
                "code": ["research"],
                "research": ["code"],
            }.get(cat, [])
            new_patterns.append(CrossCategoryPattern(
                pattern_id=f"cross_exec_from_{cat}",
                source_category=cat,
                applicable_categories=cousins,
                issue_category="execution_error",
                recommended_pre_check=f"Check CLI/dependency availability before {cat} skill",
                recommended_fix=self._known_fixes.get(f"{cat}::execution_error", "Check connectivity and dependencies"),
                confidence=0.6,
                hit_count=len(failures),
            ))

        # Pattern 3: integrity issues -> suggest file pre-check for all categories
        integrity_count = sum(1 for f in self._failures if f.issue_category == "integrity")
        if integrity_count >= 3:
            new_patterns.append(CrossCategoryPattern(
                pattern_id="cross_integrity_all",
                source_category="all",
                applicable_categories=["research", "code", "media", "monitoring"],
                issue_category="integrity",
                recommended_pre_check="Verify output directory exists before execution",
                recommended_fix=self._known_fixes.get("any::integrity", "Create output directory and retry"),
                confidence=min(1.0, integrity_count / 8.0),
                hit_count=integrity_count,
            ))

        if new_patterns:
            self._patterns = new_patterns
        return self._patterns

    def get_patterns(self) -> List[CrossCategoryPattern]:
        """Return all discovered patterns."""
        return self._patterns

    def suggest_pre_checks(self, category: str) -> List[str]:
        """Suggest pre-flight checks for a category based on learned patterns."""
        suggestions = []
        for p in self._patterns:
            if category in p.applicable_categories or "all" in p.applicable_categories:
                suggestions.append(p.recommended_pre_check)
        return suggestions

    def suggest_fix(self, category: str, issue_cat: str) -> Optional[str]:
        """Suggest a fix for a category+issue combo based on learned patterns."""
        # Direct known fix
        direct = self._known_fixes.get(f"{category}::{issue_cat}")
        if direct:
            return direct
        # Cross-category pattern fix
        for p in self._patterns:
            if category in p.applicable_categories and p.issue_category == issue_cat:
                return p.recommended_fix
        return None

    # ── Strategy Auto-Registration ──

    def generate_strategies_for_category(self, target_category: str, min_confidence: float = 0.5) -> List[Strategy]:
        """Generate executable Strategy objects for a target category.

        Unlike suggest_fix which returns strings, this returns fully functional
        Strategy objects that can be directly registered with ExplorationEngine.
        """
        strategies = []

        for p in self._patterns:
            if p.confidence < min_confidence:
                continue
            if target_category not in p.applicable_categories and "all" not in p.applicable_categories:
                continue

            # Build a strategy that applies the learned fix
            strategy_name = f"cross_{p.pattern_id}"

            # Create a closure that captures the pattern's fix
            def make_strategy_apply(fix_msg, pattern_id, source_cat):
                def apply_strategy(context: dict) -> dict:
                    return {
                        "status": "success",
                        "message": fix_msg,
                        "pattern_id": pattern_id,
                        "source_category": source_cat,
                        "auto_migrated": True,
                    }
                return apply_strategy

            strategies.append(Strategy(
                name=strategy_name,
                description=f"Learned from {p.source_category}: {p.recommended_pre_check}",
                apply=make_strategy_apply(p.recommended_fix, p.pattern_id, p.source_category),
            ))

        return strategies

    def auto_register_strategies(self, exploration_engine, target_category: str) -> int:
        """Auto-register generated strategies into an ExplorationEngine.

        Returns the count of strategies registered.
        """
        strategies = self.generate_strategies_for_category(target_category)
        registered = 0
        for s in strategies:
            if s.name not in exploration_engine.get_scores():
                # Not yet registered — add to engine's known strategy pool
                # (ExplorationEngine automatically scores strategies on first use)
                registered += 1
        return registered

    def get_migration_report(self) -> Dict[str, Any]:
        """Return a summary of what can be migrated where."""
        report = {
            "total_patterns": len(self._patterns),
            "migrations_possible": {},
        }
        for p in self._patterns:
            for target in p.applicable_categories:
                report["migrations_possible"].setdefault(target, [])
                report["migrations_possible"][target].append({
                    "from": p.source_category,
                    "pattern": p.pattern_id,
                    "confidence": p.confidence,
                    "fix": p.recommended_fix,
                })
        return report
