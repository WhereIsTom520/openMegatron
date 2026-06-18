---
name: preflight_check
version: 1.1.0
description: Universal pre-submission paper checklist — systematically audits 16 dimensions with 130+ deep heuristic checks, LaTeX-aware parsing, actionable fix suggestions with evidence, optional pdflatex compilation, and structured audit reports. Covers research coherence, abstract, introduction, related work, method, figures, tables, experiments, statistics, terminology, language, citations, LaTeX compilation, layout, blind review compliance, and final submission readiness.
category: research
entry_function: main
parameters:
  type: object
  properties:
    action:
      type: string
      description: "check — run the full 16-dimension preflight audit"
      enum: ["check"]
    draft:
      type: string
      description: Path to the paper draft (.tex or .md) or raw paper text to analyze.
    output:
      type: string
      description: "Output file path for the audit report (.md or .json). Default: stdout."
    dimensions:
      type: array
      items:
        type: string
      description: "Specific dimensions to check (1-16). Default: all 16."
      enum:
        - "research_coherence"
        - "abstract"
        - "introduction"
        - "related_work"
        - "method"
        - "figures"
        - "tables"
        - "experiments"
        - "statistics"
        - "terminology"
        - "language"
        - "citations"
        - "latex_compile"
        - "layout"
        - "blind_review"
        - "final_submission"
    venue_type:
      type: string
      description: "Target venue type: journal | conference | workshop"
      enum: ["journal", "conference", "workshop"]
      default: "journal"
    lang:
      type: string
      description: "Report output language: zh | en"
      enum: ["zh", "en"]
      default: "zh"
    strictness:
      type: string
      description: "Check strictness: friendly | standard | critical"
      enum: ["friendly", "standard", "critical"]
      default: "standard"
  required:
    - action
    - draft
keywords: [paper, preflight, checklist, submission, audit, review, check, quality, latex, proofread, compliance, blind review, layout, terminology]
produces:
  stdout: JSON with per-dimension scores, issues found, and fix suggestions.
side_effects:
  - Reads paper draft file if path provided.
  - Writes audit report file if output path given.
  - May call LLM for content analysis of paper text.
risk: low
---

# Preflight Check v1.0.0

Universal pre-submission paper checklist — systematically audits a paper draft across 16 dimensions before journal/conference submission.

## 16 Audit Dimensions

| # | Dimension | Key Checks |
|---|-----------|------------|
| 1 | **Research Coherence** | Main research question clear? Innovation consistent across sections? Contributions specific and verifiable? |
| 2 | **Abstract** | Background → method → results → conclusion flow? Data-supported claims? No new concepts not in body? |
| 3 | **Introduction** | Problem importance established? Gap identified? Innovation clearly stated? Contribution list concrete? |
| 4 | **Related Work** | Organized by theme? Covers traditional + recent + closest work? Gaps identified → motivates this paper? |
| 5 | **Method** | Module naming consistent? Input/output of each module clear? Symbols defined? Figures ↔ text ↔ pseudocode aligned? |
| 6 | **Figures** | Each figure has clear purpose? Terminology matches text? Readable at print size? Caption explains significance? |
| 7 | **Tables** | Headers clear? Units specified? Best values bolded? All baselines explained? Standard deviations reported? |
| 8 | **Experiments** | Dataset details + split explained? Fair baselines? Main + comparison + ablation all present? Failure cases discussed? |
| 9 | **Statistics** | Mean ± std reported? Multi-run or cross-validation? Core results backed by significance tests? |
| 10 | **Terminology** | Method/module names consistent? Abbreviations expanded on first use? No legacy/internal code names? |
| 11 | **Language** | No colloquialisms? No absolute claims without evidence? Appropriate hedging? Sentence length reasonable? |
| 12 | **Citations** | All keys exist? Recent + key works covered? Format consistent? No undefined references? |
| 13 | **LaTeX Compilation** | No errors? No missing figures? No undefined refs/cites? No overfull hboxes? Compiled 2-3× consecutively? |
| 14 | **Layout** | No large blank areas? Column balance OK? Figures/tables near first citation? No orphan headings? |
| 15 | **Blind Review** | Author names removed? Affiliations hidden? Acknowledgments removed? PDF metadata clean? No logos/watermarks? |
| 16 | **Final Submission** | Title informative? Abstract self-contained? Fig.1 shows innovation? Conclusions not overclaimed? No draft traces? |

## Scoring

Each dimension is scored 0-5 based on strictness level:
- **5**: Fully compliant, no issues found
- **4**: Minor issues, easy to fix
- **3**: Several issues, needs attention
- **2**: Significant problems, must revise
- **1**: Critical gaps, major rewrite needed
- **0**: Dimension missing or not applicable

## Output

- Overall readiness score (0-100)
- Per-dimension score with issue list
- Severity-tagged findings: 🔴 critical / 🟡 warning / 🔵 suggestion
- Actionable fix suggestions for each issue
- Priority-ordered fix checklist
- Venue-specific expectations check
