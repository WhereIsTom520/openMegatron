---
name: peer_review_simulator
version: 1.0.0
description: Simulate structured peer review for a draft paper — evaluate novelty, methodology, clarity, soundness, and produce actionable revision suggestions.
category: research
entry_function: main
parameters:
  type: object
  properties:
    action:
      type: string
      description: "review"
      enum: ["review"]
    title:
      type: string
      description: Paper title.
    abstract:
      type: string
      description: Paper abstract.
    draft:
      type: string
      description: Full paper text or path to draft file.
    venue_type:
      type: string
      description: "Target venue type for review standards: journal | conference | workshop"
      enum: ["journal", "conference", "workshop"]
      default: "journal"
    field:
      type: string
      description: Research field for domain-specific checks.
    lang:
      type: string
      description: "Review output language: zh | en"
      enum: ["zh", "en"]
      default: "zh"
    strictness:
      type: string
      description: "Review strictness: friendly | standard | critical"
      enum: ["friendly", "standard", "critical"]
      default: "standard"
  required:
    - action
    - abstract
keywords: [peer review, review, simulate, evaluate, revision, improve, draft, feedback, accept, reject]
---

# Peer Review Simulator v1.0.0

Simulates a structured peer review for a draft paper before submission.

## Review Dimensions

| Dimension | Checks |
|-----------|--------|
| **Novelty** | Is the contribution clearly stated? Compared to SOTA? |
| **Methodology** | Is the method clearly described? Reproducible? Appropriate for the claims? |
| **Soundness** | Are claims supported by evidence? Statistical rigor? Ablation studies? |
| **Clarity** | Is the writing clear? Figures/tables well-labeled? Logical flow? |
| **Related Work** | Are key references cited? Is positioning clear vs prior work? |
| **Impact** | Would this paper influence the field? Practical or theoretical significance? |

## Output

- Overall recommendation: Accept / Minor Revision / Major Revision / Reject
- Score per dimension (1-5)
- Strengths (what works well)
- Weaknesses (what needs improvement)
- Specific actionable suggestions
- Missing references that should be cited
