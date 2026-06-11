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


# ── Prompts ──────────────────────────────────────────────────────────────

SYSTEM_ACADEMIC = """You are an experienced academic researcher and writer. Write in formal academic English.
Output valid JSON only, with no markdown fences."""

REVIEW_SYSTEM = """You are writing a literature review section for an academic paper.
Use the provided evidence matrix and gap analysis to write a structured review.
Output JSON with keys: "title", "introduction", "themes" (array of {name, papers, synthesis}), 
"research_gap", "transition_to_present_study"."""

DRAFT_SYSTEM = """You are writing a complete academic paper draft.
Write clear, well-structured academic prose with proper citations (using [Ref:N] notation).
Output JSON with keys for each section requested."""


def build_review_prompt(topic: str, matrix: list, gap: dict) -> str:
    rows_text = "\n".join(
        f"- [{r.get('ref_id','?')}] {r.get('title','')} ({r.get('year','')}, {r.get('venue','')}) "
        f"| Method: {r.get('method_category','')} | Finding: {r.get('main_evidence_or_findings','')[:200]}"
        for r in (matrix or [])[:30]
    )
    gap_text = json.dumps(gap or {}, ensure_ascii=False, indent=2)[:2000]
    return f"""Topic: {topic}

Evidence Matrix ({len(matrix or [])} papers):
{rows_text}

Gap Analysis:
{gap_text}

Generate a structured literature review as JSON with:
- title: section title
- introduction: 2-3 paragraph overview of the field
- themes: array of {{name, papers (ref IDs), synthesis (2-3 paragraphs)}}
- research_gap: 1 paragraph identifying the gap this paper addresses
- transition_to_present_study: 1 paragraph connecting gap to current work"""


def build_draft_prompt(topic: str, sections: list, matrix: list, gap: dict) -> str:
    rows_text = "\n".join(
        f"[Ref:{r.get('ref_id','?')}] {r.get('title','')} | {r.get('authors','')} ({r.get('year','')}, {r.get('venue','')})"
        for r in (matrix or [])[:40]
    )
    gap_text = json.dumps(gap or {}, ensure_ascii=False, indent=2)[:1500]
    sections_text = ", ".join(sections) if sections else "abstract, introduction, related_work, methodology, results, discussion, conclusion"
    return f"""Title / Topic: {topic}

Sections to generate: {sections_text}

Available references:
{rows_text}

Research Gap:
{gap_text}

Generate a complete paper draft as JSON with one key per section.
Each section value is a string of 2-6 paragraphs of formal academic prose.
Use [Ref:N] notation for citations.
Output ONLY valid JSON."""


def _compact(text: str, max_chars: int = 3000) -> str:
    return text[:max_chars] if len(text) > max_chars else text


# ── LaTeX generator ──────────────────────────────────────────────────────

LATEX_TEMPLATE = r"""\documentclass[11pt,a4paper]{article}

%% ── Packages ──
\usepackage[utf8]{inputenc}
\usepackage[T1]{fontenc}
\usepackage{geometry}
\geometry{margin=1in}
\usepackage{setspace}
\onehalfspacing
\usepackage{natbib}
\bibliographystyle{plainnat}
\usepackage{hyperref}
\hypersetup{colorlinks=true,linkcolor=blue,citecolor=blue,urlcolor=blue}
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


def draft_to_latex(draft: dict, topic: str, bib_entries: list[str] | None = None) -> str:
    content_parts = []
    section_map = {
        "abstract": "abstract",
        "introduction": r"\section{Introduction}",
        "related_work": r"\section{Related Work}",
        "methodology": r"\section{Methodology}",
        "methods": r"\section{Methods}",
        "results": r"\section{Results}",
        "findings": r"\section{Findings}",
        "discussion": r"\section{Discussion}",
        "conclusion": r"\section{Conclusion}",
    }
    if "abstract" in draft:
        content_parts.append(r"\begin{abstract}" + "\n" + draft["abstract"] + "\n" + r"\end{abstract}" + "\n")
    for key, heading in section_map.items():
        if key == "abstract":
            continue
        if key in draft and draft[key]:
            content_parts.append(heading + "\n" + draft[key] + "\n")
    # Unnamed sections (e.g. user-specified)
    for key in draft:
        if key not in section_map and key != "status" and not key.startswith("_"):
            content_parts.append(r"\section{" + key.replace("_", " ").title() + "}\n" + draft[key] + "\n")
    content = "\n".join(content_parts)
    doc = LATEX_TEMPLATE.replace("%TITLE%", topic).replace("%AUTHOR%", "openMegatron Draft")
    # Insert bibliography entries
    if bib_entries:
        bib_path = Path("references.bib")
        bib_path.write_text("\n\n".join(bib_entries), encoding="utf-8")
        doc = doc.replace("%BIBFILE%", "references")
    else:
        doc = doc.replace(r"\bibliography{%BIBFILE%}", "")
    return doc.replace("%CONTENT%", content)


def draft_to_markdown(draft: dict, topic: str, bib_entries: list[str] | None = None) -> str:
    lines = [
        "---",
        f'title: "{topic}"',
        f'date: "{datetime.now().strftime("%Y-%m-%d")}"',
        "lang: en",
        "---",
        "",
    ]
    for key, val in draft.items():
        if key.startswith("_") or key == "status":
            continue
        heading = key.replace("_", " ").title()
        lines.append(f"## {heading}\n")
        lines.append(str(val) + "\n")
    if bib_entries:
        lines.append("## References\n")
        for entry in bib_entries:
            lines.append(entry)
    return "\n".join(lines)


# ── Main ─────────────────────────────────────────────────────────────────

async def main_async() -> int:
    args = parse_cli_args()
    action = args.get("action", "")
    topic = args.get("topic", "")
    if not action or not topic:
        print(json.dumps({"status": "error", "error": "Missing 'action' or 'topic'."}, ensure_ascii=False))
        return 2

    matrix = args.get("matrix") or []
    gap = args.get("gap_analysis") or {}
    papers = args.get("papers") or []
    output = args.get("output", "")
    overwrite = args.get("overwrite", False)

    # ── export_latex / export_markdown (no LLM needed) ──
    if action in ("export_latex", "export_markdown"):
        draft = {k: v for k, v in args.items() if isinstance(v, str) and k not in ("action", "output", "topic")}
        if not draft:
            print(json.dumps({"status": "error", "error": "No draft sections provided for export."}, ensure_ascii=False))
            return 2
        bib = args.get("bib_entries") or []
        if action == "export_latex":
            tex = draft_to_latex(draft, topic, bib)
            if output:
                p = Path(output).expanduser()
                if p.exists() and not overwrite:
                    print(json.dumps({"status": "error", "error": f"Output exists: {p}"}, ensure_ascii=False))
                    return 2
                p.write_text(tex, encoding="utf-8")
                print(json.dumps({"status": "success", "action": "export_latex", "output": str(p.resolve())}, ensure_ascii=False, indent=2))
            else:
                print(tex)
        else:
            md = draft_to_markdown(draft, topic, bib)
            if output:
                p = Path(output).expanduser()
                if p.exists() and not overwrite:
                    print(json.dumps({"status": "error", "error": f"Output exists: {p}"}, ensure_ascii=False))
                    return 2
                p.write_text(md, encoding="utf-8")
                print(json.dumps({"status": "success", "action": "export_markdown", "output": str(p.resolve())}, ensure_ascii=False, indent=2))
            else:
                print(md)
        return 0

    # ── LLM-based generation ──
    try:
        llm_cfg = load_llm_config()
    except (FileNotFoundError, ValueError) as e:
        print(json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False))
        return 2

    try:
        if action == "generate_review":
            system = REVIEW_SYSTEM
            user = build_review_prompt(topic, matrix, gap)
            out = await call_llm(llm_cfg, system, user)
            data = json.loads(out, strict=False)
            result = {"status": "success", "action": "generate_review", "topic": topic,
                       "review": data, "paper_count": len(matrix)}
            if output:
                Path(output).expanduser().write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                result["output"] = str(Path(output).expanduser().resolve())
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0

        if action == "generate_draft":
            sections = args.get("sections") or [
                "abstract", "introduction", "related_work",
                "methodology", "results", "discussion", "conclusion"
            ]
            system = DRAFT_SYSTEM
            user = build_draft_prompt(topic, sections, matrix, gap)
            out = await call_llm(llm_cfg, system, user, max_tokens=8192)
            data = json.loads(out, strict=False)
            result = {"status": "success", "action": "generate_draft", "topic": topic,
                       "sections": sections, **data, "paper_count": len(matrix)}
            if output:
                Path(output).expanduser().write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                result["output"] = str(Path(output).expanduser().resolve())
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0

        print(json.dumps({"status": "error", "error": f"Unknown action: {action}"}, ensure_ascii=False))
        return 2

    except json.JSONDecodeError:
        print(json.dumps({"status": "error", "action": action, "error": "LLM returned invalid JSON."}, ensure_ascii=False))
        return 2
    except Exception as e:
        print(json.dumps({"status": "error", "action": action, "error": str(e)}, ensure_ascii=False))
        return 2


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())


