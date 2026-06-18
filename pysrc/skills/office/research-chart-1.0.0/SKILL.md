---
name: research_chart
version: 1.0.0
description: Generate publication-quality charts for academic papers — comparison bar charts, ablation waterfall plots, radar/spider charts, confusion matrices, ROC/PR curves, box plots, heatmaps, correlation matrices, timeline/Gantt charts, and LaTeX-compatible output. All charts export as high-resolution SVG/PNG/PDF with consistent styling for journal/conference submission.
category: office
entry_function: main
parameters:
  type: object
  properties:
    action:
      type: string
      description: |
        Chart type:
        - compare: multi-method comparison bar chart (the most common paper figure)
        - ablation: ablation study waterfall/bar chart showing contribution of each component
        - radar: radar/spider chart for multi-dimensional comparison
        - confusion: confusion matrix heatmap
        - roc: ROC curve (single or multi-model)
        - boxplot: box plot for distribution comparison
        - heatmap: generic heatmap (correlation, attention weights, etc.)
        - timeline: Gantt/timeline chart for method pipelines or schedules
        - scatter_fit: scatter plot with optional linear fit line
        - bar_error: bar chart with error bars
      enum: [compare, ablation, radar, confusion, roc, boxplot, heatmap, timeline, scatter_fit, bar_error]
    data:
      type: array
      description: Data array. Format depends on chart type (see docs).
    path:
      type: string
      description: Path to CSV/JSON data file (alternative to inline data).
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
      description: Output image width in pixels (default 1200 for print, 800 for screen).
      default: 1200
    height:
      type: integer
      description: Output image height in pixels (default 800).
      default: 800
    dpi:
      type: integer
      description: Output resolution (default 150 for screen, 300 for print).
      default: 150
    output:
      type: string
      description: Output file path. Supports .svg, .png, .pdf. If omitted, prints base64 PNG to stdout.
    style:
      type: string
      description: "Visual style preset: academic (grayscale-friendly, serif fonts), colored (colorblind-safe palette), dark (for presentations)"
      enum: [academic, colored, dark]
      default: academic
    font_scale:
      type: number
      description: Font size multiplier (default 1.0). Use 1.2 for presentations, 0.9 for dense figures.
      default: 1.0
    palette:
      type: array
      items:
        type: string
      description: Custom color palette as hex codes. Overrides style preset.
    legend:
      type: boolean
      description: Show legend (default true).
      default: true
    grid:
      type: boolean
      description: Show grid lines (default true for academic).
      default: true
    annotate:
      type: boolean
      description: Show value labels on bars/points (default true).
      default: true
    sort:
      type: boolean
      description: Sort bars by value descending (default false).
      default: false
    horizontal:
      type: boolean
      description: Draw horizontal bars instead of vertical (for compare/ablation).
      default: false
    error_bars:
      type: array
      description: Error bar values matching data array order (for compare/bar_error).
    x_rotation:
      type: integer
      description: X-axis label rotation angle in degrees (default 0, use 45 for long labels).
      default: 0
    figsize:
      type: array
      items:
        type: number
      description: "Figure size in inches as [width, height]. Overrides width/height for print-accurate sizing."
    tight_layout:
      type: boolean
      description: Apply tight_layout for clean margins (default true).
      default: true
    lang:
      type: string
      description: "Output language for labels: zh | en"
      enum: [zh, en]
      default: "zh"
  required:
    - action
keywords: [chart, plot, figure, visualization, paper, academic, research, matplotlib, bar, ablation, radar, confusion, roc, boxplot, heatmap, timeline, scatter, error, svg, pdf, png, publication, journal, conference, 科研绘图, 论文配图, 图表]
produces:
  stdout: Base64-encoded PNG or SVG markup when no output path given.
side_effects:
  - Writes .svg/.png/.pdf file when output path is given.
  - Uses matplotlib (no network access, pure local rendering).
risk: low
---

# Research Chart v1.0.0

Publication-quality academic chart generator. Produces figures suitable for direct inclusion in LaTeX papers, conference posters, and journal submissions.

## Supported Chart Types

| Action | Description | Typical Use |
|--------|------------|-------------|
| `compare` | Multi-method comparison bar chart | Main results table visualization |
| `ablation` | Ablation study waterfall/bar chart | Show each component's contribution |
| `radar` | Multi-dimensional radar/spider chart | Model capability comparison |
| `confusion` | Confusion matrix heatmap | Classification error analysis |
| `roc` | ROC / PR curves | Binary classifier evaluation |
| `boxplot` | Box-and-whisker plot | Distribution comparison |
| `heatmap` | Generic heatmap | Correlation, attention, feature importance |
| `timeline` | Gantt/timeline chart | Method pipeline, project schedule |
| `scatter_fit` | Scatter plot with fit line | Regression visualization |
| `bar_error` | Bar chart with error bars | Results with confidence intervals |

## Style Presets

| Style | Palette | Font | Best For |
|-------|---------|------|----------|
| `academic` | Grayscale-friendly, high contrast | Serif (Times) | Journal/conference papers |
| `colored` | Colorblind-safe (Okabe-Ito) | Sans-serif (Arial) | Slides, posters |
| `dark` | Light-on-dark | Sans-serif | Dark-mode presentations |

## Data Format by Chart Type

### compare
```json
[
  {"method": "Ours", "metric": 92.3},
  {"method": "Baseline A", "metric": 85.1},
  {"method": "Baseline B", "metric": 78.4}
]
```

### ablation
```json
[
  {"component": "Full Model", "value": 92.3, "delta": 0},
  {"component": "w/o Module A", "value": 88.1, "delta": -4.2},
  {"component": "w/o Module B", "value": 85.7, "delta": -6.6},
  {"component": "w/o Both", "value": 78.4, "delta": -13.9}
]
```

### radar
```json
{
  "dimensions": ["Accuracy", "Speed", "Memory", "Robustness", "Scalability"],
  "models": [
    {"name": "Ours", "values": [92, 85, 78, 90, 82]},
    {"name": "Baseline", "values": [85, 70, 65, 75, 70]}
  ]
}
```

### roc
```json
[
  {"model": "Ours", "fpr": [0, 0.1, 0.2, ...], "tpr": [0, 0.6, 0.85, ...], "auc": 0.92},
  {"model": "Baseline", "fpr": [0, 0.1, 0.2, ...], "tpr": [0, 0.5, 0.75, ...], "auc": 0.85}
]
```

## Output Formats

- **SVG**: Vector format, ideal for LaTeX (no quality loss at any zoom)
- **PDF**: Also vector, directly includable with `\includegraphics`
- **PNG**: Raster format for Word/PowerPoint/websites
- **Base64 PNG**: stdout mode for embedding in web UIs and chat responses
