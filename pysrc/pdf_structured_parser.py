"""
PDF Structured Parser - PDF 结构化解析器

核心能力:
1. GROBID API 客户端（结构化提取全文、参考文献、作者等）
2. 本地解析 fallback（PyPDF2 + 正则提取）
3. 参考文献列表自动提取与解析
4. 表格数据提取
5. 方法学章节专项提取
6. 引用标记与原文跳转映射
"""

from __future__ import annotations
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, IO
from collections import defaultdict

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

try:
    from pypdf import PdfReader
    HAS_PYPDF = True
except ImportError:
    HAS_PYPDF = False


@dataclass
class BibEntry:
    """参考文献条目"""
    id: str = ""
    title: str = ""
    authors: List[str] = None
    journal: str = ""
    volume: str = ""
    issue: str = ""
    pages: str = ""
    year: str = ""
    doi: str = ""
    raw_text: str = ""
    parser_confidence: float = 0.0


@dataclass
class TableData:
    """表格数据"""
    table_id: str = ""
    caption: str = ""
    headers: List[str] = None
    rows: List[List[str]] = None
    footnote: str = ""
    page_number: int = 0


@dataclass
class MethodSection:
    """方法学章节"""
    study_design: str = ""
    participants: str = ""
    intervention: str = ""
    outcomes: str = ""
    analysis: str = ""
    datasets: List[str] = None
    metrics: List[str] = None
    hyperparameters: Dict[str, Any] = None


@dataclass
class StructuredPaper:
    """结构化论文"""
    title: str = ""
    authors: List[str] = None
    abstract: str = ""
    keywords: List[str] = None
    doi: str = ""
    journal: str = ""
    year: str = ""
    sections: Dict[str, str] = None  # section_name -> content
    references: List[BibEntry] = None
    tables: List[TableData] = None
    methods: MethodSection = None
    citation_markers: Dict[int, List[int]] = None  # section_index -> ref_indices
    raw_text: str = ""
    parser_used: str = ""  # "grobid" | "pypdf" | "fallback"


class GrobidClient:
    """GROBID API 客户端"""

    NS = {'tei': 'http://www.tei-c.org/ns/1.0'}

    def __init__(self, base_url: str = "http://localhost:8070"):
        self.base_url = base_url.rstrip("/")
        self.timeout = 120

    def is_available(self) -> bool:
        """检查 GROBID 服务是否可用。"""
        if not HAS_REQUESTS:
            return False
        try:
            resp = requests.get(f"{self.base_url}/api/isalive", timeout=5)
            return resp.status_code == 200
        except Exception:
            return False

    def process_fulltext(self, pdf_path: str | Path, force: bool = False) -> Optional[StructuredPaper]:
        """使用 GROBID 处理 PDF 全文。"""
        if not self.is_available():
            return None

        try:
            with open(pdf_path, "rb") as f:
                files = {"input": (Path(pdf_path).name, f, "application/pdf")}
                data = {
                    "consolidateHeader": "1",
                    "includeRawCitations": "1",
                    "includeRawAffiliations": "1",
                    "teiCoordinates": ["persName", "figure", "ref", "biblStruct", "formula", "s"]
                }

                resp = requests.post(
                    f"{self.base_url}/api/processFulltextDocument",
                    files=files,
                    data=data,
                    timeout=self.timeout,
                )
                resp.raise_for_status()

                return self._parse_tei_xml(resp.text, pdf_path)

        except Exception:
            return None

    def _parse_tei_xml(self, xml_content: str, source_path: str | Path) -> StructuredPaper:
        """解析 GROBID 输出的 TEI XML。"""
        root = ET.fromstring(xml_content)
        paper = StructuredPaper(
            parser_used="grobid",
            sections={},
            references=[],
            tables=[],
            authors=[],
            keywords=[],
        )

        # ── 标题 ──
        title_elem = root.find('.//tei:titleStmt/tei:title', self.NS)
        if title_elem is not None:
            paper.title = (title_elem.text or "").strip()

        # ── 作者 ──
        for author in root.findall('.//tei:sourceDesc/tei:biblStruct/tei:analytic/tei:author', self.NS):
            name_parts = []
            forename = author.find('tei:forename', self.NS)
            surname = author.find('tei:surname', self.NS)
            if forename is not None:
                name_parts.append(forename.text or "")
            if surname is not None:
                name_parts.append(surname.text or "")
            if name_parts:
                paper.authors.append(" ".join(name_parts))

        # ── DOI ──
        doi_elem = root.find('.//tei:idno[@type="DOI"]', self.NS)
        if doi_elem is not None:
            paper.doi = (doi_elem.text or "").strip()

        # ── 摘要 ──
        abstract_elem = root.find('.//tei:profileDesc/tei:abstract', self.NS)
        if abstract_elem is not None:
            paper.abstract = " ".join(abstract_elem.itertext()).strip()

        # ── 关键词 ──
        for kw in root.findall('.//tei:keywords/tei:term', self.NS):
            kw_text = (kw.text or "").strip()
            if kw_text:
                paper.keywords.append(kw_text)

        # ── 期刊信息 ──
        journal_title = root.find('.//tei:monogr/tei:title', self.NS)
        if journal_title is not None:
            paper.journal = (journal_title.text or "").strip()

        bibl_date = root.find('.//tei:monogr/tei:imprint/tei:date[@type="published"]', self.NS)
        if bibl_date is not None:
            paper.year = bibl_date.get("when", "")

        # ── 正文章节 ──
        body = root.find('.//tei:text/tei:body', self.NS)
        if body is not None:
            current_section = "正文"
            section_content = []

            for elem in body:
                if elem.tag == "{http://www.tei-c.org/ns/1.0}div" and elem.get("type") == "section":
                    # 保存上一章节
                    if section_content:
                        paper.sections[current_section] = " ".join(section_content).strip()

                    # 开始新章节
                    head = elem.find('tei:head', self.NS)
                    current_section = (head.text or "未命名章节").strip() if head is not None else "未命名章节"
                    section_content = []

                    # 收集章节内容
                    for p in elem.findall('.//tei:p', self.NS):
                        section_content.append(" ".join(p.itertext()))
                elif elem.tag == "{http://www.tei-c.org/ns/1.0}p":
                    section_content.append(" ".join(elem.itertext()))

            # 保存最后一章
            if section_content:
                paper.sections[current_section] = " ".join(section_content).strip()

        # ── 参考文献 ──
        for bibl in root.findall('.//tei:listBibl/tei:biblStruct', self.NS):
            entry = BibEntry()

            # 标题
            title_elem = bibl.find('tei:analytic/tei:title', self.NS)
            if title_elem is not None:
                entry.title = (title_elem.text or "").strip()

            # 作者
            bib_authors = []
            for author in bibl.findall('tei:analytic/tei:author', self.NS):
                forename = author.find('tei:forename', self.NS)
                surname = author.find('tei:surname', self.NS)
                name_parts = []
                if forename is not None:
                    name_parts.append(forename.text or "")
                if surname is not None:
                    name_parts.append(surname.text or "")
                if name_parts:
                    bib_authors.append(" ".join(name_parts))
            entry.authors = bib_authors

            # 期刊 / 专著标题
            monogr_title = bibl.find('tei:monogr/tei:title', self.NS)
            if monogr_title is not None:
                entry.journal = (monogr_title.text or "").strip()

            # 卷期页
            imprint = bibl.find('tei:monogr/tei:imprint', self.NS)
            if imprint is not None:
                volume = imprint.find('tei:biblScope[@unit="volume"]', self.NS)
                if volume is not None:
                    entry.volume = volume.text or ""
                issue = imprint.find('tei:biblScope[@unit="issue"]', self.NS)
                if issue is not None:
                    entry.issue = issue.text or ""
                pages = imprint.find('tei:biblScope[@unit="page"]', self.NS)
                if pages is not None:
                    entry.pages = pages.text or ""
                date = imprint.find('tei:date[@type="published"]', self.NS)
                if date is not None:
                    entry.year = date.get("when", "")

            # DOI
            doi = bibl.find('.//tei:idno[@type="DOI"]', self.NS)
            if doi is not None:
                entry.doi = (doi.text or "").strip()

            paper.references.append(entry)

        # ── 原始文本（合并） ──
        all_text = [paper.title, paper.abstract]
        all_text.extend(paper.sections.values())
        paper.raw_text = "\n".join(t for t in all_text if t)

        return paper


class LocalPDFParser:
    """本地 PDF 解析器（不依赖 GROBID）"""

    def __init__(self):
        pass

    def parse(self, pdf_path: str | Path) -> Optional[StructuredPaper]:
        """使用 PyPDF 解析 PDF。"""
        if not HAS_PYPDF:
            return None

        try:
            reader = PdfReader(str(pdf_path))
            full_text = []
            for page in reader.pages:
                try:
                    full_text.append(page.extract_text() or "")
                except Exception:
                    pass

            text = "\n".join(full_text)
            return self._parse_text(text)

        except Exception:
            return None

    def _parse_text(self, text: str) -> StructuredPaper:
        """从纯文本中启发式提取结构。"""
        paper = StructuredPaper(parser_used="pypdf", raw_text=text)

        # 分段
        paragraphs = [p.strip() for p in re.split(r'\n\s*\n', text) if p.strip()]

        # 提取标题（第一段）
        if paragraphs:
            paper.title = paragraphs[0].replace("\n", " ")

        # 尝试提取摘要
        for i, p in enumerate(paragraphs[:10]):
            if "abstract" in p.lower()[:20] or "摘要" in p[:10]:
                abstract_start = p[8:] if "abstract" in p.lower() else p[2:]
                if i + 1 < len(paragraphs):
                    paper.abstract = abstract_start + " " + paragraphs[i + 1]
                else:
                    paper.abstract = abstract_start
                break

        # 提取参考文献
        paper.references = self._extract_references_text(text)

        # 尝试按章节分割
        paper.sections = self._split_sections(text)

        # 提取方法学信息
        paper.methods = self._extract_methods(paper.sections)

        return paper

    def _extract_references_text(self, text: str) -> List[BibEntry]:
        """从文本中提取参考文献列表。"""
        refs = []

        # 找到参考文献部分的起始位置
        ref_patterns = [
            r'(References|REFERENCES|参考文献|Bibliography|BIBLIOGRAPHY)\s*[\n\r]',
        ]

        ref_start = None
        for pattern in ref_patterns:
            match = re.search(pattern, text)
            if match:
                ref_start = match.end()
                break

        if not ref_start:
            return refs

        ref_text = text[ref_start:]

        # 分割参考文献条目（匹配 [1] 或 1. 开头的格式）
        entry_pattern = r'(?:^|\n)\s*\[?(\d+)\]?[.\)]?\s+'
        entries = re.split(entry_pattern, ref_text)

        for i in range(1, len(entries), 2):
            if i + 1 < len(entries):
                raw_ref = entries[i + 1].strip()
                if len(raw_ref) > 20 and len(raw_ref) < 2000:
                    bib = BibEntry(raw_text=raw_ref)
                    self._parse_bib_text(bib, raw_ref)
                    refs.append(bib)

        return refs[:100]  # 最多提取 100 条

    def _parse_bib_text(self, bib: BibEntry, raw: str):
        """启发式解析单条参考文献。"""
        # 年份
        year_match = re.search(r'\((\d{4})\)', raw) or re.search(r'(\d{4})\.', raw)
        if year_match:
            bib.year = year_match.group(1)

        # DOI
        doi_match = re.search(r'doi:\s*(\S+)', raw, re.IGNORECASE)
        if doi_match:
            bib.doi = doi_match.group(1).rstrip('.')

        # 标题（粗提取）
        first_quote = re.search(r'[""](.+?)[""]', raw)
        if first_quote:
            bib.title = first_quote.group(1)

    def _split_sections(self, text: str) -> Dict[str, str]:
        """按章节标题分割文本。"""
        sections = {}

        # 常见的章节标题模式
        headings = [
            r"1?\.?\s*(Introduction|INTRODUCTION)",
            r"2?\.?\s*(Related Work|RELATED WORK|Background|BACKGROUND)",
            r"3?\.?\s*(Method|METHOD|Methodology|METHODOLOGY|Approach|APPROACH)",
            r"4?\.?\s*(Experiments|EXPERIMENTS)",
            r"5?\.?\s*(Results|RESULTS)",
            r"6?\.?\s*(Discussion|DISCUSSION)",
            r"7?\.?\s*(Conclusion|CONCLUSION|Conclusions|CONCLUSIONS)",
        ]

        # 找出所有章节边界
        boundaries = []
        for pattern in headings:
            for match in re.finditer(pattern, text):
                boundaries.append((match.start(), match.group(0).strip()))

        boundaries.sort()

        # 提取每个章节的内容
        for i, (pos, heading) in enumerate(boundaries):
            next_pos = boundaries[i + 1][0] if i + 1 < len(boundaries) else len(text)
            content = text[pos:next_pos].strip()
            # 移除标题行
            content_lines = content.split('\n')
            if content_lines:
                content = '\n'.join(content_lines[1:]).strip()
            sections[heading] = content[:5000]  # 限制长度

        return sections

    def _extract_methods(self, sections: Dict[str, str]) -> MethodSection:
        """从章节中提取方法学信息。"""
        methods = MethodSection(datasets=[], metrics=[], hyperparameters={})

        method_text = ""
        for name, content in sections.items():
            if "method" in name.lower() or "实验" in name or "experiment" in name.lower():
                method_text += " " + content

        if not method_text:
            return methods

        # 提取数据集
        dataset_patterns = [
            r'(\w+(?:-\w+)*\s+(?:Dataset|dataset|Corpus|corpus|Benchmark|benchmark))',
            r'(?:dataset|corpus|benchmark)\s+(?:of|is|was|were|named|called)?\s*[""]?(\w+(?:-\w+)*)[""]?',
        ]
        for pattern in dataset_patterns:
            for match in re.finditer(pattern, method_text):
                ds = match.group(1)
                if ds and len(ds) > 2 and ds not in methods.datasets:
                    methods.datasets.append(ds)

        # 提取指标
        metric_keywords = [
            "accuracy", "precision", "recall", "F1", "F-1", "F1-score", "AUC", "AUROC",
            "BLEU", "ROUGE", "METEOR", "perplexity", "PPL", "MCC", "IoU", "mAP",
        ]
        for metric in metric_keywords:
            if metric.lower() in method_text.lower():
                methods.metrics.append(metric)

        methods.analysis = method_text[:2000]

        return methods


class PDFParser:
    """PDF 解析器入口，自动选择最佳后端。"""

    def __init__(self, grobid_url: str = "http://localhost:8070", prefer_grobid: bool = True):
        self.grobid = GrobidClient(grobid_url)
        self.local = LocalPDFParser()
        self.prefer_grobid = prefer_grobid

    def parse(self, pdf_path: str | Path, force_local: bool = False) -> StructuredPaper:
        """解析 PDF，自动选择最佳后端。"""
        result = None

        if not force_local and self.prefer_grobid:
            result = self.grobid.process_fulltext(pdf_path)

        if result is None:
            # fallback 到本地解析
            result = self.local.parse(pdf_path)

        if result is None:
            # 最后的 fallback
            result = StructuredPaper(parser_used="fallback")
            if HAS_PYPDF:
                try:
                    reader = PdfReader(str(pdf_path))
                    result.raw_text = "\n".join(page.extract_text() or "" for page in reader.pages)
                except Exception:
                    pass

        return result

    def batch_parse(self, pdf_paths: List[str | Path], output_dir: str | Path = None) -> List[StructuredPaper]:
        """批量解析 PDF。"""
        results = []
        output_path = Path(output_dir) if output_dir else None

        for pdf_path in pdf_paths:
            paper = self.parse(pdf_path)
            results.append(paper)

            if output_path:
                output_path.mkdir(parents=True, exist_ok=True)
                out_file = output_path / (Path(pdf_path).stem + ".json")
                with open(out_file, "w", encoding="utf-8") as f:
                    json.dump(self._paper_to_dict(paper), f, ensure_ascii=False, indent=2)

        return results

    def _paper_to_dict(self, paper: StructuredPaper) -> Dict:
        return {
            "title": paper.title,
            "authors": paper.authors,
            "abstract": paper.abstract,
            "keywords": paper.keywords,
            "doi": paper.doi,
            "journal": paper.journal,
            "year": paper.year,
            "sections": paper.sections,
            "references": [
                {
                    "id": r.id,
                    "title": r.title,
                    "authors": r.authors,
                    "journal": r.journal,
                    "volume": r.volume,
                    "issue": r.issue,
                    "pages": r.pages,
                    "year": r.year,
                    "doi": r.doi,
                }
                for r in paper.references
            ],
            "methods": {
                "study_design": paper.methods.study_design if paper.methods else "",
                "datasets": paper.methods.datasets if paper.methods else [],
                "metrics": paper.methods.metrics if paper.methods else [],
            } if paper.methods else {},
            "parser_used": paper.parser_used,
        }
