---
name: paper_fetch_review
description: Fetch configured top-tier conference/journal papers only, fill missing abstracts, rank them, report the active venue policy, and optionally generate a structured literature review using an OpenAI-compatible LLM.
entry_function: main
timeout_sec: 150
parameters:
  type: object
  properties:
    action:
      type: string
      description: Use "fetch" or "search" to return papers, "review" to fetch papers and generate a literature review, or "policy" to inspect the active venue whitelist.
    query:
      type: string
      description: Search query, for example "retrieval augmented generation".
    year_start:
      type: integer
      description: Earliest publication year, for example 2024.
    limit:
      type: integer
      description: Maximum papers to retrieve before filtering. Default is 100.
    top_n:
      type: integer
      description: Number of papers to return and cite in the review. Default is all filtered papers.
    generate_review:
      type: boolean
      description: Whether to generate a structured literature review after fetching papers.
    domain:
      type: string
      description: Optional single venue-policy domain such as ai, nlp, cv, data, hci, cs, is, management, medicine. Use management for 信管/信息管理/信息系统/MIS topics; use hci for human-AI collaboration or human-computer interaction when the user did not ask for a management/IS lens.
    fill_abstracts:
      type: boolean
      description: Whether to enrich missing abstracts for the final candidate set. Default true.
    abstract_limit:
      type: integer
      description: Maximum final candidates to enrich when abstracts are missing. Default 8.
  required:
    - action
    - query
    domains:
      type: array
      items:
        type: string
      description: List of venue-policy domains for cross-domain search (e.g., ["ai","nlp","hci"]). Overrides single `domain` when set.
    venues:
      type: array
      items:
        type: string
      description: Filter papers to specific venue names only (e.g., ["NeurIPS","ICML"]).
keywords: [paper, literature, review, top venue, top journal, top conference, arxiv, openalex, scholar, citations, abstract]
---

# Paper Fetch Skill

Fetches top-tier conference/journal papers from OpenAlex, filters with `pysrc/skills/research/config/venues.toml`, fills missing abstracts for the final candidate set, ranks by venue policy/citations, and can generate a structured literature review.

## Actions

- `fetch`: fetch papers and return a ranked list.
- `search`: alias of `fetch`, kept for planner compatibility.
- `review`: fetch papers and generate a structured literature review.
- `policy`: return the active venue whitelist summary without network calls.

## Notes

- The script only accepts configured top conferences/journals. A result count of 0 means no accepted top-tier venue paper was found in the fetched candidate set.
- The script automatically normalizes common domain mistakes. For example, 信管/信息系统 queries are searched under the management/IS venue policy, and broad human-AI collaboration queries are searched under HCI unless the user explicitly asks for another lens.
- Literature review generation uses `pysrc/model.toml` or `OPENAI_API_KEY`; never print API keys or base URLs in user-facing output.

