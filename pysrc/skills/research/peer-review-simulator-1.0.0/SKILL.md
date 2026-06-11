---
name: peer_review_simulator
version: 1.1.0
description: Domain-aware structured peer review — evaluates novelty, methodology, soundness, clarity, related work, and reproducibility with field-specific checklists, red-flag detection, and actionable revision suggestions.
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
      description: Paper abstract (required).
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
      description: "Research field override: ai_ml | nlp | systems | hci | security | theory. Auto-detected if not specified."
      enum: ["ai_ml", "nlp", "systems", "hci", "security", "theory"]
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

# Peer Review Simulator v1.1.0

Domain-aware structured peer review simulation. Detects the research field from title+abstract and applies field-specific evaluation criteria.

## Supported Fields

| Field | Key Checks |
|-------|-----------|
| **AI/ML** | Benchmark comparison, ablation, significance testing, hyperparameter sensitivity |
| **NLP** | Human evaluation, LLM baselines, multi-dataset, prompt disclosure |
| **Systems** | End-to-end benchmarks, resource metrics, scalability, fault tolerance |
| **HCI** | User study design, sample size, demographics, qualitative analysis |
| **Security** | Threat model, attack/defense evaluation, assumptions |
| **Theory** | Formal statements, proofs, bound comparison, tightness |

## Review Dimensions (1-5 scale)

| Dimension | What it checks |
|-----------|---------------|
| **Novelty** | Clear gap statement? Contribution well-positioned? Compared to SOTA? |
| **Methodology** | Field-specific required elements present? Any red flags? |
| **Experiment** | Statistical tests? Ablation? Multiple datasets? Error bars? |
| **Clarity** | Structured sections? Well-sized paragraphs? Logical flow? |
| **Related Work** | Citation count? Recent references? Top-venue citations? |

## Red Flag Detection

Each field has specific red flags that automatically reduce scores and generate warnings:
- AI/ML: single dataset, no baselines, <1% improvement without significance
- NLP: only BLEU/ROUGE, no LLM comparison, prompts not disclosed
- Systems: micro-benchmarks only, single-machine eval
- HCI: n<12, single demographic, lab-only for real-world claims
- Security: no threat model, weak baselines

## Output

- Overall recommendation with score
- Per-dimension scores with detailed breakdown
- Strengths and weaknesses with specific evidence
- Actionable revision suggestions
- Field-specific venue expectations
- Natural-language summary for quick assessment
