---
name: chart_util
description: Generate charts (bar, line, pie, scatter) as SVG from CSV/JSON data. No external dependencies — produces inline SVG markup for embedding in reports, papers, or web pages.
category: office
entry_function: main
parameters:
  type: object
  properties:
    action:
      type: string
      description: One of bar, line, pie, scatter.
    data:
      type: array
      description: List of data dicts. Each dict must have at least x and y fields.
    path:
      type: string
      description: Path to CSV/JSON data file (alternative to inline data).
    x:
      type: string
      description: Column name for x-axis / categories.
    y:
      type: string
      description: Column name for y-axis / values.
    title:
      type: string
      description: Chart title.
    xlabel:
      type: string
      description: X-axis label.
    ylabel:
      type: string
      description: Y-axis label.
    width:
      type: integer
      description: Chart image width (default 800).
    height:
      type: integer
      description: Chart image height (default 500).
    color:
      type: string
      description: Primary color as hex or name (default "#2563eb").
    colors:
      type: array
      items:
        type: string
      description: Color palette for multi-series or pie slices.
    output:
      type: string
      description: Output file path (.svg). Prints SVG to stdout if omitted.
    horizontal:
      type: boolean
      description: For bar charts — draw horizontal bars (default false).
    stacked:
      type: boolean
      description: For bar charts — stack series (default false).
    group_by:
      type: string
      description: Column for grouping (multi-series).
    smooth:
      type: boolean
      description: For line charts — smooth curves (default false).
  required:
    - action
keywords: [chart, bar, line, pie, scatter, svg, visualize, plot, graph]
produces:
  stdout: SVG markup string when no output path given.
side_effects:
  - Writes SVG to file when output path given.
risk: low
---
