---
name: paper_draft
description: Generate structured paper drafts (literature review, full paper sections) and export to LaTeX or Markdown with embedded BibTeX references. Completes the research workflow from evidence matrix to publishable draft.
category: research
entry_function: main
parameters:
  type: object
  properties:
    action:
      type: string
      description: >
        One of: generate_review, generate_draft, export_latex, export_markdown.
    topic:
      type: string
      description: Research topic / paper title.
    matrix:
      type: array
      description: Evidence matrix rows from evidence_matrix skill.
    gap_analysis:
      type: object
      description: Research gap analysis from evidence_matrix skill.
    papers:
      type: array
      description: Paper list (optional, used for reference formatting).
    sections:
      type: array
      items:
        type: string
      description: Sections to include (for generate_draft). Default: all standard sections.
    abstract_only:
      type: boolean
      description: Generate only abstract (for quick summary).
    output:
      type: string
      description: Output file path (.tex or .md).
    overwrite:
      type: boolean
      description: Overwrite output if exists.
  required:
    - action
    - topic
keywords: [paper, draft, latex, markdown, review, write, export, publish, thesis]
produces:
  stdout: JSON with draft sections or file path.
side_effects:
  - Writes .tex or .md files when output path given.
  - Calls LLM via model.toml config.
risk: low
---
