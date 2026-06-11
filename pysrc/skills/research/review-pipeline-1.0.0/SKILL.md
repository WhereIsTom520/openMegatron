---
name: review_pipeline
version: 1.1.0
description: Full research workflow — top-venue search, paper reading, evidence matrix, gap analysis, review generation, citation verification. Now with incremental update support.
entry_function: main
parameters:
  type: object
  properties:
    action:
      type: string
      description: "run | update"
      enum: ["run", "update"]
    query:
      type: string
      description: Research topic query.
    year_start:
      type: integer
      description: Earliest paper year.
    limit:
      type: integer
      description: Candidate search limit. Default 100.
    top_n:
      type: integer
      description: Number of papers to keep. Default 8.
    generate_review:
      type: boolean
      description: Whether to call LLM for a Chinese review.
    review_type:
      type: string
      description: narrative or systematic.
    citation_style:
      type: string
      description: gbt7714, ieee, apa, or bibtex.
    domain:
      type: string
      description: Venue-policy domain (ai, nlp, cv, data, hci, cs, is, management, medicine).
    fill_abstracts:
      type: boolean
      description: Enrich missing abstracts. Default true.
    out:
      type: string
      description: Optional JSON output path.
    previous_out:
      type: string
      description: Path to previous pipeline output JSON (for update action).
    new_papers:
      type: array
      description: New papers to add (for update action).
  required:
    - action
    - query
keywords: [review pipeline, literature review, systematic review, evidence matrix, citation verification, research gap, innovation, update, incremental]
---

# Review Pipeline v1.1.0

## Actions

### `run`
Original behavior — full pipeline: search → read → matrix → gap → review → verify.

### `update` ★ NEW
Incremental update: add new papers to an existing pipeline output without re-running the full workflow.
- Loads previous pipeline JSON from `previous_out`
- Adds `new_papers` to the existing paper set (deduplicating by DOI/title)
- Rebuilds only the evidence matrix and gap analysis (keeps existing review text)
- Much faster than full re-run — useful when you find 1-2 new papers after finishing a review
