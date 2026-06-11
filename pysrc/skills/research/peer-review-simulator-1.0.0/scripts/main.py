#!/usr/bin/env python3
"""peer-review-simulator v1.1.0 — structured peer review with domain-aware analysis."""

from __future__ import annotations
import json, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from research_common import compact_text, emit, fail, parse_params, venue_score

# ── Domain-specific review criteria ──────────────────────

DOMAIN_CRITERIA = {
    "ai_ml": {
        "name_zh": "AI/机器学习",
        "name_en": "AI / Machine Learning",
        "required_elements": [
            "benchmark comparison on standard datasets",
            "ablation study of key components",
            "statistical significance testing",
            "hyperparameter sensitivity analysis",
            "computational cost / efficiency metrics",
        ],
        "red_flags": [
            "no comparison to baselines published in last 2 years",
            "only tested on one dataset",
            "no error bars or standard deviation",
            "improvement < 1% without significance test",
        ],
        "venue_expectations": {
            "flagship": "Must beat SOTA on ≥2 standard benchmarks with rigorous ablation.",
            "top": "Should demonstrate clear improvement over strong baselines with ablation.",
            "ccf-a": "Must show significant contribution with thorough experimental validation.",
            "ccf-b": "Should provide solid experimental evidence and clear methodology.",
        },
    },
    "nlp": {
        "name_zh": "自然语言处理",
        "name_en": "Natural Language Processing",
        "required_elements": [
            "evaluation on multiple datasets/languages",
            "human evaluation beyond automatic metrics",
            "comparison to recent LLM-based baselines",
            "analysis of failure cases",
            "reproducibility: code, prompts, or model checkpoints",
        ],
        "red_flags": [
            "only reports BLEU/ROUGE without human eval",
            "no comparison to GPT-4 or equivalent strong baseline",
            "prompts not disclosed for LLM-based methods",
            "evaluated only on English",
        ],
        "venue_expectations": {
            "flagship": "Human eval + strong baselines + multi-dataset + open-source artifacts.",
            "top": "Thorough automatic eval + some human validation + clear contribution.",
        },
    },
    "systems": {
        "name_zh": "系统/工程",
        "name_en": "Systems / Engineering",
        "required_elements": [
            "end-to-end performance benchmarks",
            "resource utilization metrics (CPU, memory, latency)",
            "comparison to production-grade alternatives",
            "fault tolerance / error handling analysis",
            "scalability evaluation",
        ],
        "red_flags": [
            "micro-benchmarks only, no end-to-end evaluation",
            "no discussion of deployment complexity",
            "single-machine evaluation for distributed claims",
        ],
        "venue_expectations": {
            "flagship": "Production-scale evaluation with comprehensive resource analysis.",
            "top": "Thorough benchmarking with clear system design documentation.",
        },
    },
    "hci": {
        "name_zh": "人机交互",
        "name_en": "Human-Computer Interaction",
        "required_elements": [
            "user study with appropriate sample size",
            "clear participant demographics",
            "statistical analysis of user study results",
            "qualitative feedback analysis",
            "discussion of ecological validity",
        ],
        "red_flags": [
            "n < 12 without justification",
            "participants all from one demographic",
            "no qualitative analysis",
            "lab study only for real-world claims",
        ],
        "venue_expectations": {
            "flagship": "Rigorous mixed-methods study with diverse participants and longitudinal data.",
            "top": "Well-designed user study with clear methodology and analysis.",
        },
    },
    "security": {
        "name_zh": "安全/隐私",
        "name_en": "Security / Privacy",
        "required_elements": [
            "threat model definition",
            "security proof or empirical attack evaluation",
            "comparison to existing defenses/attacks",
            "discussion of assumptions and limitations",
            "responsible disclosure if applicable",
        ],
        "red_flags": [
            "no formal threat model",
            "defense evaluated against weak/outdated attacks only",
            "security-through-obscurity claims",
        ],
    },
    "theory": {
        "name_zh": "理论",
        "name_en": "Theory",
        "required_elements": [
            "formal problem statement",
            "theorem statements with proofs",
            "comparison to existing theoretical bounds",
            "discussion of assumptions and tightness",
        ],
        "red_flags": [
            "proof sketch only, no full proof",
            "assumptions not clearly stated or unrealistic",
            "no comparison to known lower/upper bounds",
        ],
    },
}

REVIEW_TEMPLATES = {
    "zh": {
        "novelty": "新颖性",
        "methodology": "方法严谨性",
        "soundness": "实验可靠性",
        "clarity": "表达清晰度",
        "related_work": "文献覆盖度",
        "impact": "影响力与意义",
        "reproducibility": "可复现性",
    },
    "en": {
        "novelty": "Novelty",
        "methodology": "Methodology",
        "soundness": "Soundness",
        "clarity": "Clarity",
        "related_work": "Related Work",
        "impact": "Impact",
        "reproducibility": "Reproducibility",
    },
}


def _detect_field(title: str, abstract: str) -> str:
    """Detect the research field from title + abstract."""
    text = f"{title} {abstract}".lower()
    scores = {
        "nlp": sum(1 for w in ["language", "nlp", "transformer", "llm", "text", "token", "translation", "summarization", "bert", "gpt", "语", "翻译", "摘要"] if w in text),
        "ai_ml": sum(1 for w in ["neural", "deep learning", "training", "gradient", "optimization", "classification", "regression", "cnn", "rnn", "神经网络", "深度", "训练", "分类"] if w in text),
        "systems": sum(1 for w in ["system", "distributed", "latency", "throughput", "scalab", "pipeline", "deploy", "kubernetes", "docker", "系统", "分布式", "延迟", "部署"] if w in text),
        "hci": sum(1 for w in ["user", "interface", "interaction", "usability", "participant", "human", "ux", "用户体验", "交互", "可用性"] if w in text),
        "security": sum(1 for w in ["security", "privacy", "attack", "defense", "adversarial", "encrypt", "vulnerab", "安全", "隐私", "攻击", "加密"] if w in text),
        "theory": sum(1 for w in ["theorem", "proof", "bound", "complexity", "convergence", "optimal", "定理", "证明", "复杂度", "收敛"] if w in text),
    }
    best = max(scores, key=scores.get)
    return best if scores[best] >= 2 else "ai_ml"


def _extract_method_section(text: str) -> str:
    """Try to extract the methodology portion of the text."""
    markers = [
        r"(?i)(?:^|\n)\s*(?:method|methodology|approach|proposed|我们的方法|方法描述|模型设计)",
        r"(?i)(?:^|\n)\s*(?:\d+[\.\)]\s*)?(?:method|approach|model architecture|框架|架构)",
    ]
    for marker in markers:
        m = re.search(marker, text)
        if m:
            start = m.start()
            # Take ~2000 chars from this point
            return text[start:start + 2000]
    return text[:2000]


def _analyze_method_rigor(text: str, field: str) -> dict:
    """Analyze methodological rigor with domain-specific checks."""
    criteria = DOMAIN_CRITERIA.get(field, DOMAIN_CRITERIA["ai_ml"])
    text_lower = text.lower()

    checks = []
    for elem in criteria["required_elements"]:
        elem_lower = elem.lower()
        # Check for keywords related to this element
        keywords = elem_lower.replace(" of ", " ").replace(" to ", " ").split()
        found = sum(1 for kw in keywords if len(kw) > 3 and kw in text_lower)
        checks.append({
            "criterion": elem,
            "satisfied": found >= 2,
            "evidence_count": found,
        })

    red_flags_found = []
    for flag in criteria["red_flags"]:
        flag_lower = flag.lower()
        keywords = flag_lower.replace("  ", " ").split()
        if sum(1 for kw in keywords if len(kw) > 3 and kw in text_lower) >= len(keywords) * 0.6:
            red_flags_found.append(flag)

    satisfied = sum(1 for c in checks if c["satisfied"])
    total = len(checks)
    score = round((satisfied / max(total, 1)) * 5, 1)
    score -= len(red_flags_found) * 0.5
    score = max(1.0, min(5.0, score))

    return {
        "score": score,
        "checks": checks,
        "red_flags": red_flags_found,
        "satisfied_count": satisfied,
        "total_count": total,
    }


def _analyze_novelty(text: str, field: str) -> dict:
    """Analyze novelty claims and positioning."""
    text_lower = text.lower()

    novelty_markers = [
        "novel", "new", "first", "propose", "introduce", "present",
        "提出", "首次", "新颖", "创新", "引入",
    ]
    comparison_markers = [
        "outperform", "state-of-the-art", "better than", "superior", "improves",
        "优于", "超越", "显著提升", "最佳",
    ]
    gap_markers = [
        "however", "but", "limitation", "gap", "lack", "missing",
        "然而", "但是", "不足", "缺乏", "空白",
    ]

    novelty_score = min(5, sum(1 for m in novelty_markers if m in text_lower) + 1)
    comparison_score = min(5, sum(1 for m in comparison_markers if m in text_lower) + 1)
    gap_score = min(5, sum(1 for m in gap_markers if m in text_lower) + 1)

    # Clear problem statement detection
    has_clear_gap = gap_score >= 3
    has_clear_contribution = novelty_score >= 3
    has_clear_comparison = comparison_score >= 3

    overall = round((novelty_score * 0.4 + comparison_score * 0.3 + gap_score * 0.3), 1)

    return {
        "score": overall,
        "has_clear_gap": has_clear_gap,
        "has_clear_contribution": has_clear_contribution,
        "has_clear_comparison": has_clear_comparison,
        "novelty_marker_count": novelty_score - 1,
        "comparison_marker_count": comparison_score - 1,
        "gap_marker_count": gap_score - 1,
    }


def _analyze_experimental_rigor(text: str, field: str) -> dict:
    """Analyze experimental design and statistical rigor."""
    text_lower = text.lower()

    has_stats = any(w in text_lower for w in [
        "p-value", "p value", "p <", "p<", "significant", "confidence interval",
        "t-test", "anova", "wilcoxon", "bootstrap", "std", "standard deviation",
        "显著", "p值", "置信区间", "标准差",
    ])
    has_ablation = any(w in text_lower for w in [
        "ablation", "ablate", "removing", "without", "component",
        "消融", "移除", "去掉",
    ])
    has_multiple_datasets = len(re.findall(r'(?:dataset|benchmark|corpus|数据[集库])', text_lower)) >= 2
    has_error_bars = any(w in text_lower for w in [
        "error bar", "±", "std", "standard deviation", "variance",
        "误差", "标准差",
    ])

    score = 2.0
    if has_stats: score += 0.8
    if has_ablation: score += 0.8
    if has_multiple_datasets: score += 0.7
    if has_error_bars: score += 0.5
    score = min(5.0, score)

    return {
        "score": round(score, 1),
        "has_statistical_tests": has_stats,
        "has_ablation": has_ablation,
        "has_multiple_datasets": has_multiple_datasets,
        "has_error_bars": has_error_bars,
    }


def _analyze_clarity(text: str) -> dict:
    """Analyze writing clarity and structure."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

    has_sections = bool(re.search(r'(?i)(?:introduction|related work|method|experiment|conclusion|引言|相关|方法|实验|结论)', text))
    avg_para_len = sum(len(p) for p in paragraphs) / max(len(paragraphs), 1) if paragraphs else 0

    # Good paragraphs are 100-500 chars
    well_sized = sum(1 for p in paragraphs if 100 <= len(p) <= 500)
    well_sized_ratio = well_sized / max(len(paragraphs), 1) if paragraphs else 0

    score = 3.0
    if has_sections: score += 0.8
    if 0.5 <= well_sized_ratio <= 0.9: score += 0.7
    if avg_para_len > 200: score += 0.5

    return {
        "score": round(min(5.0, score), 1),
        "has_structured_sections": has_sections,
        "avg_paragraph_length": round(avg_para_len),
        "well_sized_paragraph_ratio": round(well_sized_ratio, 2),
        "paragraph_count": len(paragraphs),
    }


def _analyze_related_work(text: str) -> dict:
    """Analyze reference and related work quality."""
    # Count citation patterns [1], [1,2], [1-3]
    citations = re.findall(r'\[\d+(?:[,;\s]*\d+)*\]', text)
    ref_count = len(citations)

    # Check for recent citations (2022-2026)
    recent_years = len(re.findall(r'(?:202[2-6]|202[2-6][a-z])', text.lower()))

    # Check for venue-quality citations
    has_top_venue_refs = bool(re.search(
        r'(?i)(?:neurips|icml|iclr|acl|emnlp|cvpr|iccv|aaai|nature|science|'
        r'tpami|jmlr|acm transactions|ieee transactions)',
        text
    ))

    score = 2.0
    if ref_count >= 30: score += 1.5
    elif ref_count >= 15: score += 1.0
    elif ref_count >= 5: score += 0.5
    if recent_years >= 5: score += 0.5
    if has_top_venue_refs: score += 0.5

    return {
        "score": round(min(5.0, score), 1),
        "citation_count": ref_count,
        "recent_citations": recent_years,
        "has_top_venue_references": has_top_venue_refs,
    }


def _generate_detailed_review(
    novelty: dict, method: dict, experiment: dict,
    clarity: dict, related: dict, field: str, lang: str
) -> dict:
    """Generate a detailed, actionable review."""
    criteria = DOMAIN_CRITERIA.get(field, DOMAIN_CRITERIA["ai_ml"])
    templates = REVIEW_TEMPLATES.get(lang, REVIEW_TEMPLATES["en"])

    strengths = []
    weaknesses = []
    suggestions = []

    # Novelty assessment
    if novelty["has_clear_contribution"] and novelty["has_clear_gap"]:
        strengths.append({
            "dimension": "novelty",
            "text": "论文有明确的问题陈述和创新贡献定位。" if lang == "zh" else "Clear problem statement and well-positioned contribution.",
        })
    else:
        if not novelty["has_clear_gap"]:
            weaknesses.append({
                "dimension": "novelty",
                "text": "研究动机不够清晰——未明确指出当前方法的局限性或研究空白。" if lang == "zh" else "Research motivation unclear — the gap this work addresses is not explicitly stated.",
            })
            suggestions.append({
                "dimension": "novelty",
                "text": "在引言中明确列出 2-3 条当前方法的不足，并说明本文如何解决这些问题。" if lang == "zh" else "Explicitly list 2-3 limitations of current approaches and how this work addresses them.",
            })

    # Methodology assessment
    if method["satisfied_count"] >= 3:
        strengths.append({
            "dimension": "methodology",
            "text": f"方法描述较完整（{method['satisfied_count']}/{method['total_count']} 项关键要素满足）。" if lang == "zh" else f"Methodology is reasonably complete ({method['satisfied_count']}/{method['total_count']} key elements present).",
        })

    for check in method["checks"]:
        if not check["satisfied"]:
            weaknesses.append({
                "dimension": "methodology",
                "text": f"缺少: {check['criterion']}" if lang == "zh" else f"Missing: {check['criterion']}",
            })

    for flag in method["red_flags"]:
        weaknesses.append({
            "dimension": "methodology",
            "text": f"⚠️ 红旗: {flag}" if lang == "zh" else f"⚠️ Red flag: {flag}",
        })
        suggestions.append({
            "dimension": "methodology",
            "text": f"建议: 解决 '{flag}' 问题。" if lang == "zh" else f"Suggestion: Address the '{flag}' concern.",
        })

    # Experimental assessment
    if experiment["score"] >= 3.5:
        strengths.append({
            "dimension": "experiment",
            "text": "实验设计较为严谨，包含充分的验证。" if lang == "zh" else "Experimental design is reasonably rigorous with adequate validation.",
        })
    if not experiment["has_statistical_tests"]:
        weaknesses.append({
            "dimension": "experiment",
            "text": "未报告统计显著性检验。" if lang == "zh" else "No statistical significance tests reported.",
        })
        suggestions.append({
            "dimension": "experiment",
            "text": "添加统计显著性检验（如 t-test、bootstrap）来支持实验结论。" if lang == "zh" else "Add statistical significance tests to support experimental claims.",
        })
    if not experiment["has_ablation"]:
        suggestions.append({
            "dimension": "experiment",
            "text": "增加消融实验，分析各个组件的贡献。" if lang == "zh" else "Include ablation studies to analyze contribution of each component.",
        })

    # Clarity assessment
    if clarity["has_structured_sections"]:
        strengths.append({
            "dimension": "clarity",
            "text": "论文结构清晰，有明确的章节划分。" if lang == "zh" else "Paper has clear section structure.",
        })

    # Related work
    if related["citation_count"] < 5:
        weaknesses.append({
            "dimension": "related_work",
            "text": "参考文献数量严重不足。" if lang == "zh" else "Reference count is critically low.",
        })
        suggestions.append({
            "dimension": "related_work",
            "text": "补充至少 15-20 篇相关文献，特别是近两年的顶会/顶刊论文。" if lang == "zh" else "Add at least 15-20 relevant references, especially recent top-venue papers.",
        })
    elif related["citation_count"] < 15:
        suggestions.append({
            "dimension": "related_work",
            "text": "建议补充近两年的最新相关工作。" if lang == "zh" else "Consider adding more recent related work from the last 2 years.",
        })

    # Compute overall score
    dim_scores = {
        "novelty": novelty["score"],
        "methodology": method["score"],
        "experiment": experiment["score"],
        "clarity": clarity["score"],
        "related_work": related["score"],
    }
    overall = round(sum(dim_scores.values()) / len(dim_scores), 1)

    # Recommendation
    if overall >= 4.0:
        recommendation = "Accept" if lang == "en" else "接收 (Accept)"
    elif overall >= 3.0:
        recommendation = "Minor Revision" if lang == "en" else "小修 (Minor Revision)"
    elif overall >= 2.0:
        recommendation = "Major Revision" if lang == "en" else "大修 (Major Revision)"
    else:
        recommendation = "Reject" if lang == "en" else "拒稿 (Reject)"

    # Venue-specific expectation
    venue_expectation = ""
    for tier_key, expectation in criteria.get("venue_expectations", {}).items():
        venue_expectation = expectation
        break
    if not venue_expectation:
        venue_expectation = "Should demonstrate clear contribution with adequate experimental validation."

    return {
        "recommendation": recommendation,
        "overall_score": overall,
        "dimension_scores": {templates.get(k, k): v for k, v in dim_scores.items()},
        "strengths": strengths[:5],
        "weaknesses": weaknesses[:8],
        "suggestions": suggestions[:6],
        "field": field,
        "field_name": criteria.get("name_zh" if lang == "zh" else "name_en", field),
        "venue_expectation": venue_expectation,
        "summary": _generate_summary(overall, strengths, weaknesses, lang),
    }


def _generate_summary(overall: float, strengths: list, weaknesses: list, lang: str) -> str:
    """Generate a natural-language review summary."""
    if lang == "zh":
        if overall >= 4.0:
            return "这是一篇质量较高的论文，在多个评审维度上表现良好。建议重点关注实验验证的完整性和最新文献的覆盖。"
        elif overall >= 3.0:
            return "论文有明确的研究动机和创新点，但在方法描述和实验设计上需要进一步强化。修改后有望达到发表标准。"
        elif overall >= 2.0:
            return f"论文存在{len(weaknesses)}个需要解决的关键问题。建议大幅修改实验设计、补充文献综述、并强化方法描述。"
        else:
            return "论文在多个核心维度上存在严重不足。建议重新审视研究问题、实验设计和文献定位后再投稿。"
    else:
        if overall >= 4.0:
            return "This is a strong submission with solid contributions across multiple dimensions. Focus on completeness of experimental validation and coverage of recent literature."
        elif overall >= 3.0:
            return "The paper has clear motivation and contribution, but needs strengthening in methodology description and experimental design. Revisions should bring it to publication standard."
        elif overall >= 2.0:
            return f"The paper has {len(weaknesses)} key issues to address. Major revisions needed for experimental design, literature coverage, and methodology description."
        else:
            return "The paper has significant deficiencies across core dimensions. Recommend revisiting the research question, experimental design, and literature positioning before resubmission."


def main():
    params = parse_params(sys.argv[1] if len(sys.argv) > 1 else "{}")
    lang = params.get("lang", "zh")
    strictness = params.get("strictness", "standard")
    title = params.get("title", "")
    abstract = str(params.get("abstract", ""))
    draft = str(params.get("draft", ""))
    venue_type = params.get("venue_type", "journal")
    field_override = params.get("field", "")

    # Load draft from file if needed
    if draft and Path(draft).exists():
        draft = Path(draft).read_text(encoding="utf-8", errors="replace")

    text = f"{title}\n{abstract}\n{draft[:8000]}"
    if not text.strip():
        fail("Provide abstract or draft text for review.")

    # Detect research field
    field = field_override if field_override else _detect_field(title, abstract)

    # Run analyses
    novelty = _analyze_novelty(text, field)
    method = _analyze_method_rigor(text, field)
    experiment = _analyze_experimental_rigor(text, field)
    clarity = _analyze_clarity(text)
    related = _analyze_related_work(text)

    # Adjust for strictness
    strictness_adj = {"friendly": 0.3, "standard": 0, "critical": -0.4}
    adj = strictness_adj.get(strictness, 0)

    # Generate review
    review = _generate_detailed_review(novelty, method, experiment, clarity, related, field, lang)
    review["overall_score"] = round(max(1.0, min(5.0, review["overall_score"] + adj)), 1)
    review["strictness"] = strictness
    review["venue_type"] = venue_type

    # Add detailed analysis for transparency
    review["detailed_analysis"] = {
        "novelty": novelty,
        "method_rigor": method,
        "experimental_rigor": experiment,
        "clarity": clarity,
        "related_work": related,
    }

    review["note"] = (
        "基于文本分析的自动评审。包含领域感知的检查清单和红旗检测。"
        "对于关键决策（如拒稿），建议结合人工评审。"
        if lang == "zh" else
        "Automated review based on text analysis with domain-aware checklists and red-flag detection. "
        "For critical decisions (reject), human review is recommended."
    )

    emit({"status": "success", **review})


if __name__ == "__main__":
    raise SystemExit(main())
