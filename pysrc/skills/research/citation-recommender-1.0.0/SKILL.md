---
name: citation_recommender
description: Recommend citations for sentences in your paper draft using TF-IDF similarity against a corpus of top-venue papers. Builds corpus from paper-library, top_paper_search results, or provided paper list.
category: research
entry_function: main
parameters:
  type: object
  properties:
    action:
      type: string
      description: One of recommend, build_corpus, list_corpus, clear_corpus.
    text:
      type: string
      description: Draft text to analyze (for recommend action).
    papers:
      type: array
      description: Paper list to build corpus from (for build_corpus).
    corpus_file:
      type: string
      description: Custom corpus file path (default ~/.openmegatron/citation_corpus.json).
    top_k:
      type: integer
      description: Max citations per sentence (default 3).
    min_similarity:
      type: number
      description: Minimum cosine similarity threshold (default 0.15).
    max_sentences:
      type: integer
      description: Max sentences to process (default 50).
  required:
    - action
keywords: [citation, recommend, tfidf, sentence, similarity, bibtex, reference, cite]
produces:
  stdout: JSON with sentence-level citation recommendations and combined bibliography.
side_effects:
  - Reads/writes corpus file at ~/.openmegatron/citation_corpus.json.
risk: low
---
