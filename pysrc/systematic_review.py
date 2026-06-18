"""
Systematic Review Framework - 系统综述框架

核心能力:
1. PRISMA 2020 合规的流程跟踪
2. PRISMA 流程图生成 (ASCII / SVG)
3. 研究注册信息管理 (PROSPERO 模板)
4. 纳入/排除标准管理 (PICO 驱动)
5. 筛选过程记录与审计追踪
6. 偏倚风险评估 (RoB 2, QUADAS-2 模板)
7. 证据等级评定 (GRADE)
8. 系统综述报告生成
"""

from __future__ import annotations
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Set
from collections import defaultdict
from datetime import datetime


@dataclass
class InclusionExclusionCriteria:
    """纳入/排除标准"""
    # Population
    population_include: List[str] = None
    population_exclude: List[str] = None

    # Intervention / Exposure
    intervention_include: List[str] = None
    intervention_exclude: List[str] = None

    # Comparison / Control
    comparison_include: List[str] = None
    comparison_exclude: List[str] = None

    # Outcome
    outcome_include: List[str] = None
    outcome_exclude: List[str] = None

    # Study design
    study_design_include: List[str] = None
    study_design_exclude: List[str] = None

    # Publication
    publication_years: Tuple[Optional[int], Optional[int]] = None
    languages: List[str] = None
    publication_types: List[str] = None  # peer-reviewed, preprint, etc.

    # Additional custom criteria
    custom_include: Dict[str, str] = None
    custom_exclude: Dict[str, str] = None


@dataclass
class StudyRecord:
    """单个研究记录"""
    study_id: str = ""
    title: str = ""
    authors: str = ""
    year: Optional[int] = None
    journal: str = ""
    doi: str = ""
    abstract: str = ""
    full_text_available: bool = False

    # 筛选状态
    screening_stage: str = "identified"  # identified, duplicates_removed, screened, eligible, included
    excluded: bool = False
    exclusion_reason: str = ""
    excluded_at_stage: str = ""

    # 偏倚风险评估
    bias_risk: Optional[Dict[str, str]] = None

    # 来源数据库
    source_database: str = ""
    source_search_date: str = ""

    # 元数据
    pico_extracted: Dict[str, str] = None
    evidence_strength: str = ""  # GRADE: high, moderate, low, very_low
    notes: str = ""


@dataclass
class SearchSource:
    """检索来源"""
    database_name: str = ""
    search_date: str = ""
    search_strategy: str = ""
    records_found: int = 0
    records_imported: int = 0


@dataclass
class RoB2Domain:
    """RoB 2 偏倚风险领域"""
    name: str
    risk_level: str  # low, some_concerns, high
    rationale: str = ""


@dataclass
class RoB2Assessment:
    """RoB 2 偏倚风险评估"""
    study_id: str = ""
    domains: List[RoB2Domain] = None
    overall_risk: str = ""  # low, some_concerns, high
    assessor: str = ""
    date_assessed: str = ""


@dataclass
class GRADEEvidenceProfile:
    """GRADE 证据概况"""
    outcome: str = ""
    study_count: int = 0
    participants_count: int = 0
    quality_rating: str = ""  # high, moderate, low, very_low
    risk_of_bias: str = ""
    inconsistency: str = ""
    indirectness: str = ""
    imprecision: str = ""
    publication_bias: str = ""
    effect_estimate: str = ""
    certainty_rationale: str = ""


class PrismaFlow:
    """PRISMA 流程图跟踪器"""

    def __init__(self):
        # 识别阶段
        self.records_from_databases: int = 0
        self.records_from_registers: int = 0
        self.records_from_other_sources: int = 0

        # 去重后
        self.records_after_duplicates_removed: int = 0

        # 筛选阶段
        self.records_screened: int = 0
        self.records_excluded_at_screening: int = 0
        self.reports_sought_for_retrieval: int = 0
        self.reports_not_retrieved: int = 0

        # 合格性评估
        self.reports_assessed_for_eligibility: int = 0
        self.reports_excluded_at_eligibility: int = 0

        # 纳入
        self.studies_included_in_review: int = 0
        self.references_included_in_review: int = 0

        # 详细排除原因统计
        self.exclusion_reasons_screening: Dict[str, int] = defaultdict(int)
        self.exclusion_reasons_eligibility: Dict[str, int] = defaultdict(int)

    @property
    def total_identified(self) -> int:
        return self.records_from_databases + self.records_from_registers + self.records_from_other_sources

    @property
    def excluded_at_screening_not_retrieved(self) -> int:
        return self.records_excluded_at_screening + self.reports_not_retrieved

    def validate(self) -> Tuple[bool, List[str]]:
        """验证数据一致性。"""
        errors = []

        if self.records_after_duplicates_removed > self.total_identified:
            errors.append("去重后的记录数不能超过总识别数")

        if self.records_screened != self.records_after_duplicates_removed:
            errors.append("筛选的记录数应等于去重后的记录数")

        if self.reports_sought_for_retrieval != self.records_screened - self.records_excluded_at_screening:
            errors.append("寻求获取的报告数应等于筛选后剩余的报告数")

        if self.reports_assessed_for_eligibility != self.reports_sought_for_retrieval - self.reports_not_retrieved:
            errors.append("评估合格性的报告数应等于获取到的报告数")

        expected_included = self.reports_assessed_for_eligibility - self.reports_excluded_at_eligibility
        if self.studies_included_in_review != expected_included:
            errors.append(f"纳入的研究数应为 {expected_included}，当前为 {self.studies_included_in_review}")

        return len(errors) == 0, errors

    def to_ascii_diagram(self) -> str:
        """生成 ASCII 风格的 PRISMA 流程图。"""
        lines = [
            "=" * 70,
            "                          PRISMA 2020 流程图",
            "=" * 70,
            "",
            "  识别阶段 (Identification)",
            "  " + "-" * 40,
            f"  数据库检索: {self.records_from_databases:>5} 条记录",
            f"  注册中心检索: {self.records_from_registers:>5} 条记录",
            f"  其他来源: {self.records_from_other_sources:>8} 条记录",
            f"  ───────────────────────────────────",
            f"  总计识别: {self.total_identified:>10} 条记录",
            "",
            "  └─ 去重 ──>",
            "",
            f"  去重后: {self.records_after_duplicates_removed:>12} 条记录",
            "",
            "  筛选阶段 (Screening)",
            "  " + "-" * 40,
            f"  筛选: {self.records_screened:>16} 条记录",
            f"  排除: {self.records_excluded_at_screening:>16} 条记录",
            "",
            "  └─ 寻求获取 ──>",
            "",
            f"  寻求获取: {self.reports_sought_for_retrieval:>12} 条报告",
            f"  无法获取: {self.reports_not_retrieved:>14} 条报告",
            "",
            "  合格性评估 (Eligibility)",
            "  " + "-" * 40,
            f"  评估合格性: {self.reports_assessed_for_eligibility:>10} 条报告",
            f"  排除: {self.reports_excluded_at_eligibility:>18} 条报告",
            "",
            "  纳入阶段 (Included)",
            "  " + "-" * 40,
            f"  纳入研究: {self.studies_included_in_review:>14} 项研究",
            f"  纳入参考文献: {self.references_included_in_review:>11} 篇参考文献",
            "",
            "=" * 70,
        ]

        # 添加排除原因详情
        if self.exclusion_reasons_screening:
            lines.extend(["", "  筛选阶段排除原因:", "  " + "-" * 40])
            for reason, count in sorted(self.exclusion_reasons_screening.items(), key=lambda x: -x[1]):
                lines.append(f"  - {reason}: {count}")

        if self.exclusion_reasons_eligibility:
            lines.extend(["", "  合格性阶段排除原因:", "  " + "-" * 40])
            for reason, count in sorted(self.exclusion_reasons_eligibility.items(), key=lambda x: -x[1]):
                lines.append(f"  - {reason}: {count}")

        return "\n".join(lines)

    def to_svg(self, output_path: Optional[str | Path] = None) -> str:
        """生成 SVG 格式的 PRISMA 流程图。"""
        box_height = 70
        box_width = 240
        gap_v = 40
        gap_h = 60
        start_y = 40

        svg = f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="{box_width * 2 + gap_h + 80}" height="650" viewBox="0 0 {box_width * 2 + gap_h + 80} 650">
<style>
  .box {{ fill: #ffffff; stroke: #333333; stroke-width: 1.5; }}
  .label {{ font-family: Arial, sans-serif; font-size: 13px; fill: #333333; text-anchor: middle; }}
  .number {{ font-family: Arial, sans-serif; font-size: 16px; font-weight: bold; fill: #1a73e8; }}
  .arrow {{ stroke: #666666; stroke-width: 1.5; fill: none; marker-end: url(#arrowhead); }}
  .heading {{ font-family: Arial, sans-serif; font-size: 14px; font-weight: bold; fill: #333333; }}
</style>
<defs>
  <marker id="arrowhead" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">
    <polygon points="0 0, 10 3.5, 0 7" fill="#666666"/>
  </marker>
</defs>
'''

        # Identification phase
        x1 = 40
        x2 = x1 + box_width + gap_h
        y = start_y

        # Heading
        svg += f'\n<text x="{x1 + box_width/2}" y="{y-10}" class="heading">Identification</text>'
        y += 20

        # Database
        svg += self._svg_box(x1, y, box_width, box_height,
            f"Database search\n(n = {self.records_from_databases})")

        # Registers / other
        svg += self._svg_box(x2, y, box_width, box_height,
            f"Registers/other sources\n(n = {self.records_from_registers + self.records_from_other_sources})")
        y += box_height + gap_v

        # After duplicates (centered)
        center_x = x1 + (x2 - x1) / 2 - box_width / 2 + gap_h / 2
        svg += f'\n<line x1="{x1 + box_width/2}" y1="{start_y + box_height}" x2="{center_x + box_width/2}" y2="{y}" class="arrow"/>'
        svg += f'\n<line x1="{x2 + box_width/2}" y1="{start_y + box_height}" x2="{center_x + box_width/2}" y2="{y}" class="arrow"/>'

        svg += self._svg_box(center_x, y, box_width, box_height,
            f"Records after duplicates\n(n = {self.records_after_duplicates_removed})")
        y += box_height + gap_v

        # Screening phase
        svg += f'\n<text x="{center_x + box_width/2}" y="{y-10}" class="heading">Screening</text>'
        y += 20

        # Screening + exclusion
        svg += self._svg_box(x1, y, box_width, box_height,
            f"Title/abstract screening\n(n = {self.records_screened})")
        svg += self._svg_box(x2, y, box_width, box_height,
            f"Excluded at screening\n(n = {self.records_excluded_at_screening})")
        y += box_height + gap_v

        # Arrows: right to exclusion, down to retrieve
        svg += f'\n<line x1="{x1 + box_width}" y1="{y - gap_v - box_height/2}" x2="{x2}" y2="{y - gap_v - box_height/2}" class="arrow"/>'
        svg += f'\n<line x1="{x1 + box_width/2}" y1="{y - gap_v}" x2="{x1 + box_width/2}" y2="{y}" class="arrow"/>'

        # Full-text sought + not retrievable
        svg += self._svg_box(x1, y, box_width, box_height,
            f"Full-text sought\n(n = {self.reports_sought_for_retrieval})")
        svg += self._svg_box(x2, y, box_width, box_height,
            f"Full-text not retrievable\n(n = {self.reports_not_retrieved})")
        y += box_height + gap_v

        # Arrows
        svg += f'\n<line x1="{x1 + box_width}" y1="{y - gap_v - box_height/2}" x2="{x2}" y2="{y - gap_v - box_height/2}" class="arrow"/>'
        svg += f'\n<line x1="{x1 + box_width/2}" y1="{y - gap_v}" x2="{x1 + box_width/2}" y2="{y}" class="arrow"/>'

        # Eligibility phase
        svg += f'\n<text x="{center_x + box_width/2}" y="{y-10}" class="heading">Eligibility</text>'
        y += 20

        svg += self._svg_box(x1, y, box_width, box_height,
            f"Eligibility assessed\n(n = {self.reports_assessed_for_eligibility})")
        svg += self._svg_box(x2, y, box_width, box_height,
            f"Excluded at eligibility\n(n = {self.reports_excluded_at_eligibility})")
        y += box_height + gap_v

        # Arrows
        svg += f'\n<line x1="{x1 + box_width}" y1="{y - gap_v - box_height/2}" x2="{x2}" y2="{y - gap_v - box_height/2}" class="arrow"/>'
        svg += f'\n<line x1="{x1 + box_width/2}" y1="{y - gap_v}" x2="{x1 + box_width/2}" y2="{y}" class="arrow"/>'

        # Included phase
        svg += f'\n<text x="{center_x + box_width/2}" y="{y-10}" class="heading">Included</text>'
        y += 20

        svg += self._svg_box(center_x, y, box_width, box_height,
            f"Studies included\nn = {self.studies_included_in_review}")

        svg += "\n</svg>"

        if output_path:
            Path(output_path).write_text(svg, encoding="utf-8")

        return svg

    def _svg_box(self, x: int, y: int, w: int, h: int, text: str) -> str:
        """生成 SVG 盒子。"""
        lines = text.split("\n")
        text_svg = ""
        for i, line in enumerate(lines):
            dy = (i - len(lines) / 2 + 0.5) * 18
            text_svg += f'\n<text x="{x + w/2}" y="{y + h/2 + dy}" class="label">{line}</text>'

        return f'\n<rect x="{x}" y="{y}" width="{w}" height="{h}" class="box"/>{text_svg}'


class SystematicReview:
    """系统综述项目管理器。"""

    def __init__(self, title: str = ""):
        self.title = title
        self.prospero_id: str = ""
        self.date_registered: str = ""

        # PRISMA 流程
        self.prisma = PrismaFlow()

        # 检索来源
        self.search_sources: List[SearchSource] = []

        # 纳入/排除标准
        self.criteria = InclusionExclusionCriteria()

        # 研究记录
        self.studies: List[StudyRecord] = []

        # 偏倚风险评估
        self.roa_assessments: Dict[str, RoB2Assessment] = {}

        # GRADE 证据概况
        self.grade_profiles: List[GRADEEvidenceProfile] = []

        # 元数据
        self.created_at: str = datetime.now().isoformat()
        self.last_updated: str = datetime.now().isoformat()

    # ──────────────────────────────────────────────────────────────
    # 研究管理
    # ──────────────────────────────────────────────────────────────

    def add_study(self, study: StudyRecord):
        """添加研究记录。"""
        self.studies.append(study)
        self.last_updated = datetime.now().isoformat()

    def add_studies_from_openalex(self, papers: List[Dict], source_database: str = "OpenAlex"):
        """从 OpenAlex 搜索结果批量添加研究。"""
        search_date = datetime.now().strftime("%Y-%m-%d")

        for p in papers:
            study = StudyRecord(
                study_id=p.get("id", ""),
                title=p.get("title", ""),
                authors=", ".join(p.get("authors", [])) if isinstance(p.get("authors"), list) else p.get("authors", ""),
                year=p.get("year"),
                journal=p.get("venue", ""),
                doi=p.get("doi", ""),
                abstract=p.get("abstract", ""),
                source_database=source_database,
                source_search_date=search_date,
                screening_stage="identified",
            )
            self.studies.append(study)

        self.prisma.records_from_databases += len(papers)
        self.prisma.records_after_duplicates_removed += len(papers)
        self.prisma.records_screened += len(papers)
        self.last_updated = datetime.now().isoformat()

    def remove_duplicates(self, by: str = "doi") -> int:
        """移除重复研究。"""
        seen = set()
        unique = []

        for study in self.studies:
            key = None
            if by == "doi" and study.doi:
                key = study.doi.lower().strip()
            elif by == "title":
                key = re.sub(r'[^a-z0-9]', '', study.title.lower().strip())

            if key and key in seen:
                continue
            if key:
                seen.add(key)
            unique.append(study)

        removed = len(self.studies) - len(unique)
        self.studies = unique
        self.prisma.records_after_duplicates_removed = len(unique)
        self.last_updated = datetime.now().isoformat()

        return removed

    def exclude_study(self, study_id: str, reason: str, stage: str = "screening"):
        """排除研究。"""
        for study in self.studies:
            if study.study_id == study_id:
                study.excluded = True
                study.exclusion_reason = reason
                study.excluded_at_stage = stage

                if stage == "screening":
                    self.prisma.records_excluded_at_screening += 1
                    self.prisma.exclusion_reasons_screening[reason] += 1
                elif stage == "eligibility":
                    self.prisma.reports_excluded_at_eligibility += 1
                    self.prisma.exclusion_reasons_eligibility[reason] += 1

                self.last_updated = datetime.now().isoformat()
                break

    def move_to_eligibility(self):
        """将筛选通过的研究移入合格性评估阶段。"""
        for study in self.studies:
            if study.screening_stage == "screened" and not study.excluded:
                study.screening_stage = "eligibility"

        self.prisma.reports_sought_for_retrieval = sum(
            1 for s in self.studies
            if s.screening_stage in ["eligibility", "eligible", "included"]
            and not s.excluded
        )
        self.prisma.reports_assessed_for_eligibility = self.prisma.reports_sought_for_retrieval
        self.last_updated = datetime.now().isoformat()

    def finalize_included(self):
        """最终确定纳入的研究。"""
        included = [s for s in self.studies if not s.excluded]
        for s in included:
            s.screening_stage = "included"

        self.prisma.studies_included_in_review = len(included)
        self.prisma.references_included_in_review = len(included)
        self.last_updated = datetime.now().isoformat()

    # ──────────────────────────────────────────────────────────────
    # 偏倚风险评估 (RoB 2)
    # ──────────────────────────────────────────────────────────────

    ROB2_DOMAINS = [
        "随机过程产生的偏倚",
        "偏离既定干预措施的偏倚",
        "结局数据缺失的偏倚",
        "结局测量的偏倚",
        "报告结果选择的偏倚",
    ]

    def create_rob2_assessment(self, study_id: str) -> RoB2Assessment:
        """创建 RoB 2 评估模板。"""
        assessment = RoB2Assessment(
            study_id=study_id,
            domains=[RoB2Domain(name=d, risk_level="unassigned") for d in self.ROB2_DOMAINS],
            date_assessed=datetime.now().strftime("%Y-%m-%d"),
        )
        self.roa_assessments[study_id] = assessment
        return assessment

    def set_rob2_domain(self, study_id: str, domain_name: str, risk_level: str, rationale: str = ""):
        """设置 RoB 2 单个领域的评估结果。"""
        if study_id not in self.roa_assessments:
            self.create_rob2_assessment(study_id)

        assessment = self.roa_assessments[study_id]
        for domain in assessment.domains:
            if domain_name in domain.name or domain.name in domain_name:
                domain.risk_level = risk_level
                domain.rationale = rationale
                break

        # 更新总体偏倚风险
        risks = [d.risk_level for d in assessment.domains]
        if "high" in risks:
            assessment.overall_risk = "high"
        elif "some_concerns" in risks:
            assessment.overall_risk = "some_concerns"
        elif all(r == "low" for r in risks if r != "unassigned"):
            assessment.overall_risk = "low"

        self.last_updated = datetime.now().isoformat()

    # ──────────────────────────────────────────────────────────────
    # GRADE 证据等级
    # ──────────────────────────────────────────────────────────────

    def add_grade_profile(self, profile: GRADEEvidenceProfile):
        """添加 GRADE 证据概况。"""
        self.grade_profiles.append(profile)
        self.last_updated = datetime.now().isoformat()

    # ──────────────────────────────────────────────────────────────
    # 报告生成
    # ──────────────────────────────────────────────────────────────

    def generate_prisma_report(self) -> str:
        """生成 PRISMA 合规报告。"""
        lines = [
            "# PRISMA 2020 系统综述报告",
            "",
            f"## 综述标题: {self.title}",
            "",
        ]

        if self.prospero_id:
            lines.append(f"- **PROSPERO 注册编号**: {self.prospero_id}")
        if self.date_registered:
            lines.append(f"- **注册日期**: {self.date_registered}")

        lines.extend([
            "",
            "## 检索来源",
            "",
        ])

        for source in self.search_sources:
            lines.append(f"### {source.database_name}")
            lines.append(f"- 检索日期: {source.search_date}")
            lines.append(f"- 检索到记录: {source.records_found} 条")
            lines.append(f"- 导入记录: {source.records_imported} 条")
            if source.search_strategy:
                lines.append(f"- 检索策略: {source.search_strategy[:200]}...")
            lines.append("")

        lines.extend([
            "## 纳入/排除标准",
            "",
        ])

        # PICO
        if self.criteria.population_include:
            lines.append("### 纳入人群:")
            for p in self.criteria.population_include:
                lines.append(f"- {p}")
            lines.append("")

        if self.criteria.intervention_include:
            lines.append("### 纳入干预:")
            for i in self.criteria.intervention_include:
                lines.append(f"- {i}")
            lines.append("")

        if self.criteria.outcome_include:
            lines.append("### 纳入结局:")
            for o in self.criteria.outcome_include:
                lines.append(f"- {o}")
            lines.append("")

        if self.criteria.study_design_include:
            lines.append("### 纳入研究设计:")
            for d in self.criteria.study_design_include:
                lines.append(f"- {d}")
            lines.append("")

        lines.extend([
            "## PRISMA 流程图",
            "",
            "```",
            self.prisma.to_ascii_diagram(),
            "```",
            "",
        ])

        # 纳入研究列表
        included = [s for s in self.studies if not s.excluded]
        lines.extend([
            "## 纳入研究",
            "",
            f"共纳入 {len(included)} 项研究:",
            "",
        ])

        for s in included[:30]:  # 最多显示 30 项
            year = s.year or "N/A"
            lines.append(f"- {s.authors[:30]} ({year}). {s.title[:80]}...")

        if len(included) > 30:
            lines.append(f"- ... 还有 {len(included) - 30} 项研究")

        # 偏倚风险摘要
        if self.roa_assessments:
            lines.extend(["", "## 偏倚风险评估摘要", ""])
            risk_counts = defaultdict(int)
            for assessment in self.roa_assessments.values():
                risk_counts[assessment.overall_risk] += 1

            for risk, count in risk_counts.items():
                lines.append(f"- **{risk}**: {count} 项研究")

        # GRADE 证据概况
        if self.grade_profiles:
            lines.extend(["", "## GRADE 证据概况", ""])
            for profile in self.grade_profiles:
                lines.append(f"### {profile.outcome}")
                lines.append(f"- **研究数**: {profile.study_count}")
                lines.append(f"- **证据等级**: {profile.quality_rating}")
                lines.append("")

        return "\n".join(lines)

    # ──────────────────────────────────────────────────────────────
    # 持久化
    # ──────────────────────────────────────────────────────────────

    def save(self, output_path: str | Path):
        """保存系统综述项目到 JSON 文件。"""
        data = {
            "title": self.title,
            "prospero_id": self.prospero_id,
            "date_registered": self.date_registered,
            "prisma": {
                "records_from_databases": self.prisma.records_from_databases,
                "records_from_registers": self.prisma.records_from_registers,
                "records_from_other_sources": self.prisma.records_from_other_sources,
                "records_after_duplicates_removed": self.prisma.records_after_duplicates_removed,
                "records_screened": self.prisma.records_screened,
                "records_excluded_at_screening": self.prisma.records_excluded_at_screening,
                "reports_sought_for_retrieval": self.prisma.reports_sought_for_retrieval,
                "reports_not_retrieved": self.prisma.reports_not_retrieved,
                "reports_assessed_for_eligibility": self.prisma.reports_assessed_for_eligibility,
                "reports_excluded_at_eligibility": self.prisma.reports_excluded_at_eligibility,
                "studies_included_in_review": self.prisma.studies_included_in_review,
                "references_included_in_review": self.prisma.references_included_in_review,
            },
            "studies": [
                {
                    "study_id": s.study_id,
                    "title": s.title,
                    "authors": s.authors,
                    "year": s.year,
                    "journal": s.journal,
                    "doi": s.doi,
                    "abstract": s.abstract,
                    "screening_stage": s.screening_stage,
                    "excluded": s.excluded,
                    "exclusion_reason": s.exclusion_reason,
                    "excluded_at_stage": s.excluded_at_stage,
                    "source_database": s.source_database,
                    "evidence_strength": s.evidence_strength,
                }
                for s in self.studies
            ],
            "created_at": self.created_at,
            "last_updated": self.last_updated,
        }

        Path(output_path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, input_path: str | Path) -> "SystematicReview":
        """从 JSON 文件加载系统综述项目。"""
        data = json.loads(Path(input_path).read_text(encoding="utf-8"))

        review = cls(title=data.get("title", ""))
        review.prospero_id = data.get("prospero_id", "")
        review.date_registered = data.get("date_registered", "")
        review.created_at = data.get("created_at", "")
        review.last_updated = data.get("last_updated", "")

        # 加载 PRISMA 数据
        prisma_data = data.get("prisma", {})
        review.prisma.records_from_databases = prisma_data.get("records_from_databases", 0)
        review.prisma.records_from_registers = prisma_data.get("records_from_registers", 0)
        review.prisma.records_from_other_sources = prisma_data.get("records_from_other_sources", 0)
        review.prisma.records_after_duplicates_removed = prisma_data.get("records_after_duplicates_removed", 0)
        review.prisma.records_screened = prisma_data.get("records_screened", 0)
        review.prisma.records_excluded_at_screening = prisma_data.get("records_excluded_at_screening", 0)
        review.prisma.reports_sought_for_retrieval = prisma_data.get("reports_sought_for_retrieval", 0)
        review.prisma.reports_not_retrieved = prisma_data.get("reports_not_retrieved", 0)
        review.prisma.reports_assessed_for_eligibility = prisma_data.get("reports_assessed_for_eligibility", 0)
        review.prisma.reports_excluded_at_eligibility = prisma_data.get("reports_excluded_at_eligibility", 0)
        review.prisma.studies_included_in_review = prisma_data.get("studies_included_in_review", 0)
        review.prisma.references_included_in_review = prisma_data.get("references_included_in_review", 0)

        # 加载研究
        for s_data in data.get("studies", []):
            study = StudyRecord()
            study.study_id = s_data.get("study_id", "")
            study.title = s_data.get("title", "")
            study.authors = s_data.get("authors", "")
            study.year = s_data.get("year")
            study.journal = s_data.get("journal", "")
            study.doi = s_data.get("doi", "")
            study.abstract = s_data.get("abstract", "")
            study.screening_stage = s_data.get("screening_stage", "identified")
            study.excluded = s_data.get("excluded", False)
            study.exclusion_reason = s_data.get("exclusion_reason", "")
            study.excluded_at_stage = s_data.get("excluded_at_stage", "")
            study.source_database = s_data.get("source_database", "")
            study.evidence_strength = s_data.get("evidence_strength", "")
            review.studies.append(study)

        return review
