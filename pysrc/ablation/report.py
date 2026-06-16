"""Ablation result visualization — generates tables and charts for paper.

Outputs:
  - Markdown tables for each ablation group
  - LaTeX tables for paper inclusion
  - ASCII bar charts for terminal viewing
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List


def load_results(json_path: str) -> dict:
    """Load ablation results from JSON."""
    with open(json_path, "r", encoding="utf-8") as f:
        return json.load(f)


def format_number(value: float, decimals: int = 3, is_percent: bool = False) -> str:
    """Format a number for display."""
    if is_percent:
        return f"{value * 100:.{decimals-1}f}%"
    if abs(value) < 0.001:
        return f"{value:.6f}"
    if abs(value) < 1:
        return f"{value:.{decimals}f}"
    if abs(value) >= 1000:
        return f"{value:,.0f}"
    return f"{value:.{decimals-1}f}"


def generate_markdown_table(results: dict) -> str:
    """Generate a Markdown table of ablation results."""
    summary = results.get("summary", {})
    baseline = summary.get("baseline", {})
    ablations = summary.get("ablations", {})

    lines = []
    lines.append("# Ablation Study Results\n")

    # Key metrics to show
    metrics = [
        ("retrieval_precision", "Precision@5", False),
        ("retrieval_recall", "Recall@5", False),
        ("retrieval_mrr", "MRR", False),
        ("answer_faithfulness", "Faithfulness", False),
        ("latency_ms", "Latency (ms)", False),
        ("cache_hit_rate", "Cache Hit Rate", True),
        ("token_usage", "Tokens", False),
        ("estimated_cost_usd", "Cost (USD)", False),
        ("task_success", "Success Rate", True),
    ]

    # Header
    lines.append("| Experiment | " + " | ".join(label for _, label, _ in metrics) + " |")
    lines.append("|" + "|".join(" --- " for _ in range(len(metrics) + 1)) + "|")

    # Baseline row
    base_vals = []
    for key, _, is_pct in metrics:
        val = baseline.get(key, 0)
        base_vals.append(format_number(val, is_percent=is_pct))
    lines.append("| **Full System** | " + " | ".join(base_vals) + " |")

    # Ablation rows
    for name, data in ablations.items():
        m = data.get("metrics", {})
        vals = []
        for key, _, is_pct in metrics:
            val = m.get(key, 0)
            vals.append(format_number(val, is_percent=is_pct))
        lines.append(f"| {name} | " + " | ".join(vals) + " |")

    # Delta table
    lines.append("\n## Delta vs Baseline\n")
    lines.append("| Experiment | " + " | ".join(label for _, label, _ in metrics) + " |")
    lines.append("|" + "|".join(" --- " for _ in range(len(metrics) + 1)) + "|")

    for name, data in ablations.items():
        delta = data.get("delta_vs_baseline", {})
        vals = []
        for key, _, _ in metrics:
            d = delta.get(key, 0)
            sign = "+" if d >= 0 else ""
            vals.append(f"{sign}{format_number(d)}")
        lines.append(f"| {name} | " + " | ".join(vals) + " |")

    return "\n".join(lines)


def generate_latex_table(results: dict) -> str:
    """Generate a LaTeX table for paper inclusion."""
    summary = results.get("summary", {})
    baseline = summary.get("baseline", {})
    ablations = summary.get("ablations", {})

    metrics = [
        ("retrieval_precision", "Prec@5"),
        ("retrieval_recall", "Rec@5"),
        ("latency_ms", "Latency"),
        ("estimated_cost_usd", "Cost"),
        ("task_success", "Success"),
    ]

    lines = []
    lines.append(r"\begin{table}[t]")
    lines.append(r"\centering")
    lines.append(r"\caption{Ablation study results.}")
    lines.append(r"\label{tab:ablation}")
    cols = "l" + "c" * len(metrics)
    lines.append(r"\begin{tabular}{" + cols + "}")
    lines.append(r"\toprule")
    lines.append(" & ".join(["Configuration"] + [label for _, label in metrics]) + r" \\")
    lines.append(r"\midrule")

    # Baseline
    base_vals = [format_number(baseline.get(key, 0), decimals=3) for key, _ in metrics]
    lines.append("Full System & " + " & ".join(base_vals) + r" \\")

    lines.append(r"\midrule")
    for name, data in ablations.items():
        m = data.get("metrics", {})
        vals = [format_number(m.get(key, 0), decimals=3) for key, _ in metrics]
        display_name = name.replace("_", r"\_")
        lines.append(f"{display_name} & " + " & ".join(vals) + r" \\")

    lines.append(r"\bottomrule")
    lines.append(r"\end{tabular}")
    lines.append(r"\end{table}")

    return "\n".join(lines)


def generate_ascii_chart(results: dict, metric: str = "retrieval_precision",
                         width: int = 40) -> str:
    """Generate an ASCII bar chart for a metric."""
    summary = results.get("summary", {})
    baseline = summary.get("baseline", {})
    ablations = summary.get("ablations", {})

    baseline_val = baseline.get(metric, 0)
    max_val = max(
        baseline_val,
        *[d.get("metrics", {}).get(metric, 0) for d in ablations.values()],
    )

    lines = []
    lines.append(f"\n{metric} Comparison:")
    lines.append("-" * (width + 30))

    def bar(name, val):
        b_width = int(val / max(max_val, 0.001) * width)
        return f"  {name:<25} {'█' * b_width} {format_number(val)}"

    lines.append(bar("Full System", baseline_val))
    for name, data in ablations.items():
        val = data.get("metrics", {}).get(metric, 0)
        lines.append(bar(name, val))

    return "\n".join(lines)


def generate_full_report(results_path: str, output_dir: str = "outputs/ablation"):
    """Generate all report formats from results JSON."""
    results = load_results(results_path)
    os.makedirs(output_dir, exist_ok=True)

    # Markdown
    md = generate_markdown_table(results)
    md_path = os.path.join(output_dir, "ablation_report.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)
    print(f"Markdown report: {md_path}")

    # LaTeX
    tex = generate_latex_table(results)
    tex_path = os.path.join(output_dir, "ablation_table.tex")
    with open(tex_path, "w", encoding="utf-8") as f:
        f.write(tex)
    print(f"LaTeX table: {tex_path}")

    # ASCII charts
    charts_path = os.path.join(output_dir, "charts.txt")
    with open(charts_path, "w", encoding="utf-8") as f:
        for metric in ["retrieval_precision", "retrieval_recall", "latency_ms", "estimated_cost_usd"]:
            f.write(generate_ascii_chart(results, metric) + "\n\n")
    print(f"ASCII charts: {charts_path}")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Generate ablation reports")
    p.add_argument("results", help="Path to ablation results JSON")
    p.add_argument("--output-dir", "-o", default="outputs/ablation",
                   help="Output directory for reports")
    args = p.parse_args()
    generate_full_report(args.results, args.output_dir)
