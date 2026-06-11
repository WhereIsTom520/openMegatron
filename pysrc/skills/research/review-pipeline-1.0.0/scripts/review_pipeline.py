from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

RESEARCH_DIR = Path(__file__).resolve().parents[2]
PYSRC_DIR = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(RESEARCH_DIR))
sys.path.insert(0, str(PYSRC_DIR))

from literature_graph import LiteratureGraph  # noqa: E402
from research_common import (  # noqa: E402
    analyze_research_gaps,
    build_evidence_matrix,
    build_review_protocol,
    build_verification_matrix,
    emit,
    fail,
    format_reference_list,
    format_verification_section,
    normalize_papers,
    parse_params,
    reading_from_paper,
    validate_review_claims,
    verify_citations,
    verify_citations_semantic,
)


def top_paper_script() -> Path:
    return RESEARCH_DIR / "top_paper_search-1.0.0" / "scripts" / "top_paper_search.py"


def run_top_paper(params: dict, venue_mode: str = "strict") -> dict:
    script = top_paper_script()
    if not script.exists():
        fail("top_paper_search skill is missing.")
    payload = {
        "action": "review" if params.get("generate_review", True) else "fetch",
        "query": params["query"],
        "year_start": params.get("year_start"),
        "limit": int(params.get("limit") or 100),
        "top_n": int(params.get("top_n") or 8),
        "generate_review": bool(params.get("generate_review", True)),
        "domain": params.get("domain"),
        "fill_abstracts": bool(params.get("fill_abstracts", True)),
        "abstract_limit": int(params.get("abstract_limit") or 8),
        "venue_mode": venue_mode,
    }
    proc = subprocess.run(
        [sys.executable, str(script), json.dumps(payload, ensure_ascii=False)],
        cwd=str(script.parents[1]),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=int(params.get("timeout") or 240),
    )
    if proc.returncode != 0:
        fail((proc.stderr or proc.stdout or "top_paper_search failed")[:1200])
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        fail(f"Could not parse top_paper_search output: {exc}")


def fallback_review(query: str, matrix: list[dict], gap_analysis: dict) -> str:
    lines = [
        f"# {query} 文献综述草稿",
        "",
        "## 一、研究现状概览",
    ]
    if not matrix:
        lines.append("未检索到符合顶刊顶会白名单的候选文献，不能生成可靠综述。")
        return "\n".join(lines)
    for row in matrix:
        lines.append(
            f"- {row['citation_hint']} {row['title']} ({row.get('year') or 'n.d.'}, "
            f"{row.get('venue') or 'unknown'}): 方法类别为 {row.get('method_category')}，"
            f"贡献类型为 {row.get('contribution_type')}。"
        )
    lines.extend([
        "",
        "## 二、方法脉络",
        "现有证据可先按方法类别、贡献类型和评测设置拆分，再比较不同方向在可靠性、可扩展性和领域适配上的差异。",
        "",
        "## 三、研究空白与未来方向",
    ])
    for item in gap_analysis.get("potential_innovation_directions", []):
        lines.append(f"- {item}")
    lines.extend([
        "",
        "## 四、证据边界",
        "本草稿基于题名、摘要、venue、引用数和可访问元数据生成；涉及实验细节、数据集、指标和定量结论时，需要继续阅读 PDF 全文。",
    ])
    return "\n".join(lines)


def build_citation_graph(papers: list, query: str) -> dict:
    if not papers:
        return {"markdown": "No papers to build citation graph.", "mermaid": "", "citation_mermaid": "", "node_count": 0, "edge_count": 0, "nodes": [], "edges": []}
    graph = LiteratureGraph.from_papers(papers, title="Literature Review: " + query[:80])
    citation_mermaid = graph._to_citation_mermaid()
    return {
        "markdown": graph.to_markdown(),
        "mermaid": graph._to_mermaid(),
        "citation_mermaid": citation_mermaid,
        "node_count": len(graph.nodes),
        "edge_count": len(graph.edges),
        "nodes": [
            {
                "id": node.get("id", ""),
                "title": (node.get("title") or "?")[:80],
                "year": node.get("year"),
                "venue": (node.get("venue") or "")[:40],
                "citations": node.get("citations", 0),
                "authors": (node.get("authors") or "")[:80],
                "external": node.get("external", False),
            }
            for node in graph.nodes
        ],
        "edges": [
            {"source": edge["source"], "target": edge["target"], "type": edge.get("type", "cites")}
            for edge in graph.edges
        ],
    }


def append_citation_graph_to_review(review: str, graph_payload: dict) -> str:
    citation_mermaid = graph_payload.get("citation_mermaid") or ""
    if not citation_mermaid:
        return review
    return review.rstrip() + "\n\n### Citation Graph\n```mermaid\n" + citation_mermaid + "\n```\n"


def main() -> int:
    params = parse_params(sys.argv[1] if len(sys.argv) > 1 else "{}")
    action = str(params.get("action") or "run").lower()
    if action == "update":
        prev_path = params.get("previous_out", "")
        new_papers_raw = params.get("new_papers", [])
        if not prev_path:
            fail("Missing previous_out path for update action.")
        prev = json.loads(Path(prev_path).read_text(encoding="utf-8", errors="replace"))
        existing = normalize_papers(prev.get("papers", []))
        new_papers = normalize_papers(new_papers_raw)

        # Deduplicate by DOI and title
        existing_dois = {p.get("doi", "") for p in existing if p.get("doi")}
        existing_titles = {_norm_title(p.get("title", "")) for p in existing}
        added = []
        for np in new_papers:
            if np.get("doi") in existing_dois:
                continue
            if _norm_title(np.get("title", "")) in existing_titles:
                continue
            added.append(np)
            existing.append(np)

        if not added:
            emit({"status": "success", "updated": True, "added": 0, "total": len(existing),
                  "message": "No new papers to add (all duplicates)."})
            return 0

        # Rebuild matrix and gap analysis with expanded set
        matrix = build_evidence_matrix(existing)
        gap = analyze_research_gaps(matrix)
        references = format_reference_list(existing, style=str(params.get("citation_style") or "gbt7714"))

        emit({"status": "success", "updated": True, "added": len(added), "total": len(existing),
              "new_titles": [p.get("title", "")[:100] for p in added],
              "evidence_matrix": matrix, "gap_analysis": gap, "references": references,
              "note": "Matrix and gap analysis rebuilt. Review text preserved from previous run."})
        return 0

    if action != "run":
        fail("review_pipeline supports action=run or action=update.")
    if not params.get("query"):
        fail("Missing query.")

    # ── Discovery phase: inclusive search for gap analysis ──
    discovery_result = run_top_paper(params, venue_mode="inclusive")
    all_papers = normalize_papers(discovery_result.get("papers", []))
    # Split papers by venue tier
    top_papers = [p for p in all_papers if p.get("venue_tier") not in ("unranked", "unknown", "")]
    other_papers = [p for p in all_papers if p.get("venue_tier") in ("unranked", "unknown", "")]
    effective_domain = discovery_result.get("effective_domain", params.get("domain"))

    # ── Gap analysis: use ALL papers (inclusive discovery) ──
    all_readings = [reading_from_paper(paper) for paper in all_papers]
    matrix = build_evidence_matrix(all_papers)
    gap_analysis = analyze_research_gaps(matrix)

    # ── Citation verification: use only top-tier papers ──
    review = discovery_result.get("review") or fallback_review(str(params["query"]), matrix, gap_analysis)
    verification = verify_citations(review, top_papers) if top_papers else {"verdict": "no_top_papers", "issues": []}
    claim_validation = validate_review_claims(review, matrix) if discovery_result.get("review") else {"verdict": "template_review", "issues": []}
    semantic_verification = verify_citations_semantic(review, top_papers) if top_papers else {}
    references = format_reference_list(top_papers, style=str(params.get("citation_style") or "gbt7714"))
    verification_matrix = discovery_result.get("verification_matrix") or build_verification_matrix(top_papers)
    citation_graph = build_citation_graph(top_papers, str(params["query"]))
    if "引用与反幻觉验证矩阵" not in review:
        review = review.rstrip() + "\n" + format_verification_section(papers)
    review = append_citation_graph_to_review(review, citation_graph)
    protocol = build_review_protocol(
        str(params["query"]),
        review_type=str(params.get("review_type") or "narrative"),
        year_start=params.get("year_start"),
        top_n=params.get("top_n"),
        domain=effective_domain,
    )

    payload = {
        "status": "success",
        "completed": True,
        "query": params["query"],
        "protocol": protocol,
        "search": {
            "filter_mode": "inclusive_for_gaps",
            "venue_policy": discovery_result.get("venue_policy"),
            "requested_domain": discovery_result.get("requested_domain", params.get("domain")),
            "effective_domain": effective_domain,
            "effective_query": discovery_result.get("effective_query"),
            "total_fetched": discovery_result.get("total_fetched"),
            "valid_count": discovery_result.get("valid_count"),
            "link_policy": discovery_result.get("link_policy"),
        },
        "papers": all_papers,                          # ALL papers for discovery
        "top_tier_papers": top_papers,                  # Top-venue only for citation
        "other_venue_papers": other_papers,             # Non-top-venue for reference
        "paper_counts": {
            "total": len(all_papers),
            "top_tier": len(top_papers),
            "other_venues": len(other_papers),
            "venue_mode_note": "Gap analysis uses ALL papers (inclusive). Citations use top-tier only (strict).",
        },
        "readings": all_readings,
        "evidence_matrix": matrix,
        "gap_analysis": gap_analysis,
        "review": review,
        "references": references,
        "verification_matrix": verification_matrix,
        "reference_verification": search_result.get("reference_verification") or {
            "verdict": "metadata_verified" if verification_matrix else "no_papers",
            "paper_count": len(verification_matrix),
            "boundary": "Metadata traceability only; full claims require full-text evidence.",
        },
        "citation_verification": verification,
        "claim_validation": claim_validation,
        "semantic_verification": semantic_verification,
        "citation_graph": citation_graph,
    }
    if params.get("out"):
        out = Path(str(params["out"])).expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        payload["out"] = str(out)
    emit(payload)
    return 0


def _norm_title(t: str) -> str:
    return " ".join(str(t).lower().replace("{", "").replace("}", "").replace("\n", " ").split())[:120]


if __name__ == "__main__":
    raise SystemExit(main())
