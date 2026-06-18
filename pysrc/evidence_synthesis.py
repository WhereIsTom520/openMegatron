"""
Evidence Synthesis Engine - 证据合成引擎

核心能力:
1. PICO/PECO 框架提取 (Population, Intervention, Comparison, Outcome)
2. 跨论文证据对齐 (同一 outcome 下不同研究的效应方向)
3. 矛盾证据检测与调和
4. 证据强度加权整合 (结合研究设计 + 样本量 + 偏倚风险)
5. 简单 Meta 分析: 可比较研究的效应量合并
6. 森林图生成
"""

from __future__ import annotations
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Set
from collections import defaultdict

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False


@dataclass
class PICOElement:
    """PICO 框架元素"""
    population: str = ""
    intervention: str = ""
    comparison: str = ""
    outcome: str = ""
    study_design: str = ""
    setting: str = ""

    def completeness_score(self) -> float:
        """PICO 完整性评分"""
        fields = [self.population, self.intervention, self.outcome]
        filled = sum(1 for f in fields if f.strip())
        return filled / 3.0


@dataclass
class EffectSize:
    """效应量"""
    value: float
    lower_ci: float
    upper_ci: float
    measure: str  # "RR", "OR", "SMD", "MD", "HR"
    p_value: Optional[float] = None
    n_cases: Optional[int] = None
    n_controls: Optional[int] = None

    @property
    def se(self) -> float:
        """标准误"""
        return (self.upper_ci - self.lower_ci) / (2 * 1.96)

    @property
    def weight(self) -> float:
        """逆方差权重"""
        return 1.0 / (self.se ** 2) if self.se > 0 else 0

    @property
    def significant(self) -> bool:
        """统计显著性（95% CI 不跨越 0 或 1）"""
        if self.measure in ["RR", "OR", "HR"]:
            return (self.lower_ci > 1) or (self.upper_ci < 1)
        return (self.lower_ci > 0) or (self.upper_ci < 0)


@dataclass
class EvidencePoint:
    """单个证据点"""
    paper_id: int
    paper_title: str
    year: Optional[int]
    pico: PICOElement
    effect_size: Optional[EffectSize]
    direction: str  # "positive", "negative", "neutral", "mixed"
    evidence_strength: str  # "strong", "moderate", "weak", "insufficient"
    sample_size: Optional[int]
    bias_risk: Optional[str]  # "low", "moderate", "high", "unclear"
    findings: str = ""
    limitations: str = ""


@dataclass
class Contradiction:
    """矛盾证据对"""
    outcome: str
    paper_a: EvidencePoint
    paper_b: EvidencePoint
    contradiction_type: str  # "opposite_direction", "significance_conflict", "magnitude_discrepancy"
    severity: float  # 0-1
    resolution_suggestion: str = ""


class EvidenceSynthesisEngine:
    """证据合成引擎"""

    def __init__(self):
        self.evidence_points: List[EvidencePoint] = []
        self.contradictions: List[Contradiction] = []
        self.outcome_groups: Dict[str, List[EvidencePoint]] = defaultdict(list)

    # ──────────────────────────────────────────────────────────────
    # 1. PICO 提取
    # ──────────────────────────────────────────────────────────────

    def extract_pico(self, text: str, title: str = "", use_llm: bool = False) -> PICOElement:
        """从论文摘要或全文中提取 PICO 元素。

        采用混合策略: 正则规则优先，可选 LLM 增强。
        """
        pico = PICOElement()

        combined_text = f"{title}. {text}"
        text_lower = combined_text.lower()

        # ── Population / Study Subjects ──
        pop_patterns = [
            r'(?:participants|patients|subjects|individuals|workers|students|children|adults)\s+(?:were|are|included|enrolled|recruited|comprised|consisted)\s+(?:of|was|were)?\s+([^,.]+)',
            r'(\d+\s*(?:patients|participants|subjects|individuals|cases|controls))',
            r'study\s+(?:population|sample|cohort)\s+(?:comprised|consisted|included)\s+([^,.]+)',
        ]
        for pattern in pop_patterns:
            m = re.search(pattern, text_lower)
            if m:
                pico.population = m.group(1).strip()
                break

        # 中文关键词
        if not pico.population:
            zh_patterns = [
                r'([^，。]*?例[^，。]*患者)',
                r'([^，。]*名[^，。]*受试者)',
                r'纳入([^，。]+患者)',
            ]
            for pattern in zh_patterns:
                m = re.search(pattern, combined_text)
                if m:
                    pico.population = m.group(1).strip()
                    break

        # ── Intervention / Exposure ──
        int_patterns = [
            r'(?:treated|received|administered|intervention|exposed)\s+(?:with|to)\s+([^,.]+)',
            r'intervention\s+group\s+(?:received|was|were)\s+([^,.]+)',
            r'(?:using|using\s+a|with)\s+([^,.]+?)\s+(?:model|method|approach|framework|algorithm)',
        ]
        for pattern in int_patterns:
            m = re.search(pattern, text_lower)
            if m:
                pico.intervention = m.group(1).strip()
                break

        # 中文干预
        if not pico.intervention:
            zh_int = [
                r'采用([^，。]+方法)',
                r'使用([^，。]+模型)',
                r'干预组?采用([^，。]+)',
            ]
            for pattern in zh_int:
                m = re.search(pattern, combined_text)
                if m:
                    pico.intervention = m.group(1).strip()
                    break

        # ── Comparison / Control ──
        comp_patterns = [
            r'(?:compared|comparison)\s+(?:with|to)\s+([^,.]+)',
            r'control\s+group\s+(?:received|was|were)\s+([^,.]+)',
            r'versus\s+([^,.]+)',
        ]
        for pattern in comp_patterns:
            m = re.search(pattern, text_lower)
            if m:
                pico.comparison = m.group(1).strip()
                break

        # ── Outcome ──
        out_patterns = [
            r'(?:primary|main|key)\s+(?:outcome|endpoint|measure)\s+(?:was|were|is|are)?\s*([^,.]+)',
            r'outcomes?\s+(?:included|were|measured|assessed)\s+([^,.]+)',
            r'(?:achieved|obtained|reached|showed)\s+([^,.]+?)\s+(?:accuracy|F1|score|performance|improvement)',
        ]
        for pattern in out_patterns:
            m = re.search(pattern, text_lower)
            if m:
                pico.outcome = m.group(1).strip()
                break

        # 中文结果
        if not pico.outcome:
            zh_out = [
                r'结果[^，。]*达到([^，。]+)',
                r'准确率为?([^，。]+%)',
                r'主要[^，。]*指标?为([^，。]+)',
            ]
            for pattern in zh_out:
                m = re.search(pattern, combined_text)
                if m:
                    pico.outcome = m.group(1).strip()
                    break

        # ── Study Design ──
        design_keywords = {
            "randomized controlled trial": ["randomized controlled trial", "rct", "randomised"],
            "cohort study": ["cohort", "longitudinal"],
            "case-control": ["case-control", "case control"],
            "cross-sectional": ["cross-sectional", "cross sectional"],
            "systematic review": ["systematic review", "meta-analysis"],
            "experimental": ["experiment", "experimental"],
            "observational": ["observational"],
            "qualitative": ["qualitative", "interview", "focus group"],
        }

        for design, keywords in design_keywords.items():
            if any(k in text_lower for k in keywords):
                pico.study_design = design
                break

        return pico

    # ──────────────────────────────────────────────────────────────
    # 2. 效应量提取
    # ──────────────────────────────────────────────────────────────

    def extract_effect_size(self, text: str) -> Optional[EffectSize]:
        """从文本中提取效应量和置信区间。"""
        text_lower = text.lower()

        # 查找常见的效应量报告模式
        patterns = [
            # OR = 2.5 (95% CI: 1.2-5.2)
            (r'(OR|odds ratio|RR|risk ratio|HR|hazard ratio)\s*[=:]\s*(\d+\.?\d*)\s*\(\s*95%\s*C[IL]\s*[:=]?\s*(\d+\.?\d*)\s*[-–—~]\s*(\d+\.?\d*)\s*\)',
             1, 2, 3, 4),
            # MD = 0.5 (95% CI -0.2 to 1.2)
            (r'(MD|mean difference|SMD|standardized mean difference)\s*[=:]\s*([-–]?\d+\.?\d*)\s*\(\s*95%\s*C[IL]\s*[:=]?\s*([-–]?\d+\.?\d*)\s*(?:to|[-–—~])\s*([-–]?\d+\.?\d*)\s*\)',
             1, 2, 3, 4),
            # p = 0.001
            (r'p\s*[<≤=]\s*(\d?\.?\d+)', None, None, None, None),
        ]

        extracted = None

        for pattern, measure_g, val_g, lower_g, upper_g in patterns:
            m = re.search(pattern, text_lower)
            if m and measure_g:
                try:
                    measure = m.group(measure_g).upper()
                    if measure in ["ODDS RATIO"]:
                        measure = "OR"
                    elif measure in ["RISK RATIO"]:
                        measure = "RR"
                    elif measure in ["HAZARD RATIO"]:
                        measure = "HR"
                    elif measure in ["MEAN DIFFERENCE"]:
                        measure = "MD"
                    elif measure in ["STANDARDIZED MEAN DIFFERENCE"]:
                        measure = "SMD"

                    val = float(m.group(val_g))
                    lower = float(m.group(lower_g).replace("–", "-").replace("−", "-"))
                    upper = float(m.group(upper_g).replace("–", "-").replace("−", "-"))

                    extracted = EffectSize(
                        value=val,
                        lower_ci=lower,
                        upper_ci=upper,
                        measure=measure,
                    )
                    break
                except (ValueError, IndexError):
                    continue

        # 提取 p 值
        p_match = re.search(r'p\s*([<≤=])\s*(\d?\.?\d+)', text_lower)
        if p_match:
            try:
                p_val = float(p_match.group(2))
                if p_match.group(1) == "<":
                    p_val = p_val / 2  # 保守估计
                if extracted:
                    extracted.p_value = p_val
            except ValueError:
                pass

        return extracted

    # ──────────────────────────────────────────────────────────────
    # 3. 研究方向判断
    # ──────────────────────────────────────────────────────────────

    def determine_study_direction(self, findings: str, effect_size: Optional[EffectSize] = None) -> str:
        """判断研究结果的方向。"""
        if effect_size is not None:
            if effect_size.significant:
                if effect_size.measure in ["RR", "OR", "HR"]:
                    return "positive" if effect_size.value > 1 else "negative"
                else:
                    return "positive" if effect_size.value > 0 else "negative"

        # 基于文本关键词
        findings_lower = findings.lower()

        positive = [
            "significant improvement", "statistically significant", "outperformed",
            "better performance", "superior", "increased", "higher", "greater",
            "显著提高", "显著改善", "优于", "显著高于", "具有统计学意义"
        ]
        negative = [
            "no significant", "not significant", "no difference", "did not improve",
            "worse", "lower", "decreased", "failed to", "no effect",
            "无显著差异", "没有统计学意义", "未显著改善", "低于"
        ]
        mixed = ["mixed results", "inconsistent", "varied", "部分显著", "结果不一致"]

        pos_count = sum(1 for k in positive if k in findings_lower)
        neg_count = sum(1 for k in negative if k in findings_lower)
        mix_count = sum(1 for k in mixed if k in findings_lower)

        if mix_count > 0:
            return "mixed"
        if pos_count > neg_count:
            return "positive"
        if neg_count > pos_count:
            return "negative"
        return "neutral"

    # ──────────────────────────────────────────────────────────────
    # 4. 证据强度评估
    # ──────────────────────────────────────────────────────────────

    def assess_evidence_strength(self,
                                study_design: str,
                                sample_size: Optional[int],
                                effect_size: Optional[EffectSize],
                                limitations: str = "") -> str:
        """基于 GRADE 框架评估证据强度。"""
        score = 0

        # 研究设计（GRADE: RCT 起始 4，观察性起始 2）
        design_scores = {
            "systematic review": 5,
            "randomized controlled trial": 4,
            "cohort study": 2,
            "case-control": 2,
            "cross-sectional": 1,
            "experimental": 3,
            "observational": 2,
            "qualitative": 1,
        }
        score += design_scores.get(study_design.lower(), 1)

        # 样本量加分
        if sample_size:
            if sample_size >= 1000:
                score += 1
            elif sample_size >= 100:
                score += 0.5

        # 效应量加分
        if effect_size:
            if effect_size.significant:
                score += 1
            # 大效应量
            if effect_size.measure in ["RR", "OR", "HR"] and effect_size.value >= 2:
                score += 1
            elif effect_size.measure not in ["RR", "OR", "HR"] and abs(effect_size.value) >= 0.5:
                score += 0.5

        # 局限性减分
        if limitations:
            lim_lower = limitations.lower()
            if "small sample" in lim_lower or "small sample" in lim_lower:
                score -= 1
            if "bias" in lim_lower or "confounding" in lim_lower:
                score -= 0.5
            if "single site" in lim_lower or "single dataset" in lim_lower:
                score -= 0.5

        if score >= 4:
            return "strong"
        elif score >= 2.5:
            return "moderate"
        elif score >= 1.5:
            return "weak"
        return "insufficient"

    # ──────────────────────────────────────────────────────────────
    # 5. 从证据矩阵构建证据点
    # ──────────────────────────────────────────────────────────────

    def build_from_evidence_matrix(self, evidence_matrix: List[Dict], papers: List[Dict]) -> List[EvidencePoint]:
        """从现有证据矩阵构建结构化证据点。"""
        self.evidence_points = []

        for row in evidence_matrix:
            ref_id = row.get("ref_id", 0)
            if not 1 <= ref_id <= len(papers):
                continue

            paper = papers[ref_id - 1]
            title = paper.get("title", "")
            year = paper.get("year")
            abstract = paper.get("abstract", "")
            findings = row.get("main_evidence_or_findings", "")
            limitations = row.get("limitations", "")

            # 提取 PICO
            pico = self.extract_pico(abstract, title)

            # 提取效应量
            effect_size = self.extract_effect_size(abstract + " " + findings)

            # 判断研究方向
            direction = self.determine_study_direction(findings, effect_size)

            # 样本量（尝试提取）
            sample_size = None
            n_match = re.search(r'n\s*[=:]\s*(\d+)', abstract + " " + findings, re.IGNORECASE)
            if n_match:
                try:
                    sample_size = int(n_match.group(1))
                except ValueError:
                    pass

            # 评估证据强度
            strength = self.assess_evidence_strength(
                pico.study_design, sample_size, effect_size, str(limitations)
            )

            ep = EvidencePoint(
                paper_id=ref_id,
                paper_title=title,
                year=year if isinstance(year, int) else None,
                pico=pico,
                effect_size=effect_size,
                direction=direction,
                evidence_strength=strength,
                sample_size=sample_size,
                bias_risk=None,  # 后续可实现
                findings=str(findings),
                limitations=str(limitations),
            )

            self.evidence_points.append(ep)

        # 按结局分组
        self._group_by_outcome()

        # 检测矛盾
        self._detect_contradictions()

        return self.evidence_points

    # ──────────────────────────────────────────────────────────────
    # 6. 按结局分组
    # ──────────────────────────────────────────────────────────────

    def _group_by_outcome(self):
        """按结局指标对证据点进行分组。"""
        self.outcome_groups.clear()

        # 简单的关键词聚类
        outcome_keywords = defaultdict(list)

        for ep in self.evidence_points:
            if ep.pico.outcome:
                words = re.findall(r'\w+', ep.pico.outcome.lower())
                key_words = tuple(sorted([w for w in words if len(w) > 3][:3]))
                if key_words:
                    outcome_keywords[key_words].append(ep)
                else:
                    outcome_keywords[("other",)].append(ep)
            else:
                # 使用发现中的关键词
                findings_words = re.findall(r'\w+', ep.findings.lower())
                key_words = tuple(sorted([w for w in findings_words if len(w) > 3][:3]))
                outcome_keywords[key_words or ("other",)].append(ep)

        # 命名群组
        for i, (key, eps) in enumerate(outcome_keywords.items()):
            if key == ("other",):
                name = "其他结局"
            else:
                # 使用该组中最常见的 outcome 文本
                all_outcomes = [ep.pico.outcome for ep in eps if ep.pico.outcome]
                if all_outcomes:
                    name = max(set(all_outcomes), key=all_outcomes.count)[:50]
                else:
                    name = f"结局组 {i + 1}"
            self.outcome_groups[name] = eps

    # ──────────────────────────────────────────────────────────────
    # 7. 矛盾检测
    # ──────────────────────────────────────────────────────────────

    def _detect_contradictions(self):
        """检测同一结局下的矛盾证据。"""
        self.contradictions = []

        for outcome, eps in self.outcome_groups.items():
            if len(eps) < 2:
                continue

            # 两两比较
            for i in range(len(eps)):
                for j in range(i + 1, len(eps)):
                    a, b = eps[i], eps[j]

                    # 类型 1: 方向相反
                    directions = {a.direction, b.direction}
                    if "positive" in directions and "negative" in directions:
                        severity = 0.8 if self._both_significant(a, b) else 0.5
                        self.contradictions.append(Contradiction(
                            outcome=outcome,
                            paper_a=a,
                            paper_b=b,
                            contradiction_type="opposite_direction",
                            severity=severity,
                            resolution_suggestion=self._suggest_resolution(a, b)
                        ))

                    # 类型 2: 显著性冲突（一个显著，一个不显著）
                    elif (a.effect_size and b.effect_size) and (a.effect_size.significant != b.effect_size.significant):
                        self.contradictions.append(Contradiction(
                            outcome=outcome,
                            paper_a=a,
                            paper_b=b,
                            contradiction_type="significance_conflict",
                            severity=0.4,
                            resolution_suggestion="可能由于样本量差异或测量方法不同导致，建议进行亚组分析"
                        ))

                    # 类型 3: 效应量差异过大
                    elif (a.effect_size and b.effect_size) and self._magnitude_discrepancy(a.effect_size, b.effect_size):
                        self.contradictions.append(Contradiction(
                            outcome=outcome,
                            paper_a=a,
                            paper_b=b,
                            contradiction_type="magnitude_discrepancy",
                            severity=0.3,
                            resolution_suggestion="效应量估计存在较大差异，建议检查测量工具是否标准化"
                        ))

    def _both_significant(self, a: EvidencePoint, b: EvidencePoint) -> bool:
        return (a.effect_size and a.effect_size.significant and
                b.effect_size and b.effect_size.significant)

    def _magnitude_discrepancy(self, a: EffectSize, b: EffectSize, threshold: float = 2.0) -> bool:
        """检测效应量差异是否过大（相差超过阈值倍）。"""
        if a.measure != b.measure:
            return False
        if a.value == 0 or b.value == 0:
            return abs(a.value - b.value) > threshold
        ratio = max(abs(a.value), abs(b.value)) / min(abs(a.value), abs(b.value))
        return ratio > threshold

    def _suggest_resolution(self, a: EvidencePoint, b: EvidencePoint) -> str:
        """为矛盾提供解决建议。"""
        suggestions = []

        # 检查是否因人群不同
        if a.pico.population and b.pico.population:
            overlap = self._text_overlap(a.pico.population, b.pico.population)
            if overlap < 0.3:
                suggestions.append("研究人群不同可能是结果差异的原因")

        # 检查是否因干预不同
        if a.pico.intervention and b.pico.intervention:
            overlap = self._text_overlap(a.pico.intervention, b.pico.intervention)
            if overlap < 0.3:
                suggestions.append("干预措施不同可能导致结果差异")

        # 检查样本量
        if a.sample_size and b.sample_size:
            if max(a.sample_size, b.sample_size) / min(a.sample_size, b.sample_size) > 5:
                suggestions.append("样本量差异较大可能影响统计效力")

        if not suggestions:
            suggestions.append("建议进行亚组分析或 meta 回归探索异质性来源")

        return "; ".join(suggestions)

    def _text_overlap(self, a: str, b: str) -> float:
        words_a = set(re.findall(r'\w+', a.lower()))
        words_b = set(re.findall(r'\w+', b.lower()))
        if not words_a or not words_b:
            return 0.0
        return len(words_a & words_b) / len(words_a | words_b)

    # ──────────────────────────────────────────────────────────────
    # 8. 简单 Meta 分析（固定效应模型）
    # ──────────────────────────────────────────────────────────────

    def meta_analyze_outcome(self, outcome: str) -> Optional[Dict]:
        """对特定结局进行简单的固定效应 Meta 分析。"""
        eps = self.outcome_groups.get(outcome, [])
        if not eps:
            return None

        # 筛选有有效效应量的研究
        valid = [ep for ep in eps if ep.effect_size is not None]
        if not valid:
            return None

        # 检查所有研究使用相同的效应量指标
        measures = {ep.effect_size.measure for ep in valid if ep.effect_size}
        if len(measures) > 1:
            return {
                "outcome": outcome,
                "error": "效应量指标不一致，无法合并",
                "measures_found": list(measures),
                "studies_count": len(valid),
            }

        # 逆方差加权合并
        total_weight = sum(ep.effect_size.weight for ep in valid if ep.effect_size)
        if total_weight == 0:
            return None

        pooled_es = sum(ep.effect_size.value * ep.effect_size.weight
                       for ep in valid if ep.effect_size) / total_weight
        pooled_se = (1.0 / total_weight) ** 0.5
        pooled_lower = pooled_es - 1.96 * pooled_se
        pooled_upper = pooled_es + 1.96 * pooled_se

        # 异质性检验（I² 近似计算）
        effect_values = [ep.effect_size.value for ep in valid if ep.effect_size]
        if len(effect_values) > 1:
            if HAS_NUMPY:
                variance = float(np.var(effect_values))
                i_squared = max(0, 100 * variance / (variance + pooled_se ** 2))
            else:
                mean_val = sum(effect_values) / len(effect_values)
                variance = sum((x - mean_val) ** 2 for x in effect_values) / len(effect_values)
                i_squared = max(0, 100 * variance / (variance + pooled_se ** 2))
        else:
            i_squared = 0

        # 异质性评级
        if i_squared < 25:
            heterogeneity = "low"
        elif i_squared < 50:
            heterogeneity = "moderate"
        else:
            heterogeneity = "high"

        return {
            "outcome": outcome,
            "measure": list(measures)[0],
            "studies_included": len(valid),
            "pooled_effect_size": round(pooled_es, 3),
            "ci_95_lower": round(pooled_lower, 3),
            "ci_95_upper": round(pooled_upper, 3),
            "significant": (pooled_lower > 1 and pooled_upper > 1) or (pooled_lower < 1 and pooled_upper < 1)
                           if list(measures)[0] in ["RR", "OR", "HR"]
                           else (pooled_lower > 0 and pooled_upper > 0) or (pooled_lower < 0 and pooled_upper < 0),
            "i_squared": round(i_squared, 1),
            "heterogeneity": heterogeneity,
            "individual_studies": [
                {
                    "paper_id": ep.paper_id,
                    "paper_title": ep.paper_title[:60] + "...",
                    "effect_size": ep.effect_size.value,
                    "ci_lower": ep.effect_size.lower_ci,
                    "ci_upper": ep.effect_size.upper_ci,
                    "weight": round(ep.effect_size.weight / total_weight * 100, 1),
                    "direction": ep.direction,
                }
                for ep in valid
            ]
        }

    # ──────────────────────────────────────────────────────────────
    # 9. 生成合成摘要
    # ──────────────────────────────────────────────────────────────

    def generate_synthesis_summary(self) -> Dict:
        """生成完整的证据合成摘要。"""
        outcome_summaries = []

        for outcome, eps in self.outcome_groups.items():
            if not eps:
                continue

            # 方向统计
            direction_counts = defaultdict(int)
            for ep in eps:
                direction_counts[ep.direction] += 1

            # 证据强度统计
            strength_counts = defaultdict(int)
            for ep in eps:
                strength_counts[ep.evidence_strength] += 1

            # 一致性判断
            majority_dir = max(direction_counts.keys(), key=lambda d: direction_counts[d])
            consistency = direction_counts[majority_dir] / len(eps)

            if consistency >= 0.8:
                consensus = "一致"
            elif consistency >= 0.6:
                consensus = "基本一致"
            else:
                consensus = "不一致"

            # 尝试 meta 分析
            meta_result = self.meta_analyze_outcome(outcome)

            outcome_summaries.append({
                "outcome": outcome,
                "studies_count": len(eps),
                "direction_breakdown": dict(direction_counts),
                "strength_breakdown": dict(strength_counts),
                "consensus": consensus,
                "consistency_score": round(consistency, 2),
                "meta_analysis": meta_result,
                "papers": [{"id": ep.paper_id, "title": ep.paper_title[:50]} for ep in eps],
            })

        return {
            "total_evidence_points": len(self.evidence_points),
            "outcome_groups_count": len(self.outcome_groups),
            "contradictions_count": len(self.contradictions),
            "pico_completeness_avg": round(
                sum(ep.pico.completeness_score() for ep in self.evidence_points) / max(len(self.evidence_points), 1),
                2
            ),
            "outcome_summaries": outcome_summaries,
            "contradictions": [
                {
                    "outcome": c.outcome,
                    "type": c.contradiction_type,
                    "severity": c.severity,
                    "paper_a": c.paper_a.paper_title[:50],
                    "paper_b": c.paper_b.paper_title[:50],
                    "suggestion": c.resolution_suggestion,
                }
                for c in self.contradictions
            ],
        }

    def generate_synthesis_report(self, output_format: str = "markdown") -> str:
        """生成人类可读的证据合成报告。"""
        summary = self.generate_synthesis_summary()

        if output_format == "markdown":
            lines = [
                "# 📊 证据合成报告",
                "",
                "## 概览",
                "",
                f"- **纳入证据点**: {summary['total_evidence_points']}",
                f"- **结局分组数**: {summary['outcome_groups_count']}",
                f"- **检测到矛盾**: {summary['contradictions_count']}",
                f"- **PICO 完整性**: {summary['pico_completeness_avg']:.0%}",
                "",
                "## 结局分析",
                "",
            ]

            for outcome in summary["outcome_summaries"]:
                lines.append(f"### {outcome['outcome']}")
                lines.append("")
                lines.append(f"- **研究数量**: {outcome['studies_count']}")
                lines.append(f"- **一致性**: {outcome['consensus']} ({outcome['consistency_score']:.0%})")
                lines.append("- **结果方向分布**:")
                for direction, count in outcome["direction_breakdown"].items():
                    lines.append(f"  - {direction}: {count} 项研究")
                lines.append("- **证据强度分布**:")
                for strength, count in outcome["strength_breakdown"].items():
                    lines.append(f"  - {strength}: {count} 项研究")

                if outcome["meta_analysis"]:
                    ma = outcome["meta_analysis"]
                    if "error" not in ma:
                        lines.append("")
                        lines.append("#### Meta 分析结果")
                        lines.append("")
                        lines.append(f"- **合并效应量**: {ma['measure']} = {ma['pooled_effect_size']} "
                                    f"(95% CI: {ma['ci_95_lower']} - {ma['ci_95_upper']})")
                        lines.append(f"- **统计显著性**: {'✅ 显著' if ma['significant'] else '❌ 不显著'}")
                        lines.append(f"- **异质性**: I² = {ma['i_squared']}% ({ma['heterogeneity']})")

                lines.append("")
                lines.append("#### 纳入文献")
                for paper in outcome["papers"]:
                    lines.append(f"- [{paper['id']}] {paper['title']}")
                lines.append("")

            if self.contradictions:
                lines.append("## ⚠️ 矛盾证据")
                lines.append("")
                for c in summary["contradictions"]:
                    lines.append(f"### {c['outcome']}")
                    lines.append("")
                    lines.append(f"- **类型**: {c['type']}")
                    lines.append(f"- **严重程度**: {'🔴 高' if c['severity'] >= 0.7 else '🟡 中' if c['severity'] >= 0.4 else '🟢 低'}")
                    lines.append(f"- **涉及文献**:")
                    lines.append(f"  1. {c['paper_a']}")
                    lines.append(f"  2. {c['paper_b']}")
                    lines.append(f"- **解决建议**: {c['suggestion']}")
                    lines.append("")

            return "\n".join(lines)

        return json.dumps(summary, ensure_ascii=False, indent=2)
