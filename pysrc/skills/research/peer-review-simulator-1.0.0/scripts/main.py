#!/usr/bin/env python3
"""peer-review-simulator v1.0.0 — structured peer review simulation."""

from __future__ import annotations
import json, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from research_common import compact_text, emit, fail, parse_params

REVIEW_TEMPLATES = {
    "zh": {
        "novelty_checklist": ["贡献是否明确陈述？", "与 SOTA 的对比是否充分？", "创新点是否足够？"],
        "method_checklist": ["方法描述是否清晰可复现？", "实验设计是否合理？", "是否包含消融实验？"],
        "soundness_checklist": ["结论是否有证据支撑？", "统计分析是否恰当？", "是否讨论了局限性？"],
        "clarity_checklist": ["写作是否清晰？", "图表是否清晰标注？", "逻辑流是否连贯？"],
        "related_work_checklist": ["关键文献是否引用？", "与先前工作的定位是否清晰？", "是否遗漏重要相关工作？"],
        "impact_checklist": ["对该领域是否有影响力？", "是否有实际应用价值？", "是否开辟新方向？"],
    },
    "en": {
        "novelty_checklist": ["Is the contribution clearly stated?", "Is comparison to SOTA adequate?", "Is the novelty sufficient?"],
        "method_checklist": ["Is the method described clearly and reproducibly?", "Is the experimental design sound?", "Are ablation studies included?"],
        "soundness_checklist": ["Are claims supported by evidence?", "Is statistical analysis appropriate?", "Are limitations discussed?"],
        "clarity_checklist": ["Is the writing clear?", "Are figures/tables well-labeled?", "Is the logical flow coherent?"],
        "related_work_checklist": ["Are key references cited?", "Is positioning vs prior work clear?", "Are important related works missing?"],
        "impact_checklist": ["Would this influence the field?", "Is there practical value?", "Does it open new directions?"],
    },
}


def _heuristic_score(text: str, checklist: list) -> dict:
    """Score based on heuristic text analysis."""
    text_lower = text.lower()
    scores = {}
    for item in checklist:
        item_lower = item.lower()
        # Check for related keywords
        if "contribution" in item_lower or "novel" in item_lower:
            kw = ["novel", "new", "propose", "first", "contribution", "state-of-the-art", "优于", "首次", "提出", "新颖"]
            score = min(5, sum(1 for w in kw if w in text_lower) + 2)
        elif "method" in item_lower or "reproduc" in item_lower:
            kw = ["algorithm", "architecture", "framework", "pipeline", "implementation", "code", "github", "hyperparameter", "pseudocode", "方法", "框架", "算法"]
            score = min(5, sum(1 for w in kw if w in text_lower) + 2)
        elif "soundness" in item_lower or "evidence" in item_lower or "statistical" in item_lower:
            kw = ["experiment", "result", "ablation", "p-value", "significance", "baseline", "compare", "实验", "结果", "显著"]
            score = min(5, sum(1 for w in kw if w in text_lower) + 2)
        elif "clarity" in item_lower or "writing" in item_lower:
            score = 4 if len(text) > 500 else 3  # Longer text = more detail
        elif "related work" in item_lower or "reference" in item_lower:
            ref_count = len(re.findall(r'\[\d+\]|\[\d+[,;]\s*\d+\]', text))
            score = min(5, max(1, ref_count // 5 + 1))
        elif "impact" in item_lower:
            kw = ["impact", "significant", "important", "state-of-the-art", "benchmark", "real-world", "application", "重要", "显著", "应用"]
            score = min(5, sum(1 for w in kw if w in text_lower) + 2)
        else:
            score = 3
        scores[item] = score
    return scores


def _generate_suggestions(scores: dict, lang: str) -> list:
    suggestions = []
    for item, score in scores.items():
        if score <= 2:
            if "contribution" in item.lower() or "novel" in item.lower():
                suggestions.append({"dimension": "novelty", "issue": "贡献不够清晰", "suggestion": "在引言末尾明确列出 2-3 条核心贡献，并与现有工作对比。"} if lang == "zh" else {"dimension": "novelty", "issue": "Contribution unclear", "suggestion": "List 2-3 core contributions explicitly at the end of the introduction."})
            elif "method" in item.lower():
                suggestions.append({"dimension": "methodology", "issue": "方法描述不够详细", "suggestion": "补充伪代码或算法流程图，明确超参数设置和实验配置。"} if lang == "zh" else {"dimension": "methodology", "issue": "Method underdescribed", "suggestion": "Add pseudocode, specify all hyperparameters, and detail the experimental setup."})
            elif "soundness" in item.lower() or "evidence" in item.lower():
                suggestions.append({"dimension": "soundness", "issue": "实验证据不足", "suggestion": "增加消融实验、误差分析、或统计显著性检验。"} if lang == "zh" else {"dimension": "soundness", "issue": "Insufficient evidence", "suggestion": "Add ablation studies, error analysis, or statistical significance tests."})
            elif "related" in item.lower():
                suggestions.append({"dimension": "related_work", "issue": "文献引用不足", "suggestion": "补充最新的相关工作，特别是在目标期刊/会议近两年发表的论文。"} if lang == "zh" else {"dimension": "related_work", "issue": "Insufficient references", "suggestion": "Add recent related work, especially papers published in the target venue in the last 2 years."})
    return suggestions


def main():
    params = parse_params(sys.argv[1] if len(sys.argv) > 1 else "{}")
    lang = params.get("lang", "zh")
    strictness = params.get("strictness", "standard")
    title = params.get("title", "")
    abstract = str(params.get("abstract", ""))
    draft = str(params.get("draft", ""))

    # Load draft from file if needed
    if draft and Path(draft).exists():
        draft = Path(draft).read_text(encoding="utf-8", errors="replace")

    text = f"{title}\n{abstract}\n{draft[:5000]}"
    if not text.strip():
        fail("Provide abstract or draft text for review.")

    templates = REVIEW_TEMPLATES.get(lang, REVIEW_TEMPLATES["en"])
    all_scores = {}
    for dim, checklist in templates.items():
        dim_name = dim.replace("_checklist", "")
        scores = _heuristic_score(text, checklist)
        all_scores[dim_name] = scores

    # Aggregate
    dim_avg = {dim: round(sum(s.values()) / max(len(s), 1), 1) for dim, s in all_scores.items()}
    overall = round(sum(dim_avg.values()) / max(len(dim_avg), 1), 1)

    # Recommendation
    strictness_adj = {"friendly": 0.5, "standard": 0, "critical": -0.5}
    adj = strictness_adj.get(strictness, 0)
    adjusted = overall + adj
    if adjusted >= 4:
        recommendation = "Accept" if lang == "en" else "接收"
    elif adjusted >= 3:
        recommendation = "Minor Revision" if lang == "en" else "小修"
    elif adjusted >= 2:
        recommendation = "Major Revision" if lang == "en" else "大修"
    else:
        recommendation = "Reject" if lang == "en" else "拒稿"

    suggestions = _generate_suggestions({k: v for dim in all_scores.values() for k, v in dim.items()}, lang)

    # Strengths and weaknesses
    strengths_dims = [d for d, s in dim_avg.items() if s >= 4]
    weakness_dims = [d for d, s in dim_avg.items() if s <= 2.5]

    emit({
        "status": "success",
        "recommendation": recommendation,
        "overall_score": overall,
        "adjusted_score": round(adjusted, 1),
        "strictness": strictness,
        "dimension_scores": dim_avg,
        "detailed_scores": all_scores,
        "strengths": strengths_dims,
        "weaknesses": weakness_dims,
        "suggestions": suggestions,
        "note": "Heuristic review — based on text analysis, not LLM. For deeper review, use an LLM-backed pipeline." if lang == "zh" else "Heuristic review — for deeper analysis, use LLM-backed review.",
    })


if __name__ == "__main__":
    raise SystemExit(main())
