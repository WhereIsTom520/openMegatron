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


def _pipeline_with_blackboard(params: dict) -> dict:
    """Run the review pipeline with task blackboard for progress tracking and checkpoint/resume."""
    from task_blackboard import TaskBlackboard, Step

    task_id = params.get("task_id") or f"review_{params.get('query', 'unknown')[:30].replace(' ', '_')}"
    save_dir = params.get("blackboard_dir", ".blackboard")
    resume = bool(params.get("resume", False))

    # Try to resume from checkpoint
    if resume:
        bb = TaskBlackboard.resume(task_id, save_dir)
        if bb and bb.is_complete():
            return {"status": "success", "already_complete": True,
                    "message": "Task was already completed.", "progress": bb.progress()}
    else:
        bb = None

    if bb is None:
        bb = TaskBlackboard(task_id, save_dir)
        bb.plan([
            Step("discover", "文献发现：检索并筛选顶刊顶会论文",
                 strategy="OpenAlex + venues.toml 白名单过滤",
                 fallback_strategy="放宽年份和领域限制重试"),
            Step("read", "论文阅读：提取摘要、方法、发现",
                 strategy="PyPDF2 提取 + LLM 结构化",
                 fallback_strategy="仅使用 OpenAlex 元数据"),
            Step("matrix", "证据矩阵：构建结构化证据表",
                 strategy="research_common.build_evidence_matrix",
                 fallback_strategy="仅用标题和摘要构建简化矩阵"),
            Step("gaps", "空白分析：识别研究空白和创新方向",
                 strategy="analyze_research_gaps + 方法分布对比",
                 fallback_strategy="基于关键词的启发式空白分析"),
            Step("review", "综述生成：撰写结构化文献综述",
                 strategy="LLM 合成 + 证据矩阵驱动",
                 fallback_strategy="模板化综述（不依赖 LLM）"),
            Step("verify", "引用验证：格式校验 + 反幻觉检查",
                 strategy="citation_verifier + DOI 可追踪性",
                 fallback_strategy="仅格式校验，标记为'未验证'"),
            Step("graph", "引用图谱：构建文献引用关系图",
                 strategy="LiteratureGraph + Mermaid 可视化",
                 fallback_strategy="仅输出文献列表（无图）"),
        ])
        bb.metadata["query"] = params.get("query", "")
        bb.metadata["domain"] = params.get("domain", "")

    # ── Step 1: Discover ──
    if bb._get("discover").status in (StepStatus.PENDING,):
        bb.start("discover")
        try:
            discovery_result = run_top_paper(params, venue_mode="inclusive")
            all_papers = normalize_papers(discovery_result.get("papers", []))
            top_papers = [p for p in all_papers if p.get("venue_tier") not in ("unranked", "unknown", "")]
            other_papers = [p for p in all_papers if p.get("venue_tier") in ("unranked", "unknown", "")]
            effective_domain = discovery_result.get("effective_domain", params.get("domain"))
            bb.complete("discover", result={"total": len(all_papers), "top_tier": len(top_papers)},
                        summary=f"检索到 {len(all_papers)} 篇论文（{len(top_papers)} 篇顶刊顶会）")
        except Exception as e:
            if bb.can_retry("discover"):
                bb.retry("discover", error=str(e), new_strategy="放宽限制重试")
                params_retry = dict(params)
                params_retry["year_start"] = max(2018, int(params.get("year_start", 2020)) - 5)
                params_retry["limit"] = int(params.get("limit", 100)) * 2
                discovery_result = run_top_paper(params_retry, venue_mode="inclusive")
                all_papers = normalize_papers(discovery_result.get("papers", []))
                top_papers = [p for p in all_papers if p.get("venue_tier") not in ("unranked", "unknown", "")]
                other_papers = [p for p in all_papers if p.get("venue_tier") in ("unranked", "unknown", "")]
                effective_domain = discovery_result.get("effective_domain", params.get("domain"))
                bb.complete("discover", result={"total": len(all_papers), "top_tier": len(top_papers)},
                            summary=f"放宽限制后检索到 {len(all_papers)} 篇论文（{len(top_papers)} 篇顶刊顶会）")
            else:
                bb.fail("discover", error=str(e))
                return {"status": "error", "message": f"文献发现失败: {e}", "blackboard": bb.to_dict()}

    # ── Step 2: Read ──
    if bb._get("read").status in (StepStatus.PENDING,):
        bb.start("read")
        try:
            all_readings = [reading_from_paper(paper) for paper in all_papers]
            bb.complete("read", result={"readings": len(all_readings)},
                        summary=f"完成 {len(all_readings)} 篇论文的结构化阅读")
        except Exception as e:
            if bb.can_retry("read"):
                bb.retry("read", error=str(e), new_strategy="仅用元数据")
                all_readings = [reading_from_paper(paper) for paper in all_papers]
                bb.complete("read", result={"readings": len(all_readings)},
                            summary=f"使用元数据完成 {len(all_readings)} 篇（无PDF深度提取）")
            else:
                bb.fail("read", error=str(e))
                return {"status": "error", "message": f"论文阅读失败: {e}", "blackboard": bb.to_dict()}

    # ── Step 3: Matrix ──
    if bb._get("matrix").status in (StepStatus.PENDING,):
        bb.start("matrix")
        try:
            matrix = build_evidence_matrix(all_papers)
            bb.complete("matrix", result={"rows": len(matrix)},
                        summary=f"构建 {len(matrix)} 行证据矩阵")
        except Exception as e:
            if bb.can_retry("matrix"):
                bb.retry("matrix", error=str(e), new_strategy="简化矩阵")
                # Simplified matrix with fewer fields
                matrix = build_evidence_matrix(all_papers[:10])
                bb.complete("matrix", result={"rows": len(matrix)},
                            summary=f"简化矩阵: {len(matrix)} 行（仅前10篇）")
            else:
                bb.fail("matrix", error=str(e))
                return {"status": "error", "message": f"证据矩阵构建失败: {e}", "blackboard": bb.to_dict()}

    # ── Step 4: Gaps ──
    if bb._get("gaps").status in (StepStatus.PENDING,):
        bb.start("gaps")
        try:
            gap_analysis = analyze_research_gaps(matrix)
            n_directions = len(gap_analysis.get("potential_innovation_directions", []))
            bb.complete("gaps", result={"directions": n_directions},
                        summary=f"发现 {n_directions} 个潜在研究方向")
        except Exception as e:
            bb.fail("gaps", error=str(e))
            gap_analysis = {"potential_innovation_directions": [], "error": str(e)}
            bb.complete("gaps", result={"directions": 0}, summary="空白分析失败，使用空结果继续")

    # ── Step 5: Review ──
    if bb._get("review").status in (StepStatus.PENDING,):
        bb.start("review")
        try:
            review = discovery_result.get("review") or fallback_review(
                str(params["query"]), matrix, gap_analysis)
            bb.complete("review", result={"length": len(review)},
                        summary=f"生成综述 ({len(review)} 字符)")
        except Exception as e:
            if bb.can_retry("review"):
                bb.retry("review", error=str(e), new_strategy="模板化综述")
                review = fallback_review(str(params["query"]), matrix, gap_analysis)
                bb.complete("review", result={"length": len(review)},
                            summary=f"使用模板生成综述 ({len(review)} 字符)")
            else:
                bb.fail("review", error=str(e))
                return {"status": "error", "message": f"综述生成失败: {e}", "blackboard": bb.to_dict()}

    # ── Step 6: Verify ──
    if bb._get("verify").status in (StepStatus.PENDING,):
        bb.start("verify")
        try:
            verification = verify_citations(review, top_papers) if top_papers else {"verdict": "no_top_papers", "issues": []}
            claim_validation = validate_review_claims(review, matrix) if discovery_result.get("review") else {"verdict": "template_review", "issues": []}
            semantic_verification = verify_citations_semantic(review, top_papers) if top_papers else {}
            references = format_reference_list(top_papers, style=str(params.get("citation_style") or "gbt7714"))
            verification_matrix = discovery_result.get("verification_matrix") or build_verification_matrix(top_papers)
            n_issues = len(verification.get("issues", []))
            bb.complete("verify", result={"issues": n_issues, "verdict": verification.get("verdict")},
                        summary=f"验证完成: {verification.get('verdict', '?')} ({n_issues} 个问题)")
        except Exception as e:
            bb.fail("verify", error=str(e))
            verification = {"verdict": "verification_failed", "issues": []}
            claim_validation = {"verdict": "skipped"}
            semantic_verification = {}
            references = format_reference_list(top_papers, style="gbt7714")
            verification_matrix = []

    # ── Step 7: Graph ──
    if bb._get("graph").status in (StepStatus.PENDING,):
        bb.start("graph")
        try:
            citation_graph = build_citation_graph(top_papers, str(params["query"]))
            bb.complete("graph", result={"nodes": citation_graph["node_count"], "edges": citation_graph["edge_count"]},
                        summary=f"构建引用图: {citation_graph['node_count']} 节点, {citation_graph['edge_count']} 边")
        except Exception as e:
            bb.fail("graph", error=str(e))
            citation_graph = {"markdown": "Graph generation failed.", "mermaid": "", "citation_mermaid": "",
                              "node_count": 0, "edge_count": 0, "nodes": [], "edges": []}

    # ── Post-processing ──
    if "引用与反幻觉验证矩阵" not in review:
        review = review.rstrip() + "\n" + format_verification_section(top_papers)
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
        "papers": all_papers,
        "top_tier_papers": top_papers,
        "other_venue_papers": other_papers,
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
        "reference_verification": {
            "verdict": "metadata_verified" if verification_matrix else "no_papers",
            "paper_count": len(verification_matrix),
            "boundary": "Metadata traceability only; full claims require full-text evidence.",
        },
        "citation_verification": verification,
        "claim_validation": claim_validation,
        "semantic_verification": semantic_verification,
        "citation_graph": citation_graph,
        "blackboard": bb.to_dict(),
        "blackboard_report": bb.progress_report(lang=params.get("lang", "zh")),
    }
    if params.get("out"):
        out = Path(str(params["out"])).expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        payload["out"] = str(out)
    return payload


def main() -> int:
    params = parse_params(sys.argv[1] if len(sys.argv) > 1 else "{}")
    action = str(params.get("action") or "run").lower()

    # ── Blackboard-aware execution ──
    use_blackboard = bool(params.get("use_blackboard", True))
    if action == "run" and use_blackboard:
        payload = _pipeline_with_blackboard(params)
        emit(payload)
        return 0

    if action == "status":
        from task_blackboard import TaskBlackboard
        task_id = params.get("task_id") or f"review_{params.get('query', 'unknown')[:30].replace(' ', '_')}"
        bb = TaskBlackboard.resume(task_id, params.get("blackboard_dir", ".blackboard"))
        if bb:
            emit({"status": "success", "blackboard": bb.to_dict(),
                  "report": bb.progress_report(lang=params.get("lang", "zh"))})
        else:
            emit({"status": "error", "message": f"No checkpoint found for {task_id}"})
        return 0

    if action == "list":
        from task_blackboard import TaskBlackboard
        checkpoints = TaskBlackboard.list_checkpoints(params.get("blackboard_dir", ".blackboard"))
        emit({"status": "success", "checkpoints": checkpoints, "total": len(checkpoints)})
        return 0

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
