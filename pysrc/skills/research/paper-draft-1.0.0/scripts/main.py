from __future__ import annotations

import asyncio
import json
import re
import sys
from datetime import datetime
from pathlib import Path

try:
    import tomllib
except ImportError:
    import tomli as tomllib

from openai import AsyncOpenAI

PYSRC = str(Path(__file__).resolve().parents[4])
if PYSRC not in sys.path:
    sys.path.insert(0, PYSRC)


def parse_cli_args() -> dict:
    if len(sys.argv) <= 1:
        return {}
    raw = sys.argv[1]
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    return {}


# ── LLM config ──────────────────────────────────────────────────────────

MODEL_TOML = Path(__file__).resolve().parents[4] / "model.toml"


def load_llm_config() -> dict:
    if not MODEL_TOML.exists():
        raise FileNotFoundError(f"LLM config not found: {MODEL_TOML}")
    with open(MODEL_TOML, "rb") as f:
        config = tomllib.load(f)
    llm_block = config.get("llm", {})
    active = llm_block.get("active_provider", "openai")
    provider = llm_block.get(active, {})
    api_key = provider.get("api_key") or llm_block.get("api_key", "")
    base_url = provider.get("base_url") or llm_block.get("base_url", "")
    model = provider.get("model", "gpt-4o-mini")
    extra_params = provider.get("extra_params", {}) or {}
    if not api_key:
        raise ValueError(f"API key not configured for provider: {active}")
    return {"api_key": api_key, "base_url": base_url, "model": model, "extra": extra_params}


async def call_llm(llm_cfg: dict, system: str, prompt: str, max_tokens: int = 4096) -> str:
    client = AsyncOpenAI(api_key=llm_cfg["api_key"], base_url=llm_cfg["base_url"])
    resp = await client.chat.completions.create(
        model=llm_cfg["model"],
        messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        temperature=0.7,
        max_tokens=max_tokens,
    )
    content = resp.choices[0].message.content or ""
    # Try to extract JSON from markdown fences
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", content)
    return m.group(1) if m else content


# ── Preprocessing helpers ─────────────────────────────────────────────────

def _group_matrix_by_theme(matrix: list) -> dict:
    """Group evidence matrix entries by method_category into thematic clusters.

    Returns a dict mapping theme name -> list of matrix rows, sorted by
    paper count descending.
    """
    themes: dict[str, list] = {}
    for row in (matrix or []):
        cat = (row.get("method_category") or "general").strip()
        if not cat:
            cat = "general"
        themes.setdefault(cat, []).append(row)
    return dict(sorted(themes.items(), key=lambda kv: -len(kv[1])))


def _extract_research_questions(matrix: list) -> list[str]:
    """Extract distinct research questions from the evidence matrix."""
    seen = set()
    rqs = []
    for row in (matrix or []):
        rq = (row.get("research_question_or_problem") or "").strip()
        if rq and rq not in seen:
            seen.add(rq)
            rqs.append(rq)
    return rqs


def _build_structured_evidence_context(matrix: list, gap: dict) -> str:
    """Build a detailed, structured evidence context for LLM prompts.

    Groups papers by theme, includes method, contribution, RQ, findings,
    limitations, and gaps for each paper, followed by a gap analysis summary.
    """
    parts = []
    themes = _group_matrix_by_theme(matrix)
    for theme, papers in themes.items():
        parts.append(f"\n### Theme: {theme} ({len(papers)} papers)")
        for r in papers:
            ref_id = r.get("ref_id", "?")
            findings = (r.get("main_evidence_or_findings") or "")[:250]
            limitations = (r.get("limitations") or "")[:150]
            gap_text = (r.get("research_gap_or_open_question") or "")[:150]
            parts.append(
                f"  [{ref_id}] {r.get('title', '')} ({r.get('year', '')}, {r.get('venue', '')})\n"
                f"      Method: {r.get('method_category', '')} | "
                f"Contribution: {r.get('contribution_type', '')}\n"
                f"      RQ: {r.get('research_question_or_problem', '')}\n"
                f"      Findings: {findings}\n"
                f"      Limitations: {limitations}\n"
                f"      Gap: {gap_text}"
            )
    parts.append("\n### Gap Analysis Summary")
    parts.append(f"  Underexplored angles: {gap.get('underexplored_angles', [])}")
    parts.append(f"  Recurring limitations: {gap.get('recurring_limitations', [])}")
    parts.append(
        f"  Innovation directions: {gap.get('potential_innovation_directions', [])}"
    )
    return "\n".join(parts)


def _format_bib_entry(paper: dict, index: int) -> str:
    """Format a single BibTeX entry from a paper dict."""
    authors = str(paper.get("authors", "Unknown")).replace(" and ", " and ")
    title = str(paper.get("title", "Untitled"))
    year = str(paper.get("year", "n.d."))
    venue = str(paper.get("venue", "Unknown venue"))
    doi = str(paper.get("doi", ""))
    first_author = (
        authors.split(",")[0].split(" and ")[0].strip().replace(" ", "")
    )
    key = re.sub(r"[^A-Za-z0-9]+", "", f"{first_author}{year}") or f"ref{index}"
    return (
        f"@article{{{key},\n"
        f"  author = {{{authors}}},\n"
        f"  title = {{{title}}},\n"
        f"  journal = {{{venue}}},\n"
        f"  year = {{{year}}},\n"
        f"  doi = {{{doi}}}\n"
        f"}}"
    )


# ── Prompts ──────────────────────────────────────────────────────────────

REVIEW_SYSTEM = """You are an experienced academic researcher writing a structured literature review.
Your output must be grounded in the provided evidence matrix — every substantive claim must reference
at least one source using [ref_id] notation (e.g., "Prior work [3,7] has shown that...").

Write in formal academic English. Structure your thinking before writing:
1. Identify the 2-4 main thematic clusters in the evidence
2. For each theme, synthesize findings across papers (do NOT just list them one by one)
3. Identify methodological patterns and contradictions between papers
4. Derive the research gap from limitations and underexplored angles in the matrix

Output valid JSON only, with no markdown fences."""

DRAFT_SYSTEM = """You are an experienced academic researcher writing a complete paper draft.
Every factual claim must be traceable to the evidence matrix via [ref_id] citations.

Guidelines:
- Abstract: Structured as Background, Objective, Methods, Results, Conclusion (150-250 words)
- Introduction: Open with domain context, narrow to specific problem, state research gap clearly
- Related Work: Organize by thematic clusters, synthesize within each cluster, identify what is missing
- Methodology: If the matrix describes methods, present them systematically
- Results/Findings: Organize by research question, present synthesized evidence with [ref_id] support
- Discussion: Address limitations found in the matrix, broader implications, concrete future work
- Conclusion: Summarize contributions and key takeaways (no new citations)
- References: Format consistently (Author, "Title", Venue, Year)

Output valid JSON only, with no markdown fences. Each section value is a string of academic prose."""


def build_review_prompt(topic: str, matrix: list, gap: dict) -> str:
    """Build a prompt for structured literature review generation.

    Includes thematic clusters, research questions, and structured evidence
    to guide the LLM toward evidence-grounded, thematically organized output.
    """
    themes = _group_matrix_by_theme(matrix)
    theme_summary = "\n".join(
        f"  - {theme}: {len(papers)} papers "
        f"[{', '.join(str(p.get('ref_id', '?')) for p in papers)}]"
        for theme, papers in themes.items()
    )
    rqs = _extract_research_questions(matrix)
    rq_text = "\n".join(
        f"  {i+1}. {rq}" for i, rq in enumerate(rqs[:10])
    )
    evidence = _build_structured_evidence_context(matrix, gap)

    return f"""Topic: {topic}

Thematic Clusters ({len(themes)} themes, {len(matrix or [])} papers):
{theme_summary}

Research Questions:
{rq_text or '  (infer from evidence matrix)'}

Structured Evidence:
{evidence[:8000]}

Generate a structured literature review as JSON with these keys:
- title: A descriptive academic title for the review
- abstract: Structured abstract with **Background:**, **Objective:**, **Methods:**,
  **Results:**, **Conclusion:** subsections (prefix each paragraph with the label)
- introduction: 2-3 paragraphs establishing the domain, its importance,
  and the scope of this review (cite key [ref_id] entries)
- themes: Array of {{theme_name, ref_ids (list of ints),
  synthesis (2-3 paragraphs synthesizing findings across papers, NOT a paper-by-paper list)}}
- research_gap: 1-2 paragraphs identifying specific gaps from the evidence,
  citing relevant [ref_id] entries for each gap
- transition_to_present_study: 1 paragraph connecting the identified gap
  to what future research should address
- references: Array of {{ref_id, citation}} using consistent
  Author, "Title", Venue, Year format

CRITICAL: Every claim in themes.synthesis, research_gap, and
transition_to_present_study MUST cite at least one [ref_id].
Do not fabricate findings not present in the evidence matrix."""


def build_draft_prompt(topic: str, sections: list, matrix: list, gap: dict) -> str:
    """Build a prompt for full paper draft generation.

    Provides thematic clusters, research questions, structured evidence,
    and detailed section-by-section writing guidelines for the LLM.
    """
    themes = _group_matrix_by_theme(matrix)
    theme_summary = "\n".join(
        f"  - {theme}: {len(papers)} papers "
        f"[{', '.join(str(p.get('ref_id', '?')) for p in papers)}]"
        for theme, papers in themes.items()
    )
    rqs = _extract_research_questions(matrix)
    rq_text = "\n".join(
        f"  RQ{i+1}: {rq}" for i, rq in enumerate(rqs[:8])
    )
    evidence = _build_structured_evidence_context(matrix, gap)
    sections_text = (
        ", ".join(sections)
        if sections
        else "abstract, introduction, related_work, methodology, results, discussion, conclusion"
    )

    return f"""Title / Topic: {topic}

Sections to generate: {sections_text}

Thematic Clusters:
{theme_summary}

Research Questions:
{rq_text or '  (synthesize from evidence)'}

Structured Evidence (truncated for space):
{evidence[:6000]}

Gap Analysis:
{json.dumps(gap or {}, ensure_ascii=False, indent=2)[:1500]}

Generate a complete paper draft as JSON with one key per requested section.
Follow these section-specific guidelines:

ABSTRACT: Structured as "Background: ... Objective: ... Methods: ... Results: ...
Conclusion: ..." (150-250 words total). Cite key [ref_id] entries.

INTRODUCTION: 3-5 paragraphs. Para 1: Broad domain context and significance.
Para 2: What prior work has established (cite [ref_id] clusters). Para 3: What
remains unresolved — the research gap. Para 4 (optional): How this paper
addresses the gap. End with a clear gap statement.

RELATED_WORK: 4-6 paragraphs organized by thematic clusters (use the clusters
above). For each theme: describe the common approach, synthesize key findings
across papers (cite 2-4 [ref_id] per theme), and note what is still missing.
Do NOT write one-paragraph-per-paper.

METHODOLOGY: If methods are described in the evidence, present them systematically
(design, data, measures, procedure). If the evidence is primarily review/survey
papers, describe the review methodology instead.

RESULTS: Organize by research question. For each RQ, synthesize what the evidence
collectively shows, citing relevant [ref_id] entries. Note any contradictions
between papers.

DISCUSSION: 3-4 paragraphs covering: (a) summary of key findings, (b) limitations
(draw from the matrix limitations column), (c) broader implications for the field,
(d) concrete future research directions.

CONCLUSION: 1-2 paragraphs summarizing the paper's contributions and key takeaways.
No new citations here.

REFERENCES: Array of {{ref_id: int, citation: string}} with consistently formatted
entries (Author, "Title", Venue, Year).

CRITICAL RULES:
1. Every factual claim in introduction, related_work, results, and discussion
   MUST be grounded in [ref_id] citations.
2. Do NOT fabricate findings. Only use what is in the evidence matrix.
3. For claims without direct evidence support, mark with [needs_verification].
4. Output ONLY valid JSON — no markdown fences, no explanatory text outside JSON."""


def _compact(text: str, max_chars: int = 3000) -> str:
    return text[:max_chars] if len(text) > max_chars else text


# ── LaTeX generator ──────────────────────────────────────────────────────

LATEX_TEMPLATE_ACL = r"""% ACL/EMNLP-style paper template
\documentclass[11pt,a4paper]{article}

%% ── Packages ──
\usepackage[utf8]{inputenc}
\usepackage[T1]{fontenc}
\usepackage[margin=1in]{geometry}
\usepackage{times}
\usepackage{latexsym}
\usepackage{microtype}
\usepackage{inconsolata}

%% ── Bibliography ──
\usepackage[numbers,sort&compress]{natbib}
\bibliographystyle{plainnat}

%% ── Hyperlinks ──
\usepackage[colorlinks=true,linkcolor=blue,citecolor=blue,urlcolor=blue]{hyperref}

%% ── Math & Tables ──
\usepackage{booktabs}
\usepackage{graphicx}
\usepackage{amsmath,amssymb}
\usepackage{caption}
\usepackage{subcaption}
\usepackage{multirow}
\usepackage{array}

%% ── Lists ──
\usepackage{enumitem}
\setlist{nosep,leftmargin=*}

%% ── Metadata ──
\title{%TITLE%}
\author{%AUTHOR%}
\date{\today}

\begin{document}

\maketitle

%CONTENT%

%REFERENCES%

\end{document}
"""


LATEX_TEMPLATE_GENERIC = r"""\documentclass[11pt,a4paper]{article}

%% ── Packages ──
\usepackage[utf8]{inputenc}
\usepackage[T1]{fontenc}
\usepackage[margin=1in]{geometry}
\usepackage{setspace}
\onehalfspacing
\usepackage{natbib}
\bibliographystyle{plainnat}
\usepackage[colorlinks=true,linkcolor=blue,citecolor=blue,urlcolor=blue]{hyperref}
\usepackage{booktabs}
\usepackage{graphicx}
\usepackage{amsmath,amssymb}
\usepackage{caption}
\usepackage{subcaption}

\title{%TITLE%}
\author{%AUTHOR%}
\date{\today}

\begin{document}

\maketitle

%CONTENT%

\bibliography{%BIBFILE%}

\end{document}
"""


# Ordered section name -> LaTeX heading mapping
_LATEX_SECTION_MAP: list[tuple[str, str]] = [
    ("abstract", "abstract"),
    ("introduction", r"\section{Introduction}"),
    ("related_work", r"\section{Related Work}"),
    ("methodology", r"\section{Methodology}"),
    ("methods", r"\section{Methods}"),
    ("results", r"\section{Results}"),
    ("findings", r"\section{Findings}"),
    ("discussion", r"\section{Discussion}"),
    ("conclusion", r"\section{Conclusion}"),
    ("references", "references"),
]

_LATEX_SECTION_LOOKUP = dict(_LATEX_SECTION_MAP)


def _escape_latex(text: str) -> str:
    """Escape special LaTeX characters in plain text."""
    escapes = [
        ("\\", r"\textbackslash{}"),
        ("&", r"\&"),
        ("%", r"\%"),
        ("$", r"\$"),
        ("#", r"\#"),
        ("_", r"\_"),
        ("{", r"\{"),
        ("}", r"\}"),
        ("~", r"\textasciitilde{}"),
        ("^", r"\textasciicircum{}"),
    ]
    # Only escape if not already escaped (crude heuristic)
    for char, replacement in escapes:
        if char == "\\":
            continue
        text = text.replace(char, replacement)
    return text


def _latex_format_abstract(abstract_text: str) -> str:
    """Format an abstract, handling structured (Background:/Objective: etc.)
    and unstructured text.
    """
    # Check for structured abstract markers
    markers = [
        "Background:", "Objective:", "Methods:", "Results:",
        "Conclusion:", "Background :", "Objective :", "Methods :",
        "Results :", "Conclusion :",
    ]
    has_structure = any(m in abstract_text for m in markers)
    if has_structure:
        # Convert structured abstract to LaTeX with \paragraph{} subsections
        lines = abstract_text.strip().split("\n")
        formatted = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            found_marker = False
            for marker in markers:
                clean_marker = marker.replace(" ", "")
                if stripped.startswith(marker) or stripped.lower().startswith(
                    marker.lower()
                ):
                    label = marker.rstrip(" :").rstrip(":")
                    formatted.append(
                        r"\noindent\textbf{" + label + r"} " + stripped[len(marker):].strip()
                    )
                    found_marker = True
                    break
            if not found_marker:
                formatted.append(stripped)
        body = "\n\n".join(formatted)
    else:
        body = abstract_text.strip()
    return r"\begin{abstract}" + "\n" + body + "\n" + r"\end{abstract}"


def _latex_format_references(refs: list) -> str:
    """Format a references section for LaTeX as a thebibliography environment."""
    if not refs:
        return ""
    lines = [
        r"\begin{thebibliography}{99}",
        "",
    ]
    for entry in refs:
        if isinstance(entry, dict):
            ref_id = entry.get("ref_id", "?")
            citation = entry.get("citation", "")
            lines.append(
                r"\bibitem{ref" + str(ref_id) + "} " + _escape_latex(str(citation))
            )
        elif isinstance(entry, str):
            lines.append(entry)
    lines.append("")
    lines.append(r"\end{thebibliography}")
    return "\n".join(lines)


def draft_to_latex(
    draft: dict,
    topic: str,
    bib_entries: list[str] | None = None,
    *,
    template: str = "acl",
) -> str:
    """Convert a draft dict to a properly formatted LaTeX document.

    Args:
        draft: Dict with section keys (abstract, introduction, related_work, etc.)
        topic: Paper title
        bib_entries: Optional list of BibTeX entry strings
        template: 'acl' for ACL/EMNLP style, 'generic' for standard article

    Returns:
        Complete LaTeX document as a string.
    """
    content_parts = []
    seen_keys: set[str] = set()

    # Abstract (always first)
    if "abstract" in draft and draft["abstract"]:
        content_parts.append(_latex_format_abstract(str(draft["abstract"])))
        seen_keys.add("abstract")

    # Ordered sections
    for key, heading in _LATEX_SECTION_MAP:
        if key in ("abstract", "references"):
            continue
        if key in draft and draft[key]:
            content_parts.append(heading + "\n" + str(draft[key]))
            seen_keys.add(key)

    # Unnamed / user-specified sections
    for key in draft:
        if key in seen_keys or key.startswith("_") or key == "status":
            continue
        val = draft[key]
        if key == "references" and isinstance(val, list):
            continue  # handled below
        if val:
            display = key.replace("_", " ").title()
            content_parts.append(
                r"\section{" + display + "}\n" + str(val)
            )
            seen_keys.add(key)

    content = "\n\n".join(content_parts)

    # References section
    references_block = ""
    refs_data = draft.get("references")
    if isinstance(refs_data, list) and refs_data:
        references_block = (
            r"\section{References}" + "\n" + _latex_format_references(refs_data) + "\n"
        )
    elif bib_entries:
        references_block = (
            r"\bibliography{references}" + "\n"
        )

    # Choose template
    if template == "generic":
        doc = LATEX_TEMPLATE_GENERIC
    else:
        doc = LATEX_TEMPLATE_ACL
        # ACL template uses %REFERENCES% placeholder for inline refs
        if references_block:
            doc = doc.replace("%REFERENCES%", references_block)
        else:
            doc = doc.replace("%REFERENCES%", "")

    if template == "generic" and bib_entries:
        doc = doc.replace("%BIBFILE%", "references")
    elif template == "generic":
        doc = doc.replace(r"\bibliography{%BIBFILE%}", "")

    doc = doc.replace("%TITLE%", _escape_latex(topic))
    doc = doc.replace("%AUTHOR%", "openMegatron Draft")
    return doc.replace("%CONTENT%", content)


def draft_to_markdown(
    draft: dict,
    topic: str,
    bib_entries: list[str] | None = None,
) -> str:
    """Convert a draft dict to clean, properly structured Markdown.

    Produces YAML frontmatter, numbered sections with proper heading levels,
    and a formatted reference list.
    """
    lines = [
        "---",
        f'title: "{topic}"',
        f'date: "{datetime.now().strftime("%Y-%m-%d")}"',
        "lang: en",
        "documentclass: article",
        "---",
        "",
        f"# {topic}",
        "",
    ]

    # Standard sections in order with heading level 2
    section_order = [
        "abstract", "introduction", "related_work",
        "methodology", "methods", "results", "findings",
        "discussion", "conclusion",
    ]
    seen: set[str] = set()

    for key in section_order:
        val = draft.get(key)
        if not val:
            continue
        seen.add(key)
        heading = key.replace("_", " ").title()
        lines.append(f"## {heading}")
        lines.append("")
        # Handle structured abstract
        if key == "abstract":
            for para in str(val).split("\n"):
                stripped = para.strip()
                if not stripped:
                    continue
                # Bold the structured labels
                for label in [
                    "Background:", "Objective:", "Methods:",
                    "Results:", "Conclusion:",
                ]:
                    if stripped.startswith(label):
                        stripped = f"**{label}**{stripped[len(label):]}"
                        break
                lines.append(stripped)
                lines.append("")
        else:
            lines.append(str(val))
            lines.append("")

    # Unnamed sections
    for key, val in draft.items():
        if key in seen or key.startswith("_") or key == "status":
            continue
        heading = key.replace("_", " ").title()
        lines.append(f"## {heading}")
        lines.append("")
        lines.append(str(val))
        lines.append("")

    # References section
    refs_data = draft.get("references")
    if isinstance(refs_data, list) and refs_data:
        lines.append("## References")
        lines.append("")
        for entry in refs_data:
            if isinstance(entry, dict):
                ref_id = entry.get("ref_id", "?")
                citation = entry.get("citation", "")
                lines.append(f"- **[{ref_id}]** {citation}")
            elif isinstance(entry, str):
                lines.append(f"- {entry}")
        lines.append("")
    elif bib_entries:
        lines.append("## References")
        lines.append("")
        for entry in bib_entries:
            lines.append(entry)
            lines.append("")

    return "\n".join(lines)


def _postprocess_draft(draft: dict, matrix: list) -> dict:
    """Post-process an LLM-generated draft to validate and clean up citations.

    - Strips markdown fences from section text
    - Validates that [ref_id] citations reference actual matrix entries
    - Warns about citations to non-existent ref_ids
    """
    valid_ref_ids = {
        int(r.get("ref_id", 0)) for r in (matrix or []) if r.get("ref_id") is not None
    }

    cleaned = {}
    for key, val in draft.items():
        if not isinstance(val, str):
            cleaned[key] = val
            continue
        # Strip any residual markdown fences
        text = val.strip()
        if text.startswith("```"):
            text = re.sub(r"^```[a-z]*\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        cleaned[key] = text.strip()

    # Validate citations in text sections
    issues = []
    for key in [
        "introduction", "related_work", "methodology", "methods",
        "results", "findings", "discussion",
    ]:
        text = cleaned.get(key)
        if not isinstance(text, str):
            continue
        cited = {int(m) for m in re.findall(r"\[(\d+)\]", text)}
        unknown = cited - valid_ref_ids
        if unknown:
            issues.append(
                f"Section '{key}' cites unknown ref_ids: {sorted(unknown)}"
            )

    if issues:
        cleaned["_citation_warnings"] = issues

    return cleaned


# ── Evidence validation ──────────────────────────────────────────────────

def build_evidence_coverage_report(draft: dict, matrix: list) -> dict:
    """Analyze which evidence matrix entries are cited in the draft.

    Returns a coverage report showing cited vs. uncited ref_ids,
    helping researchers verify that the draft covers the evidence base.
    """
    if not matrix:
        return {"coverage": "no_evidence", "cited": [], "uncited": [], "rate": 0.0}

    total_ref_ids = {
        int(r.get("ref_id", 0)) for r in matrix if r.get("ref_id") is not None
    }
    if not total_ref_ids:
        return {"coverage": "no_ref_ids", "cited": [], "uncited": [], "rate": 0.0}

    # Collect all [N] citations from all text sections
    all_text = " ".join(
        str(draft.get(k, ""))
        for k in draft
        if isinstance(draft.get(k), str)
    )
    cited = {int(m) for m in re.findall(r"\[(\d+)\]", all_text)}
    cited &= total_ref_ids  # only valid refs
    uncited = sorted(total_ref_ids - cited)
    cited_sorted = sorted(cited)
    rate = len(cited) / len(total_ref_ids) if total_ref_ids else 0.0

    return {
        "coverage": "complete" if rate >= 0.8 else ("partial" if rate >= 0.4 else "sparse"),
        "total_refs": len(total_ref_ids),
        "cited_count": len(cited),
        "uncited_count": len(uncited),
        "rate": round(rate, 3),
        "cited": cited_sorted,
        "uncited": uncited,
    }


# ── Main ─────────────────────────────────────────────────────────────────

async def main_async() -> int:
    args = parse_cli_args()
    action = args.get("action", "")
    topic = args.get("topic", "")
    if not action or not topic:
        print(
            json.dumps(
                {"status": "error", "error": "Missing 'action' or 'topic'."},
                ensure_ascii=False,
            )
        )
        return 2

    matrix = args.get("matrix") or []
    gap = args.get("gap_analysis") or {}
    papers = args.get("papers") or []
    output = args.get("output", "")
    overwrite = args.get("overwrite", False)

    # ── export_latex / export_markdown (no LLM needed) ──
    if action in ("export_latex", "export_markdown"):
        # Collect all string-valued keys as draft sections, plus handle
        # structured data like 'references' as a list
        draft: dict = {}
        for k, v in args.items():
            if k in ("action", "output", "topic", "bib_entries", "matrix",
                     "gap_analysis", "papers", "overwrite"):
                continue
            if isinstance(v, str):
                draft[k] = v
            elif isinstance(v, list) and k == "references":
                draft[k] = v
        if not draft:
            print(
                json.dumps(
                    {"status": "error",
                     "error": "No draft sections provided for export."},
                    ensure_ascii=False,
                )
            )
            return 2

        bib = args.get("bib_entries") or []
        template = args.get("template", "acl")

        if action == "export_latex":
            tex = draft_to_latex(draft, topic, bib, template=template)
            if output:
                p = Path(output).expanduser()
                if p.exists() and not overwrite:
                    print(
                        json.dumps(
                            {"status": "error", "error": f"Output exists: {p}"},
                            ensure_ascii=False,
                        )
                    )
                    return 2
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(tex, encoding="utf-8")
                print(
                    json.dumps(
                        {
                            "status": "success",
                            "action": "export_latex",
                            "template": template,
                            "output": str(p.resolve()),
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
            else:
                print(tex)
        else:
            md = draft_to_markdown(draft, topic, bib)
            if output:
                p = Path(output).expanduser()
                if p.exists() and not overwrite:
                    print(
                        json.dumps(
                            {"status": "error", "error": f"Output exists: {p}"},
                            ensure_ascii=False,
                        )
                    )
                    return 2
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(md, encoding="utf-8")
                print(
                    json.dumps(
                        {
                            "status": "success",
                            "action": "export_markdown",
                            "output": str(p.resolve()),
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
            else:
                print(md)
        return 0

    # ── LLM-based generation ──
    try:
        llm_cfg = load_llm_config()
    except (FileNotFoundError, ValueError) as e:
        print(
            json.dumps(
                {"status": "error", "error": str(e)}, ensure_ascii=False
            )
        )
        return 2

    try:
        if action == "generate_review":
            system = REVIEW_SYSTEM
            user = build_review_prompt(topic, matrix, gap)
            out = await call_llm(llm_cfg, system, user, max_tokens=8192)
            data = json.loads(out, strict=False)

            # Post-process: validate citations
            data = _postprocess_draft(data, matrix)

            # Build evidence coverage report
            coverage = build_evidence_coverage_report(data, matrix)

            # Build BibTeX entries from papers if available
            bib_entries = None
            if papers:
                bib_entries = [
                    _format_bib_entry(p, idx + 1)
                    for idx, p in enumerate(papers[:50])
                ]

            result = {
                "status": "success",
                "action": "generate_review",
                "topic": topic,
                "review": data,
                "paper_count": len(matrix),
                "evidence_coverage": coverage,
            }
            if bib_entries:
                result["bib_entries"] = bib_entries

            if output:
                p = Path(output).expanduser()
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(
                    json.dumps(result, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                result["output"] = str(p.resolve())
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0

        if action == "generate_draft":
            sections = args.get("sections") or [
                "abstract",
                "introduction",
                "related_work",
                "methodology",
                "results",
                "discussion",
                "conclusion",
            ]
            system = DRAFT_SYSTEM
            user = build_draft_prompt(topic, sections, matrix, gap)
            out = await call_llm(llm_cfg, system, user, max_tokens=16384)
            data = json.loads(out, strict=False)

            # Post-process: validate and clean up citations
            data = _postprocess_draft(data, matrix)

            # Build evidence coverage report
            coverage = build_evidence_coverage_report(data, matrix)

            # Build BibTeX entries from papers if available
            bib_entries = None
            if papers:
                bib_entries = [
                    _format_bib_entry(p, idx + 1)
                    for idx, p in enumerate(papers[:50])
                ]

            result = {
                "status": "success",
                "action": "generate_draft",
                "topic": topic,
                "sections": sections,
                **data,
                "paper_count": len(matrix),
                "evidence_coverage": coverage,
            }
            if bib_entries:
                result["bib_entries"] = bib_entries

            if output:
                p = Path(output).expanduser()
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(
                    json.dumps(result, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                result["output"] = str(p.resolve())
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0

        print(
            json.dumps(
                {"status": "error", "error": f"Unknown action: {action}"},
                ensure_ascii=False,
            )
        )
        return 2

    except json.JSONDecodeError:
        print(
            json.dumps(
                {"status": "error", "action": action,
                 "error": "LLM returned invalid JSON. Try again or check the evidence matrix format."},
                ensure_ascii=False,
            )
        )
        return 2
    except Exception as e:
        print(
            json.dumps(
                {"status": "error", "action": action, "error": str(e)},
                ensure_ascii=False,
            )
        )
        return 2


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())


