"""
Anti-Hallucination 2.0 - 科研反幻觉增强系统

核心能力:
1. 句子级事实三元组提取 (S-P-O)
2. 证据溯源定位 (哪个引用支撑哪个声明)
3. 转述质量评估 (语义相似度 + 信息完整性)
4. 数值声明验证
5. 方法学一致性检查
6. 幻觉置信度评分
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

try:
    from openai import OpenAI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False


@dataclass
class FactTriple:
    """事实三元组：Subject - Predicate - Object"""
    subject: str
    predicate: str
    object: str
    confidence: float = 1.0
    source_sentence: str = ""
    sentence_index: int = -1


@dataclass
class EvidenceTrace:
    """证据溯源记录"""
    claim: str
    cited_papers: List[int]
    evidence_sources: List[Dict[str, Any]]
    semantic_similarity: float
    lexical_overlap: float
    verdict: str  # supported / partial / contradicted / no_evidence
    mismatches: List[str]
    verification_notes: str


@dataclass
class NumericClaim:
    """数值声明"""
    sentence: str
    value: float
    unit: str
    context: str
    paper_ref: Optional[int]
    verified: bool = False
    source_value: Optional[float] = None


class AntiHallucinationEngine:
    """反幻觉引擎 V2"""

    def __init__(self, embedding_model: str = "text-embedding-3-small"):
        self.embedding_model = embedding_model
        self._openai_client = None

    @property
    def openai_client(self):
        if not HAS_OPENAI:
            return None
        if self._openai_client is None:
            try:
                self._openai_client = OpenAI()
            except Exception:
                # No API key or other issue, return None to use fallback
                return None
        return self._openai_client

    # ──────────────────────────────────────────────────────────────
    # 1. 文本分块与句子分解
    # ──────────────────────────────────────────────────────────────

    def split_sentences(self, text: str) -> List[str]:
        """智能分句，处理中英文混合。"""
        # 统一标点
        text = text.replace("。", ". ").replace("！", "! ").replace("？", "? ")
        # 在句末标点后换行
        text = re.sub(r'([.!?])\s+', r'\1\n', text)
        # 按换行分割并过滤空句
        sentences = []
        for line in text.split('\n'):
            line = line.strip()
            if line and len(line) > 10:
                sentences.append(line)
        return sentences

    def extract_citation_markers(self, sentence: str) -> List[int]:
        """从句子中提取引用标记，如 [1], [2,3], [4-6]。"""
        citations = []

        # 匹配 [1], [2,3], [4-6] 等格式
        patterns = [
            r'\[(\d+)\]',  # 单个数字
            r'\[(\d+)\s*,\s*(\d+)\]',  # 逗号分隔
            r'\[(\d+)\s*-\s*(\d+)\]',  # 范围
        ]

        # 先找所有方括号内容
        for bracket in re.findall(r'\[[\d,\s\-\–]+\]', sentence):
            content = bracket[1:-1]

            # 处理范围: 1-3 或 1–3
            range_match = re.match(r'(\d+)\s*[\-\–]\s*(\d+)', content)
            if range_match:
                start, end = int(range_match.group(1)), int(range_match.group(2))
                citations.extend(range(start, end + 1))
                continue

            # 处理逗号分隔: 1, 2, 3
            nums = re.findall(r'(\d+)', content)
            citations.extend(int(n) for n in nums)

        return sorted(set(citations))

    # ──────────────────────────────────────────────────────────────
    # 2. 事实三元组提取 (基于规则 + LLM 混合)
    # ──────────────────────────────────────────────────────────────

    def extract_fact_triples(self, sentence: str, use_llm: bool = False) -> List[FactTriple]:
        """从句子中提取事实三元组。

        策略: 先规则提取，可选 LLM 增强提取。
        """
        triples = []

        # 规则 1: 性能声明模式 - "X achieves Y on Z"
        pattern1 = r'(\w+(?:\s+\w+){0,3})\s+(achieves|outperforms|reaches|achieved|outperformed|reached|obtains|obtained|attains|attained)\s+([^.]+?)(?:\s+on\s+([^.]+?))?\.'
        for m in re.finditer(pattern1, sentence, re.IGNORECASE):
            triples.append(FactTriple(
                subject=m.group(1).strip(),
                predicate=m.group(2).strip(),
                object=m.group(3).strip() + (f" on {m.group(4).strip()}" if m.group(4) else ""),
                source_sentence=sentence
            ))

        # 规则 2: 比较声明 - "X outperforms Y by Z"
        pattern2 = r'(\w+(?:\s+\w+){0,3})\s+(outperforms|surpasses|is better than|exceeds|outperformed|surpassed|exceeded)\s+([^,.]+?)(?:\s+by\s+([^,.]+))?'
        for m in re.finditer(pattern2, sentence, re.IGNORECASE):
            triples.append(FactTriple(
                subject=m.group(1).strip(),
                predicate=m.group(2).strip(),
                object=m.group(3).strip() + (f" by {m.group(4).strip()}" if m.group(4) else ""),
                source_sentence=sentence
            ))

        # 规则 3: 状态/属性声明 - "X has Y" / "X is Y"
        pattern3 = r'(\w+(?:\s+\w+){0,2})\s+(has|achieves|attains|reaches|obtains|provides|offers|is|are)\s+((?:a |an )?[^.]+?)\.'
        for m in re.finditer(pattern3, sentence, re.IGNORECASE):
            triples.append(FactTriple(
                subject=m.group(1).strip(),
                predicate=m.group(2).strip(),
                object=m.group(3).strip(),
                source_sentence=sentence
            ))

        # LLM 增强提取（如果启用且可用）
        if use_llm and self.openai_client:
            llm_triples = self._extract_triples_llm(sentence)
            triples.extend(llm_triples)

        return triples

    def _extract_triples_llm(self, sentence: str) -> List[FactTriple]:
        """使用 LLM 提取更复杂的事实三元组。"""
        try:
            prompt = f"""从以下学术句子中提取事实三元组 (subject, predicate, object)。
只返回 JSON 格式，不要额外解释。

句子: {sentence}

返回格式:
{{"triples": [
    {{"subject": "...", "predicate": "...", "object": "..."}}
]}}"""

            resp = self.openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                response_format={"type": "json_object"}
            )
            data = json.loads(resp.choices[0].message.content or "{}")
            return [
                FactTriple(**t, source_sentence=sentence)
                for t in data.get("triples", [])
            ]
        except Exception:
            return []

    # ──────────────────────────────────────────────────────────────
    # 3. 数值声明验证
    # ──────────────────────────────────────────────────────────────

    def extract_numeric_claims(self, sentence: str) -> List[NumericClaim]:
        """提取句子中的数值声明。"""
        claims = []

        # 性能指标数值
        patterns = [
            # "92.5% accuracy" / "accuracy of 92.5%"
            (r'(\d+(?:\.\d+)?)\s*%?\s*(accuracy|F1|f1|precision|recall|BLEU|ROUGE|MCC|AUROC|AUC|mAP)', 1, 2),
            (r'(accuracy|F1|f1|precision|recall|BLEU|ROUGE|MCC|AUROC|AUC|mAP)\s*(?:of|is)?\s*(\d+(?:\.\d+)?)\s*%?', 2, 1),
            # "x% improvement"
            (r'(\d+(?:\.\d+)?)\s*%?\s*(improvement|gain|increase|reduction|decrease)', 1, 2),
            # 参数数量
            (r'(\d+(?:\.\d+)?)\s*(B|M|K)?\s*(parameters|params)', 1, 3),
        ]

        for pattern, val_group, unit_group in patterns:
            for m in re.finditer(pattern, sentence, re.IGNORECASE):
                try:
                    value = float(m.group(val_group))
                    unit = m.group(unit_group).strip().lower()

                    # 处理 B/M/K 后缀
                    if unit in ["b", "m", "k"] and "parameter" in sentence.lower():
                        multiplier = {"b": 1e9, "m": 1e6, "k": 1e3}[unit]
                        value *= multiplier
                        unit = "parameters"

                    claims.append(NumericClaim(
                        sentence=sentence,
                        value=value,
                        unit=unit,
                        context=sentence,
                        paper_ref=None
                    ))
                except (ValueError, IndexError):
                    continue

        return claims

    def verify_numeric_claim(self, claim: NumericClaim, paper_text: str) -> Tuple[bool, Optional[float]]:
        """验证数值声明是否在原文中存在。"""
        claim_val = claim.value

        # 在原文中查找相同单位的数值
        pattern = rf'(\d+(?:\.\d+)?)\s*%?\s*{re.escape(claim.unit)}'
        matches = re.finditer(pattern, paper_text, re.IGNORECASE)

        source_values = []
        for m in matches:
            try:
                source_values.append(float(m.group(1)))
            except ValueError:
                continue

        if not source_values:
            return False, None

        # 检查是否有接近的值（允许 5% 的舍入误差）
        for sv in source_values:
            if abs(claim_val - sv) / max(abs(sv), 1) < 0.05:
                return True, sv

        return False, source_values[0] if source_values else None

    # ──────────────────────────────────────────────────────────────
    # 4. 语义相似度计算
    # ──────────────────────────────────────────────────────────────

    def _cosine_similarity(self, a: List[float], b: List[float]) -> float:
        if not HAS_NUMPY:
            dot = sum(x * y for x, y in zip(a, b))
            na = sum(x * x for x in a) ** 0.5
            nb = sum(x * x for x in b) ** 0.5
            return dot / (na * nb + 1e-10)
        return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-10))

    def compute_semantic_similarity(self, text1: str, text2: str) -> float:
        """计算两段文本的语义相似度。"""
        if not self.openai_client:
            # 降级到词袋重叠
            return self._lexical_overlap(text1, text2)

        try:
            resp = self.openai_client.embeddings.create(
                model=self.embedding_model,
                input=[text1[:8000], text2[:8000]]
            )
            emb1 = resp.data[0].embedding
            emb2 = resp.data[1].embedding
            return self._cosine_similarity(emb1, emb2)
        except Exception:
            return self._lexical_overlap(text1, text2)

    def _lexical_overlap(self, text1: str, text2: str) -> float:
        """词袋重叠相似度（降级方案）。"""
        stop = {"the", "a", "an", "of", "in", "on", "for", "with", "to", "is", "are", "was", "were", "be", "been", "and", "or", "by"}
        words1 = {w.lower() for w in re.findall(r'\w+', text1) if w.lower() not in stop and len(w) > 2}
        words2 = {w.lower() for w in re.findall(r'\w+', text2) if w.lower() not in stop and len(w) > 2}
        if not words1 or not words2:
            return 0.0
        return len(words1 & words2) / len(words1 | words2)

    # ──────────────────────────────────────────────────────────────
    # 5. 证据溯源核心逻辑
    # ──────────────────────────────────────────────────────────────

    def trace_evidence(self,
                       sentence: str,
                       cited_refs: List[int],
                       evidence_matrix: List[Dict],
                       papers: List[Dict]) -> EvidenceTrace:
        """追踪一个声明的证据来源。

        Args:
            sentence: 声明句子
            cited_refs: 句子中的引用编号
            evidence_matrix: 证据矩阵（包含每篇论文的摘要、方法、发现）
            papers: 论文元数据列表

        Returns:
            EvidenceTrace 对象，包含验证结果
        """
        # 收集所有引用论文的证据文本
        evidence_texts = []
        cited_paper_data = []

        for ref_idx in cited_refs:
            if 1 <= ref_idx <= len(papers):
                paper = papers[ref_idx - 1]
                # 从证据矩阵获取更多信息
                matrix_row = None
                for row in evidence_matrix:
                    if row.get("ref_id") == ref_idx:
                        matrix_row = row
                        break

                # 构建该论文的完整证据文本
                evidence_parts = [
                    paper.get("abstract", "") or "",
                    paper.get("title", "") or "",
                ]
                if matrix_row:
                    evidence_parts.extend([
                        str(matrix_row.get("main_evidence_or_findings", "")),
                        str(matrix_row.get("method_category", "")),
                        str(matrix_row.get("limitations", "")),
                    ])

                evidence_text = " ".join(str(p) for p in evidence_parts if p)
                evidence_texts.append(evidence_text)
                cited_paper_data.append({"ref_id": ref_idx, **paper})

        if not evidence_texts:
            # 没有引用标记的声明
            return EvidenceTrace(
                claim=sentence,
                cited_papers=[],
                evidence_sources=[],
                semantic_similarity=0.0,
                lexical_overlap=0.0,
                verdict="no_citation",
                mismatches=["此声明没有任何引用标记"],
                verification_notes="建议为该声明添加引用来源"
            )

        # 计算与每篇引用论文的相似度
        similarities = []
        for i, ev_text in enumerate(evidence_texts):
            sim = self.compute_semantic_similarity(sentence, ev_text)
            similarities.append({
                "ref_id": cited_refs[i],
                "similarity": sim,
                "paper_title": cited_paper_data[i].get("title", "")[:80]
            })

        max_sim = max(s["similarity"] for s in similarities) if similarities else 0
        lex_overlap = max(self._lexical_overlap(sentence, et) for et in evidence_texts) if evidence_texts else 0

        # 判断证据状态
        mismatches = []
        if max_sim < 0.3:
            verdict = "no_evidence"
            mismatches.append("声明内容与引用论文的语义相似度极低")
        elif max_sim < 0.5:
            verdict = "weak"
            mismatches.append("声明内容与引用论文的语义相似度较低")
        elif max_sim < 0.7:
            verdict = "partial"
        else:
            verdict = "supported"

        # 额外检查：数值验证
        numeric_claims = self.extract_numeric_claims(sentence)
        if numeric_claims:
            for claim in numeric_claims:
                # 检查每篇引用论文
                for ev_text in evidence_texts:
                    verified, source_val = self.verify_numeric_claim(claim, ev_text)
                    if not verified:
                        mismatches.append(f"数值 '{claim.value} {claim.unit}' 在引用论文中未找到匹配")
                        if source_val is not None:
                            mismatches.append(f"  - 原文中找到的值: {source_val} {claim.unit}")
                        if verdict == "supported":
                            verdict = "partial"

        notes = []
        if verdict == "supported":
            notes.append("声明得到引用论文的有力支持")
        elif verdict == "partial":
            notes.append("声明得到部分支持，建议检查具体数值或细节描述")
        elif verdict == "weak":
            notes.append("证据支持较弱，建议检查引用是否准确或补充更强的证据")
        else:
            notes.append("在引用论文中未找到支持该声明的证据，建议核实或补充引用")

        return EvidenceTrace(
            claim=sentence,
            cited_papers=cited_refs,
            evidence_sources=similarities,
            semantic_similarity=float(max_sim),
            lexical_overlap=float(lex_overlap),
            verdict=verdict,
            mismatches=mismatches,
            verification_notes="; ".join(notes)
        )

    # ──────────────────────────────────────────────────────────────
    # 6. 方法学一致性检查
    # ──────────────────────────────────────────────────────────────

    def verify_methodology_consistency(self,
                                       claim: str,
                                       claimed_method: str,
                                       claimed_result: str,
                                       evidence_matrix: List[Dict],
                                       cited_refs: List[int]) -> Dict:
        """检查方法学声明的一致性。

        验证声称的方法是否与论文实际方法一致，以及声称的结果是否合理。
        """
        issues = []
        warnings = []

        # 收集引用论文的方法学类别
        methods_used = set()
        results_claimed = []

        for ref_idx in cited_refs:
            for row in evidence_matrix:
                if row.get("ref_id") == ref_idx:
                    method = row.get("method_category", "")
                    if method:
                        methods_used.add(str(method))
                    findings = row.get("main_evidence_or_findings", "")
                    if findings:
                        results_claimed.append(str(findings))

        # 检查方法匹配
        if methods_used:
            method_overlap = self._lexical_overlap(claimed_method, " ".join(methods_used))
            if method_overlap < 0.1:
                issues.append(f"声称的方法 '{claimed_method}' 与引用论文实际使用的方法 "
                             f"({', '.join(methods_used)}) 不匹配")
        else:
            warnings.append("引用论文中未提取到方法学信息")

        # 检查结果匹配
        if results_claimed:
            result_overlap = self._lexical_overlap(claimed_result, " ".join(results_claimed))
            if result_overlap < 0.1:
                issues.append(f"声称的结果 '{claimed_result[:50]}...' 在引用论文中没有对应表述")

        return {
            "consistent": len(issues) == 0,
            "issues": issues,
            "warnings": warnings,
            "methods_in_citations": list(methods_used),
            "method_overlap_score": self._lexical_overlap(claimed_method, " ".join(methods_used)) if methods_used else 0,
        }

    # ──────────────────────────────────────────────────────────────
    # 7. 完整综述验证流程
    # ──────────────────────────────────────────────────────────────

    def verify_review_full(self,
                           review_text: str,
                           evidence_matrix: List[Dict],
                           papers: List[Dict],
                           enable_llm: bool = False) -> Dict:
        """完整的综述反幻觉验证流程。

        Args:
            review_text: 生成的综述全文
            evidence_matrix: 证据矩阵
            papers: 论文元数据列表
            enable_llm: 是否启用 LLM 增强提取

        Returns:
            完整验证报告
        """
        sentences = self.split_sentences(review_text)

        # 逐句验证
        sentence_reports = []
        all_triples = []
        hallucination_risk = 0.0

        for idx, sent in enumerate(sentences):
            cited = self.extract_citation_markers(sent)

            # 提取事实三元组
            triples = self.extract_fact_triples(sent, use_llm=enable_llm)
            for t in triples:
                t.sentence_index = idx
            all_triples.extend(triples)

            # 证据溯源
            if cited:
                trace = self.trace_evidence(sent, cited, evidence_matrix, papers)
                sentence_reports.append({
                    "sentence_index": idx,
                    "sentence": sent,
                    "citations": cited,
                    "triples": [{"s": t.subject, "p": t.predicate, "o": t.object} for t in triples],
                    "verdict": trace.verdict,
                    "semantic_similarity": trace.semantic_similarity,
                    "lexical_overlap": trace.lexical_overlap,
                    "mismatches": trace.mismatches,
                    "notes": trace.verification_notes
                })

                # 更新幻觉风险
                if trace.verdict in ["no_evidence", "no_citation"]:
                    hallucination_risk += 0.1
                elif trace.verdict == "weak":
                    hallucination_risk += 0.03
                elif trace.verdict == "partial":
                    hallucination_risk += 0.01
            else:
                # 没有引用的句子
                sentence_reports.append({
                    "sentence_index": idx,
                    "sentence": sent,
                    "citations": [],
                    "triples": [{"s": t.subject, "p": t.predicate, "o": t.object} for t in triples],
                    "verdict": "no_citation",
                    "semantic_similarity": 0,
                    "lexical_overlap": 0,
                    "mismatches": ["无引用标记"],
                    "notes": "此句子没有引用来源，如为事实声明建议添加引用"
                })
                hallucination_risk += 0.02

        # 统计汇总
        verdict_counts = defaultdict(int)
        for r in sentence_reports:
            verdict_counts[r["verdict"]] += 1

        total = len(sentence_reports)
        hallucination_score = hallucination_risk / max(total, 1)

        # 总体评分
        if hallucination_score < 0.05:
            overall_verdict = "trustworthy"
            overall_level = "✅ 低"
        elif hallucination_score < 0.15:
            overall_verdict = "mostly_trustworthy"
            overall_level = "⚠️ 中"
        else:
            overall_verdict = "needs_review"
            overall_level = "❌ 高"

        # 找出有问题的句子
        problematic = [r for r in sentence_reports
                      if r["verdict"] in ["no_evidence", "no_citation", "weak"]]

        return {
            "overall_verdict": overall_verdict,
            "hallucination_risk_level": overall_level,
            "hallucination_score": float(hallucination_score),
            "total_sentences_verified": total,
            "verdict_breakdown": dict(verdict_counts),
            "sentence_level_reports": sentence_reports,
            "fact_triples_extracted": len(all_triples),
            "problematic_sentences": problematic,
            "recommendations": self._generate_recommendations(hallucination_score, verdict_counts, problematic),
            "coverage_metrics": {
                "cited_sentences_ratio": verdict_counts.get("supported", 0) + verdict_counts.get("partial", 0) / max(total, 1),
                "uncited_sentences_ratio": verdict_counts.get("no_citation", 0) / max(total, 1),
            }
        }

    def _generate_recommendations(self, risk_score: float, counts: Dict, problematic: List[Dict]) -> List[str]:
        """生成改进建议。"""
        recs = []

        if counts.get("no_citation", 0) > 3:
            recs.append(f"有 {counts['no_citation']} 个句子没有引用标记，建议为关键声明补充引用来源")

        if counts.get("no_evidence", 0) > 0:
            recs.append(f"有 {counts['no_evidence']} 个声明在引用论文中找不到证据支持，建议核实引用或修改表述")

        if counts.get("weak", 0) > 0:
            recs.append(f"有 {counts['weak']} 个声明证据支持较弱，建议改用更相关的文献或调整表述以匹配原文")

        if risk_score > 0.1:
            recs.append("整体幻觉风险偏高，建议人工审核所有标注为有问题的句子")

        # 检查特定问题
        numeric_issues = [p for p in problematic if "数值" in str(p.get("mismatches", ""))]
        if numeric_issues:
            recs.append(f"发现 {len(numeric_issues)} 个数值声明与原文不符，建议仔细核对具体数字")

        if not recs:
            recs.append("综述引用质量良好，继续保持")

        return recs

    # ──────────────────────────────────────────────────────────────
    # 8. 生成验证报告
    # ──────────────────────────────────────────────────────────────

    def generate_verification_report(self, result: Dict, format: str = "markdown") -> str:
        """生成人类可读的验证报告。"""
        if format == "markdown":
            lines = [
                "# 🔍 反幻觉验证报告",
                "",
                f"**总体评级**: {result['hallucination_risk_level']}",
                f"**幻觉分数**: {result['hallucination_score']:.2%}",
                f"**验证句子数**: {result['total_sentences_verified']}",
                f"**提取事实三元组**: {result['fact_triples_extracted']}",
                "",
                "## 验证统计",
                "",
            ]

            for verdict, count in result["verdict_breakdown"].items():
                emoji = {"supported": "✅", "partial": "⚠️", "weak": "❓", "no_evidence": "❌", "no_citation": "ℹ️"}.get(verdict, "•")
                lines.append(f"- {emoji} **{verdict}**: {count}")

            lines.extend([
                "",
                "## 改进建议",
                "",
            ])

            for rec in result["recommendations"]:
                lines.append(f"- {rec}")

            if result["problematic_sentences"]:
                lines.extend([
                    "",
                    "## 有问题的句子",
                    "",
                ])
                for p in result["problematic_sentences"][:20]:  # 最多显示 20 个
                    lines.append(f"### 句子 {p['sentence_index'] + 1}")
                    lines.append(f"> {p['sentence']}")
                    lines.append(f"")
                    lines.append(f"- **判定**: {p['verdict']}")
                    lines.append(f"- **语义相似度**: {p['semantic_similarity']:.2%}")
                    if p["mismatches"]:
                        lines.append(f"- **问题**:")
                        for m in p["mismatches"]:
                            lines.append(f"  - {m}")
                    lines.append("")

            return "\n".join(lines)

        return json.dumps(result, ensure_ascii=False, indent=2)
