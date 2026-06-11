"""Research-specific validators for the RepairHook self-healing engine.

These validators check common failure modes in research skill outputs:
- Empty results from API calls
- Missing or truncated abstracts
- Low citation counts
- Unrealistic author lists
- Duplicate papers in results
"""

from __future__ import annotations

import re
from typing import Any, List
from repair_hook import RepairIssue, Validator


async def validate_papers_have_abstracts(result: list, context: dict = None) -> List[RepairIssue]:
    """Check that papers in the result list have non-empty abstracts."""
    issues = []
    if not isinstance(result, list):
        return issues
    empty_count = sum(1 for p in result if isinstance(p, dict) and not p.get("abstract"))
    total = len(result)
    if total > 0 and empty_count > total * 0.5:
        issues.append(RepairIssue(
            severity="warning",
            message=f"{empty_count}/{total} papers missing abstracts",
            category="missing_field",
            raw_context={"total": total, "missing_abstracts": empty_count, "threshold": 0.5},
        ))
    return issues


async def validate_paper_count_in_range(min_count: int = 1, max_count: int = 200) -> Validator:
    """Check that the paper count is within a reasonable range."""
    async def _validate(result: list, context: dict = None) -> List[RepairIssue]:
        issues = []
        if not isinstance(result, list):
            return issues
        count = len(result)
        if count == 0:
            issues.append(RepairIssue(
                severity="error",
                message="No papers found",
                category="empty_result",
                raw_context={"count": 0},
            ))
        elif count > max_count:
            issues.append(RepairIssue(
                severity="warning",
                message=f"Too many papers ({count}), consider filtering",
                category="quality_low",
                raw_context={"count": count, "max": max_count},
            ))
        return issues
    return _validate


async def validate_citations_nonzero(result: list, context: dict = None) -> List[RepairIssue]:
    """Warn if most papers have zero citations (suspect data quality issue)."""
    issues = []
    if not isinstance(result, list):
        return issues
    papers = [p for p in result if isinstance(p, dict)]
    if not papers:
        return issues
    zero_cite = sum(1 for p in papers if p.get("citations", 0) == 0)
    if zero_cite > len(papers) * 0.8:
        issues.append(RepairIssue(
            severity="warning",
            message=f"{zero_cite}/{len(papers)} papers have 0 citations",
            category="quality_low",
            raw_context={"zero_cited": zero_cite, "total": len(papers)},
        ))
    return issues


async def validate_no_duplicates_by_title(result: list, context: dict = None) -> List[RepairIssue]:
    """Check for duplicate papers by normalized title."""
    issues = []
    if not isinstance(result, list):
        return issues
    seen = {}
    for idx, paper in enumerate(result):
        if not isinstance(paper, dict):
            continue
        title = (paper.get("title") or "").strip().lower()
        title = re.sub(r"\s+", " ", title)
        if title and len(title) > 10:
            if title in seen:
                issues.append(RepairIssue(
                    severity="info",
                    message=f"Duplicate at index {idx}: same title as index {seen[title]}",
                    category="quality_low",
                    raw_context={"index": idx, "duplicate_of": seen[title], "title": title[:80]},
                ))
            else:
                seen[title] = idx
    if issues:
        issues.append(RepairIssue(
            severity="info",
            message=f"Found {len(issues)} duplicate(s) in results",
            category="quality_low",
        ))
    return issues


async def validate_doi_exists(result: list, context: dict = None) -> List[RepairIssue]:
    """Check that papers have DOIs (needed for proper citation)."""
    issues = []
    if not isinstance(result, list):
        return issues
    missing = sum(1 for p in result if isinstance(p, dict) and not p.get("doi"))
    total = len(result)
    if total > 0 and missing > total * 0.3:
        issues.append(RepairIssue(
            severity="warning",
            message=f"{missing}/{total} papers missing DOI",
            category="missing_field",
            raw_context={"missing_doi": missing, "total": total},
        ))
    return issues


async def validate_year_in_range(min_year: int = 1900, max_year: int = 2030) -> Validator:
    """Check publication years are in a reasonable range."""
    async def _validate(result: list, context: dict = None) -> List[RepairIssue]:
        issues = []
        if not isinstance(result, list):
            return issues
        for idx, paper in enumerate(result):
            if not isinstance(paper, dict):
                continue
            year = paper.get("year")
            if year is not None:
                try:
                    y = int(year)
                    if y < min_year or y > max_year:
                        issues.append(RepairIssue(
                            severity="warning",
                            message=f"Paper {idx} has out-of-range year: {y}",
                            category="quality_low",
                            raw_context={"index": idx, "year": y},
                        ))
                except (ValueError, TypeError):
                    pass
        return issues
    return _validate

