from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from research_common import analyze_research_gaps, build_evidence_matrix, emit, evidence_to_csv, export_csv, fail, normalize_paper, papers_from_params, parse_params


def papers_from_readings(readings):
    papers = []
    for idx, item in enumerate(readings or []):
        if isinstance(item, dict):
            papers.append(normalize_paper({
                "id": item.get("id") or str(idx + 1),
                "title": item.get("title"),
                "year": item.get("year"),
                "venue": item.get("venue"),
                "doi": item.get("doi"),
                "url": item.get("url"),
                "abstract": item.get("evidence_text") or item.get("key_findings") or item.get("core_problem"),
            }, idx))
    return papers


def _load_papers(params: dict) -> list:
    papers = papers_from_params(params)
    if not papers and params.get("readings"):
        papers = papers_from_readings(params.get("readings"))
    if not papers and params.get("path"):
        value = json.loads(Path(str(params["path"])).read_text(encoding="utf-8", errors="replace"))
        papers = papers_from_readings(value.get("readings")) if isinstance(value, dict) else []
        if not papers:
            params["papers"] = value
            papers = papers_from_params(params)
    return papers


def _normalize_matrix(data) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("matrix") or data.get("rows") or []
    return []


def _compare_matrices(a: list, b: list) -> dict:
    """Compare two evidence matrices."""
    titles_a = {row.get("title", "").lower()[:80] for row in a}
    titles_b = {row.get("title", "").lower()[:80] for row in b}
    common = titles_a & titles_b
    only_a = titles_a - titles_b
    only_b = titles_b - titles_a

    methods_a = {row.get("method", row.get("method_category", "")) for row in a}
    methods_b = {row.get("method", row.get("method_category", "")) for row in b}

    years_a = [row.get("year") for row in a if row.get("year")]
    years_b = [row.get("year") for row in b if row.get("year")]

    return {
        "matrix_a_count": len(a),
        "matrix_b_count": len(b),
        "papers_in_both": len(common),
        "papers_only_in_a": len(only_a),
        "papers_only_in_b": len(only_b),
        "overlap_ratio": round(len(common) / max(len(titles_a | titles_b), 1), 3),
        "methods_shared": sorted(methods_a & methods_b),
        "methods_unique_to_a": sorted(methods_a - methods_b),
        "methods_unique_to_b": sorted(methods_b - methods_a),
        "year_range_a": f"{min(years_a)}-{max(years_a)}" if years_a else "N/A",
        "year_range_b": f"{min(years_b)}-{max(years_b)}" if years_b else "N/A",
        "sample_only_a": [row.get("title", "")[:100] for row in a if row.get("title", "").lower()[:80] in only_a][:5],
        "sample_only_b": [row.get("title", "")[:100] for row in b if row.get("title", "").lower()[:80] in only_b][:5],
    }


def _find_contradictions(matrix: list) -> list[dict]:
    """Heuristic contradiction detection across evidence matrix rows."""
    contradictions = []
    for i, row_a in enumerate(matrix):
        for j, row_b in enumerate(matrix):
            if j <= i:
                continue
            signals = []
            # Same method, different conclusion
            method_a = str(row_a.get("method", row_a.get("method_category", "")))
            method_b = str(row_b.get("method", row_b.get("method_category", "")))
            if method_a and method_a == method_b:
                findings_a = str(row_a.get("findings", row_a.get("key_findings", ""))).lower()
                findings_b = str(row_b.get("findings", row_b.get("key_findings", ""))).lower()
                # Check for negation signals
                neg_markers = ["not", "does not", "fails to", "no significant", "no evidence",
                               "cannot", "unable", "contrary", "opposite", "inconsistent"]
                has_neg_a = any(m in findings_a for m in neg_markers)
                has_neg_b = any(m in findings_b for m in neg_markers)
                if has_neg_a != has_neg_b:
                    signals.append("opposing_conclusions")

            # Same benchmark, different scores → potential evaluation conflict
            title_text = str(row_a.get("title", "")) + " " + str(row_b.get("title", ""))
            bench_overlap = _benchmark_overlap(
                str(row_a.get("abstract", row_a.get("findings", ""))),
                str(row_b.get("abstract", row_b.get("findings", ""))),
            )
            if bench_overlap:
                signals.append(f"shared_benchmark:{bench_overlap}")

            if signals:
                contradictions.append({
                    "paper_a": (row_a.get("title") or f"Paper {i+1}")[:120],
                    "paper_b": (row_b.get("title") or f"Paper {j+1}")[:120],
                    "signals": signals,
                    "confidence": "medium" if len(signals) >= 2 else "low",
                })

    return contradictions[:20]


def _benchmark_overlap(text_a: str, text_b: str) -> str:
    common_benchmarks = {"ImageNet", "CIFAR-10", "CIFAR-100", "COCO", "SQuAD", "GLUE",
                         "MNIST", "LibriSpeech", "WMT", "KITTI", "nuScenes", "MMLU"}
    found_a = {b for b in common_benchmarks if b.lower() in text_a.lower()}
    found_b = {b for b in common_benchmarks if b.lower() in text_b.lower()}
    overlap = found_a & found_b
    return ",".join(sorted(overlap)[:3]) if overlap else ""


def main() -> int:
    params = parse_params(sys.argv[1] if len(sys.argv) > 1 else "{}")
    action = str(params.get("action") or "build").strip().lower()

    if action == "compare":
        a = _normalize_matrix(params.get("matrix_a") or params.get("papers") or [])
        b = _normalize_matrix(params.get("matrix_b") or [])
        if not a:
            a = build_evidence_matrix(_load_papers({**params, "papers": params.get("matrix_a") or params.get("papers")}))
        if not b:
            fail("Provide matrix_b for comparison (second evidence matrix).")
        result = _compare_matrices(a, b if isinstance(b, list) else _normalize_matrix(b))
        result["status"] = "success"
        emit(result)
        return 0

    if action == "contradictions":
        papers = _load_papers(params)
        if not papers:
            fail("No papers found for contradiction analysis.")
        matrix = build_evidence_matrix(papers)
        conflicts = _find_contradictions(matrix)
        emit({
            "status": "success",
            "completed": True,
            "total_papers": len(papers),
            "contradictions_found": len(conflicts),
            "contradictions": conflicts,
            "note": "Heuristic detection — review flagged pairs manually. Low confidence = weak signal only.",
        })
        return 0

    # Default: build
    papers = _load_papers(params)
    if not papers:
        fail("No papers/readings found for evidence matrix.")
    matrix = build_evidence_matrix(papers)
    output_format = str(params.get("output_format") or "json").lower()
    if output_format == "csv":
        csv_text = evidence_to_csv(matrix)
        csv_payload = {
            "status": "success", "completed": True, "count": len(matrix),
            "format": "csv", "csv": csv_text,
            "gap_analysis": analyze_research_gaps(matrix),
        }
        if params.get("out"):
            out = Path(str(params["out"])).expanduser().resolve()
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(csv_text, encoding="utf-8")
            csv_payload["out"] = str(out)
        emit(csv_payload)
        return 0
    payload = {
        "status": "success", "completed": True, "count": len(matrix),
        "matrix": matrix, "gap_analysis": analyze_research_gaps(matrix),
    }
    if params.get("out"):
        out = Path(str(params["out"])).expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        payload["out"] = str(out)
    emit(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


