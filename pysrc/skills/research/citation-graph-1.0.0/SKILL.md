---
name: citation_graph
description: Build citation graphs with multi-depth reference expansion, timeline analysis, topic clusters, venue distribution, and Mermaid visualization. Supports query, expand, and analyze modes.
category: research
entry_function: main
parameters:
  type: object
  properties:
    action:
      type: string
      description: "build | query | expand | analyze | author_network"
      enum: ["build", "query", "expand", "analyze", "author_network"]
    query:
      type: string
      description: Topic query for OpenAlex search.
    papers:
      type: array
      description: Paper metadata list.
    limit:
      type: integer
      description: Max papers to search (default 20).
    depth:
      type: integer
      description: Reference expansion depth (0=no expansion, 1=direct refs, 2=refs-of-refs). Default 1.
    readings:
      type: array
      description: Structured reading list from paper_reader. Automatically converted to papers for graph building.
readings:
      type: array
      description: Structured reading list from paper_reader. Automatically converted to papers for graph building.
    max_nodes:
      type: integer
      description: Maximum nodes in the graph (default 50).
    include_references:
      type: boolean
      description: Fetch referenced works (default false).
    paper:
      type: object
      description: Single paper metadata object for action=expand.
  required:
    - action
keywords: [citation, graph, openalex, references, related work, mermaid, timeline, clusters]
---

# Citation Graph

Enhanced citation graph builder with:

- **Multi-depth expansion**: Follow reference chains up to configurable depth
- **Timeline analysis**: Year-by-year publication trends
- **Topic clusters**: Keyword-based paper grouping
- **Venue distribution**: Top venues ranked by paper count
- **Mermaid output**: Ready-to-render graph visualization
- **Three modes**: `query` (search topic), `expand` (explore one paper's network), `analyze` (full depth analysis)


