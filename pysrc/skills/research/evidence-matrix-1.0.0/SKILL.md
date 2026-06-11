---
name: evidence_matrix
version: 1.1.0
description: Build evidence matrices, compare matrices to find contradictions, and detect methodological conflicts across papers.
entry_function: main
parameters:
  type: object
  properties:
    action:
      type: string
      description: "build | compare | contradictions"
      enum: ["build", "compare", "contradictions"]
    papers:
      type: array
      description: Paper metadata list (for build).
    readings:
      type: array
      description: Structured readings from paper_reader (for build).
    path:
      type: string
      description: JSON file path containing papers or readings.
    matrix_a:
      type: object
      description: First evidence matrix (for compare).
    matrix_b:
      type: object
      description: Second evidence matrix (for compare).
    output_format:
      type: string
      description: "Output format: json | csv. Default json."
      enum: ["json", "csv"]
    out:
      type: string
      description: Optional output path.
  required:
    - action
keywords: [evidence, matrix, literature review, synthesis, research gap, innovation, compare, contradiction, conflict]
---

# Evidence Matrix v1.1.0

## Actions

### `build`
Original behavior — structured evidence matrix with method distribution, contribution distribution, recurring limitations, underexplored angles, and potential innovation directions.

### `compare` ★ NEW
Compare two evidence matrices (e.g., from different search queries, time periods, or domains):
- **Method overlap**: which methods appear in both? Which are unique to each?
- **Contribution shift**: how do contribution types differ between matrix A and B?
- **Venue comparison**: do the two sets target different venues?
- **Gap comparison**: what gaps does each reveal that the other misses?
- Output: side-by-side comparison table + summary

### `contradictions` ★ NEW
Find contradictory findings across papers in a matrix:
- **Direct contradictions**: Paper A claims X improves Y, Paper B claims X does not improve Y
- **Methodological conflicts**: same problem, different approaches with incompatible assumptions
- **Dataset disagreements**: same benchmark, different reported scores → potential evaluation issues
- **Interpretation conflicts**: same result, different conclusions drawn
- Output: conflict pairs with confidence scores and evidence excerpts