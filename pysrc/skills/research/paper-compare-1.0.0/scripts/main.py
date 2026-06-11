#!/usr/bin/env python3
"""paper-compare v1.0.0 — side-by-side paper comparison."""

from __future__ import annotations
import json, sys, re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from research_common import compact_text, emit, fail, normalize_paper, papers_from_params, parse_params, reading_from_paper

def main():
    params = parse_params(sys.argv[1] if len(sys.argv) > 1 else "{}")
    papers = papers_from_params(params)
    if not papers and params.get("readings"):
        papers = [normalize_paper({**r, "title": r.get("title", "")}, i) for i, r in enumerate(params["readings"]) if isinstance(r, dict)]
    if not papers and params.get("path"):
        v = json.loads(Path(params["path"]).read_text(encoding="utf-8", errors="replace"))
        papers = papers_from_params({"papers": v}) if isinstance(v, list) else papers_from_params(v)
    if len(papers) < 2:
        fail("Need at least 2 papers to compare.")

    lang = params.get("lang", "zh")
    readings = [reading_from_paper(p) for p in papers]

    # Build comparison
    dims = {
        "problem": [r.get("core_problem", "")[:200] for r in readings],
        "method": [r.get("method_category", "") for r in readings],
        "contribution": [r.get("contribution_type", "") for r in readings],
        "findings": [r.get("key_findings", "")[:300] for r in readings],
        "limitations": [r.get("limitations", "")[:200] for r in readings],
        "evidence_strength": [r.get("evidence_strength", "") for r in readings],
    }

    # Check for agreements/contradictions
    signals = []
    for i in range(len(papers)):
        for j in range(i+1, len(papers)):
            if dims["method"][i] and dims["method"][i] == dims["method"][j]:
                fi = dims["findings"][i].lower()
                fj = dims["findings"][j].lower()
                neg_words = ["not", "no", "fail", "cannot", "contrary", "inconsistent"]
                if any(w in fi for w in neg_words) != any(w in fj for w in neg_words):
                    signals.append({"type": "possible_contradiction", "paper_a": i+1, "paper_b": j+1,
                                    "note": "Same method, potentially opposing conclusions"})
            if dims["contribution"][i] and dims["contribution"][i] == dims["contribution"][j]:
                signals.append({"type": "similar_contribution", "paper_a": i+1, "paper_b": j+1,
                                "note": "Both claim similar contribution type — check for novelty overlap"})

    # Complementary reading hint
    strengths = []
    for i, r in enumerate(readings):
        s = []
        if r.get("method_category"):
            s.append(f"method={r['method_category']}")
        if r.get("evidence_strength") in ("strong", "experimental"):
            s.append("strong_evidence")
        if r.get("limitations"):
            s.append("discusses_limitations")
        strengths.append((i+1, s))

    result = {
        "status": "success", "papers_count": len(papers),
        "papers": [{"index": i+1, "title": p.get("title", "")[:120], "year": p.get("year", ""),
                     "venue": p.get("venue", ""), "authors": str(p.get("authors", ""))[:100]} for i, p in enumerate(papers)],
        "comparison": {d: [{"index": i+1, "title": papers[i].get("title", "")[:80], "value": dims[d][i]} for i in range(len(papers))] for d in dims},
        "signals": signals,
        "complementary_reading": " & ".join([f"Paper {idx} ({'+'.join(s)})" for idx, s in strengths if s]) if strengths else "Read all papers for full picture.",
    }
    if lang == "zh":
        result["title_zh"] = "论文对比分析"
        for s in signals:
            s["note_zh"] = s["note"]
    emit(result)

if __name__ == "__main__":
    raise SystemExit(main())
