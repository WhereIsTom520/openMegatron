---
name: cross_category_learner
description: Cross-category failure pattern discovery and self-learning. Analyzes failures across skill categories (research, media, code, office) to discover reusable patterns and improve pre-flight checks.
category: research
entry_function: main
parameters:
  type: object
  properties:
    action:
      type: string
      description: One of stats, patterns, learn, clear.
  required:
    - action
keywords: [learning, pattern, cross-category, skill, failure, self-improvement]
produces:
  stdout: JSON with pattern statistics or pattern list.
side_effects:
  - Reads and writes CrossCategoryLearner state data.
risk: low
---
