from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> None:
    try:
        params = json.loads(sys.argv[1] if len(sys.argv) > 1 else "{}")
    except json.JSONDecodeError as exc:
        print(json.dumps({"status": "error", "message": f"Invalid JSON: {exc}", "completed": False}, ensure_ascii=False))
        raise SystemExit(1)

    title = params.get("title", "")
    abstract = params.get("abstract", "")
    keywords = params.get("keywords", [])
    field = params.get("field", "")
    top_k = min(int(params.get("top_k", 5)), 15)
    used = params.get("used_journals", [])
    include_conferences = params.get("include_conferences", True)
    lang = params.get("lang", "zh")

    action = params.get("action", "match")

    if action == "deadlines":
        field = params.get("field", "")
        top_k = min(int(params.get("top_k", 10)), 20)
        include_conferences = params.get("include_conferences", True)
        lang = params.get("lang", "zh")

        from matcher import JournalMatcher
        matcher = JournalMatcher(base_dir=str(Path(__file__).parent))
        # Get all journals/conferences in the field
        results = matcher.match(
            title=title or field or "research", abstract=abstract or field or "",
            keywords=keywords, field=field, top_k=top_k,
            include_conferences=include_conferences,
        )
        deadlines = _estimate_deadlines(results, lang)
        print(json.dumps({"status": "success", "completed": True, "deadlines": deadlines, "total": len(deadlines)}, ensure_ascii=False, indent=2))
        return

    if not title and not abstract:
        print(json.dumps({"status": "error", "message": "Missing title or abstract.", "completed": False}, ensure_ascii=False))
        raise SystemExit(1)

    from matcher import JournalMatcher
    matcher = JournalMatcher(base_dir=str(Path(__file__).parent))

    results = matcher.match(
        title=title,
        abstract=abstract,
        keywords=keywords,
        field=field,
        top_k=top_k,
        used_journals=used,
        include_conferences=include_conferences,
    )

    # Enhance with online data
    if params.get("online", True):
        from matcher import enhance_with_online
        results = enhance_with_online(results, top_k)

    if lang == "zh":
        output = format_results_zh(results)
    else:
        output = format_results_en(results)

    print(json.dumps({"status": "success", "completed": True, "result": output}, ensure_ascii=False, indent=2))


def format_results_zh(results: list) -> dict:
    return {
        "recommendations": [{
            "rank": i + 1,
            "name": r["name"],
            "type": "期刊" if r["type"] == "journal" else "会议",
            "match_score": round(r["score"] * 100, 1),
            "impact_factor": r.get("if_latest", "-"),
            "jcr_quartile": r.get("jcr", "-"),
            "cas_quartile": r.get("cas", "-"),
            "ccf_level": r.get("ccf", "-"),
            "review_cycle": f"{r['review_months'][0]}-{r['review_months'][1]} 个月" if r.get("review_months") else "-",
            "acceptance_rate": r.get("acceptance", "-"),
            "match_reason": r.get("match_reason", ""),
            "link": r.get("openalex_id", "") or (f"https://ieeexplore.ieee.org/xpl/RecentIssue.jsp?punumber={r.get('punumber', '')}" if r.get("punumber") else f"https://scholar.google.com/scholar?q={r['name']}"),
        } for i, r in enumerate(results)],
        "total_matched": len(results),
        "note": "数据基于内置期刊画像库，建议投稿前核实官方最新信息。"
    }


def format_results_en(results: list) -> dict:
    return {
        "recommendations": [{
            "rank": i + 1,
            "name": r["name"],
            "type": "Journal" if r["type"] == "journal" else "Conference",
            "match_score": round(r["score"] * 100, 1),
            "impact_factor": r.get("if_latest", "-"),
            "jcr_quartile": r.get("jcr", "-"),
            "cas_quartile": r.get("cas", "-"),
            "ccf_level": r.get("ccf", "-"),
            "review_cycle": f"{r['review_months'][0]}-{r['review_months'][1]} months" if r.get("review_months") else "-",
            "acceptance_rate": r.get("acceptance", "-"),
            "match_reason": r.get("match_reason", ""),
            "link": r.get("openalex_id", "") or (f"https://ieeexplore.ieee.org/xpl/RecentIssue.jsp?punumber={r.get('punumber', '')}" if r.get("punumber") else f"https://scholar.google.com/scholar?q={r['name']}"),
        } for i, r in enumerate(results)],
        "total_matched": len(results),
        "note": "Data based on built-in journal profiles; verify against official sources before submission."
    }


def _estimate_deadlines(results: list, lang: str = "zh") -> list:
    """Estimate upcoming deadlines from review cycle info."""
    from datetime import datetime, timedelta
    now = datetime.now()
    deadlines = []
    for r in results:
        cycle = r.get("review_months") or [3, 6]
        is_conf = r.get("type") == "conference"
        # Estimate next deadline
        if is_conf:
            # Conferences: typical annual cycle, estimate based on typical conf months
            conf_months = [1, 3, 6, 7, 9, 11]  # Common submission months
            future = [datetime(now.year, m, 15) for m in conf_months if datetime(now.year, m, 15) > now]
            if not future:
                future = [datetime(now.year + 1, m, 15) for m in conf_months[:1]]
            next_deadline = min(future)
        else:
            # Journals: rolling submissions (most common) or quarterly
            if cycle[0] <= 2:
                next_deadline = now + timedelta(days=30)  # Monthly/rolling → next month
            else:
                quarter = ((now.month - 1) // 3 + 1) * 3 + 1
                if quarter > 12:
                    next_deadline = datetime(now.year + 1, quarter - 12, 1)
                else:
                    next_deadline = datetime(now.year, quarter, 1)
                if next_deadline <= now:
                    next_deadline = datetime(now.year + 1, max(1, next_deadline.month - 9), 1)

        name = r.get("name", "")
        label = "会议" if is_conf else "期刊"
        deadlines.append({
            "name": name,
            "type": label,
            "next_deadline_estimate": next_deadline.strftime("%Y-%m-%d"),
            "review_cycle": f"{cycle[0]}-{cycle[1]} 个月" if not is_conf else "会议周期",
            "acceptance_rate": r.get("acceptance", "-"),
            "ccf_level": r.get("ccf", "-"),
            "jcr_quartile": r.get("jcr", "-"),
            "match_score": round(r.get("score", 0) * 100, 1),
            "note": "基于典型周期估算，请以官网 Call for Papers 为准。" if lang == "zh" else "Estimated from typical cycles; verify at official CFP.",
        })
    deadlines.sort(key=lambda d: d["next_deadline_estimate"])
    return deadlines


if __name__ == "__main__":
    main()


