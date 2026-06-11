#!/usr/bin/env python3
"""citation-verifier v1.1.0 — citation format check + full existence audit."""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from research_common import (
    batch_check_retractions,
    build_verification_matrix,
    emit, fail,
    format_reference_list,
    format_verification_section,
    normalize_papers,
    papers_from_params,
    parse_params,
    verify_citations,
)


def _audit_sync(review: str, papers: list[dict], max_link_checks: int = 20) -> dict:
    """Synchronous wrapper for async audit. Uses asyncio.run()."""
    return asyncio.run(_audit_async(review, papers, max_link_checks))


async def _audit_async(review: str, papers: list[dict], max_link_checks: int = 20) -> dict:
    """Run the full citation audit pipeline."""
    import aiohttp

    t0 = time.monotonic()
    report: dict = {
        "status": "success",
        "action": "audit",
        "total_papers": len(papers),
        "checks": {},
    }

    # ── 1. Format check ───────────────────────────────
    fmt_result = verify_citations(review, papers)
    report["checks"]["format"] = {
        "valid_indices": fmt_result.get("valid", 0),
        "out_of_range": fmt_result.get("out_of_range", 0),
        "weak_support": fmt_result.get("weak", 0),
        "uncited_papers": fmt_result.get("uncited", 0),
        "details": fmt_result.get("details", []),
    }

    # ── 2. DOI resolution + retraction check ──────────
    batch_check_retractions(papers)
    doi_ok = sum(1 for p in papers if p.get("retraction_check", {}).get("retracted") is False and p.get("doi"))
    doi_retracted = sum(1 for p in papers if p.get("retraction_check", {}).get("retracted"))
    doi_unresolved = sum(1 for p in papers if p.get("retraction_check", {}).get("status") == "unresolved")
    report["checks"]["existence"] = {
        "total_with_doi": sum(1 for p in papers if p.get("doi")),
        "doi_resolved": doi_ok,
        "retractions_found": doi_retracted,
        "doi_unresolved": doi_unresolved,
        "retraction_details": [
            {
                "title": (p.get("title") or "")[:100],
                "doi": p.get("doi", ""),
                "status": p.get("retraction_check", {}).get("status", "unknown"),
                "retracted": p.get("retraction_check", {}).get("retracted", False),
                "message": p.get("retraction_check", {}).get("message", ""),
            }
            for p in papers
            if p.get("retraction_check", {}).get("retracted")
            or p.get("retraction_check", {}).get("status") == "unresolved"
        ],
    }

    # ── 3. HTTP link reachability ─────────────────────
    check_count = min(max_link_checks, len(papers))
    link_results = []
    timeout = aiohttp.ClientTimeout(total=8)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        from research_common import build_paper_links, _link_check_for_url

        for paper in papers[:check_count]:
            links = build_paper_links(paper)
            urls_to_check = [links.get("doi_url"), links.get("openalex_url")] if isinstance(links, dict) else []
            paper_links = []
            for url in urls_to_check:
                if url:
                    status = await _link_check_for_url(session, url)
                    paper_links.append({"url": url, **status})
            link_results.append({
                "title": (paper.get("title") or "")[:100],
                "doi": paper.get("doi", ""),
                "links": paper_links,
                "any_reachable": any(
                    l.get("status") == "reachable" for l in paper_links
                ),
            })

    reachable_count = sum(1 for r in link_results if r["any_reachable"])
    broken_count = sum(1 for r in link_results if not r["any_reachable"] and r["links"])

    report["checks"]["links"] = {
        "checked": check_count,
        "reachable": reachable_count,
        "broken": broken_count,
        "not_checked": len(papers) - check_count,
        "details": link_results,
    }

    # ── 4. Unified per-reference summary ──────────────
    per_ref = []
    for i, p in enumerate(papers):
        entry = {
            "index": i + 1,
            "title": (p.get("title") or f"Reference {i+1}")[:150],
            "doi": p.get("doi", ""),
            "format_ok": True,
            "exists": p.get("retraction_check", {}).get("retracted") is False and bool(p.get("doi")),
            "retracted": p.get("retraction_check", {}).get("retracted", False),
            "link_reachable": False,
            "metadata_source": p.get("retraction_check", {}).get("metadata_source") or "unknown",
            "hallucination_risk": p.get("retraction_check", {}).get("hallucination_risk", "unknown"),
        }
        # Check if this paper was flagged in format check
        for detail in fmt_result.get("details", []):
            if detail.get("index") == i + 1 and detail.get("status") != "supported":
                entry["format_ok"] = False
        # Check link reachability
        if i < len(link_results):
            entry["link_reachable"] = link_results[i]["any_reachable"]
        per_ref.append(entry)

    report["per_reference"] = per_ref

    # ── 5. Summary ────────────────────────────────────
    all_format_ok = report["checks"]["format"]["out_of_range"] == 0
    all_exist = doi_unresolved == 0 and doi_retracted == 0
    all_links_ok = broken_count == 0
    healthy = all_format_ok and all_exist and all_links_ok

    report["summary"] = {
        "healthy": healthy,
        "format_ok": all_format_ok,
        "all_exist": all_exist,
        "all_links_ok": all_links_ok,
        "total_papers": len(papers),
        "format_issues": report["checks"]["format"]["out_of_range"] + report["checks"]["format"]["weak_support"],
        "retractions": doi_retracted,
        "broken_links": broken_count,
    }

    # ── 6. Verification matrix ────────────────────────
    report["verification_matrix"] = build_verification_matrix(papers)
    report["verification_section"] = format_verification_section(papers)

    # ── 7. Formatted references ────────────────────────
    report["references"] = format_reference_list(papers, style="gbt7714")

    report["duration_seconds"] = round(time.monotonic() - t0, 1)
    report["evidence_boundary"] = (
        "DOI resolution via CrossRef API; HTTP reachability checked via live GET requests; "
        "retraction status from CrossRef. A resolved DOI confirms the paper exists in CrossRef "
        "but does not guarantee the full text is accessible. Paywalled sites may return false "
        "negatives on HTTP checks. Always verify critical references manually."
    )

    return report


def main() -> int:
    params = parse_params(sys.argv[1] if len(sys.argv) > 1 else "{}")
    if params.get("path"):
        value = json.loads(Path(str(params["path"])).read_text(encoding="utf-8", errors="replace"))
        if isinstance(value, dict):
            params = {**value, **params}
    action = str(params.get("action") or "verify").strip().lower()
    review = str(params.get("review") or params.get("text") or "")
    papers = papers_from_params(params)
    if not papers and params.get("matrix"):
        papers = normalize_papers(params["matrix"])
    if not review:
        fail("Missing review text.")
    if not papers:
        fail("Missing papers or matrix.")

    if action == "audit":
        max_links = int(params.get("max_link_checks", 20))
        result = _audit_sync(review, papers, max_link_checks)
        # Add formatted references (not included in audit by default since already there)
        if params.get("include_references", True) and "references" not in result:
            result["references"] = format_reference_list(
                papers, style=str(params.get("citation_style") or "gbt7714")
            )
        emit(result)
    else:
        # Default: verify (original behavior)
        result = verify_citations(review, papers)
        payload = {
            "status": "success", "completed": True,
            "verification_matrix": build_verification_matrix(papers),
            **result,
        }
        if params.get("include_references", True):
            payload["references"] = format_reference_list(
                papers, style=str(params.get("citation_style") or "gbt7714")
            )
        emit(payload)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
