---
name: paper_compare
version: 1.0.0
description: Side-by-side comparison of two or more papers — diff methodologies, findings, datasets, contributions, and identify agreements vs contradictions.
category: research
entry_function: main
parameters:
  type: object
  properties:
    action:
      type: string
      description: "compare"
      enum: ["compare"]
    papers:
      type: array
      description: List of 2+ paper dicts to compare.
    readings:
      type: array
      description: Structured readings from paper_reader (alternative to papers).
    path:
      type: string
      description: JSON file path containing papers or readings.
    lang:
      type: string
      description: "Output language: zh | en"
      enum: ["zh", "en"]
      default: "zh"
  required:
    - action
keywords: [paper, compare, comparison, diff, methodology, findings, side-by-side, contradiction, agreement]
---

# Paper Compare v1.0.0

Side-by-side paper comparison. Takes 2+ papers and produces a structured comparison:

## Comparison Dimensions

- **Problem & Motivation**: what problem does each paper address? Same problem or different angles?
- **Method**: approaches used — are they complementary, competing, or orthogonal?
- **Datasets & Benchmarks**: which datasets used? Any overlap?
- **Key Findings**: what did each paper conclude? Any agreements or contradictions?
- **Strengths & Limitations**: what does each paper do well / poorly?
- **Innovation**: how novel is each contribution?

## Output

- Comparison table (Markdown-compatible)
- Agreement/contradiction signals
- Complementary reading recommendation: "read Paper A for method, Paper B for evaluation"
