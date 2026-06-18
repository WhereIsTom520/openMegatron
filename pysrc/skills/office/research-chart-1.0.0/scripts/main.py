#!/usr/bin/env python3
"""research-chart v1.0.0 — publication-quality academic chart generator."""

from __future__ import annotations

import base64
import io
import json
import os
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", category=UserWarning, module="matplotlib")

try:
    import matplotlib
    matplotlib.use("Agg")  # non-interactive backend
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mticker
    from matplotlib.patches import FancyBboxPatch
    import numpy as np
except ImportError:
    plt = None
    np = None


# ═══════════════════════════════════════════════════════════════
# Style presets
# ═══════════════════════════════════════════════════════════════

STYLES = {
    "academic": {
        "font_family": "serif",
        "font_size": 12,
        "title_size": 14,
        "label_size": 11,
        "tick_size": 10,
        "annotation_size": 9,
        "bg_color": "#ffffff",
        "grid_color": "#e0e0e0",
        "spine_color": "#333333",
        "text_color": "#222222",
        "palette": ["#333333", "#666666", "#999999", "#bbbbbb", "#dddddd",
                     "#555555", "#888888", "#aaaaaa", "#cccccc", "#444444"],
    },
    "colored": {
        "font_family": "sans-serif",
        "font_size": 13,
        "title_size": 15,
        "label_size": 12,
        "tick_size": 10,
        "annotation_size": 10,
        "bg_color": "#ffffff",
        "grid_color": "#e8e8e8",
        "spine_color": "#444444",
        "text_color": "#1a1a1a",
        "palette": [
            "#0173B2", "#DE8F05", "#029E73", "#D55E00", "#CC78BC",
            "#CA9161", "#FBAFE4", "#949494", "#ECE133", "#56B4E9",
        ],
    },
    "dark": {
        "font_family": "sans-serif",
        "font_size": 13,
        "title_size": 15,
        "label_size": 12,
        "tick_size": 10,
        "annotation_size": 10,
        "bg_color": "#1e1e2e",
        "grid_color": "#3a3a4a",
        "spine_color": "#888888",
        "text_color": "#e0e0e0",
        "palette": [
            "#89b4fa", "#fab387", "#a6e3a1", "#f38ba8", "#cba6f7",
            "#f9e2af", "#94e2d5", "#f5c2e7", "#bac2de", "#74c7ec",
        ],
    },
}


def _get_style(params: dict) -> dict:
    """Merge user params with style preset."""
    style_name = params.get("style", "academic")
    base = dict(STYLES.get(style_name, STYLES["academic"]))
    if params.get("palette"):
        base["palette"] = params["palette"]
    font_scale = float(params.get("font_scale", 1.0))
    if font_scale != 1.0:
        for key in ("font_size", "title_size", "label_size", "tick_size", "annotation_size"):
            base[key] = max(6, int(base[key] * font_scale))
    return base


def _setup_figure(params: dict, style: dict):
    """Create matplotlib figure with consistent styling."""
    figsize = params.get("figsize")
    if figsize and len(figsize) == 2:
        w, h = float(figsize[0]), float(figsize[1])
    else:
        w = int(params.get("width", 1200)) / int(params.get("dpi", 150))
        h = int(params.get("height", 800)) / int(params.get("dpi", 150))

    fig, ax = plt.subplots(figsize=(w, h), dpi=int(params.get("dpi", 150)))
    fig.patch.set_facecolor(style["bg_color"])
    ax.set_facecolor(style["bg_color"])

    # Fonts
    plt.rcParams["font.family"] = style["font_family"]
    plt.rcParams["font.size"] = style["font_size"]
    plt.rcParams["text.color"] = style["text_color"]
    plt.rcParams["axes.labelcolor"] = style["text_color"]
    plt.rcParams["xtick.color"] = style["text_color"]
    plt.rcParams["ytick.color"] = style["text_color"]

    # Spines
    for spine in ax.spines.values():
        spine.set_color(style["spine_color"])
        spine.set_linewidth(0.8)

    # Grid
    if params.get("grid", True):
        ax.grid(True, linestyle="--", alpha=0.4, color=style["grid_color"], linewidth=0.5)
        ax.set_axisbelow(True)

    return fig, ax


def _finish_figure(fig, ax, params: dict, style: dict) -> str:
    """Apply final touches: title, labels, legend, tight_layout, save/encode."""
    title = params.get("title", "")
    xlabel = params.get("xlabel", "")
    ylabel = params.get("ylabel", "")

    if title:
        ax.set_title(title, fontsize=style["title_size"], fontweight="bold", pad=12)
    if xlabel:
        ax.set_xlabel(xlabel, fontsize=style["label_size"])
    if ylabel:
        ax.set_ylabel(ylabel, fontsize=style["label_size"])

    # X-axis label rotation
    rotation = int(params.get("x_rotation", 0))
    if rotation:
        for label in ax.get_xticklabels():
            label.set_rotation(rotation)
            label.set_ha("right" if rotation > 0 else "center")

    # Legend
    if params.get("legend", True) and ax.get_legend_handles_labels()[0]:
        ax.legend(
            fontsize=style["tick_size"],
            frameon=True,
            fancybox=True,
            framealpha=0.9,
            edgecolor=style["grid_color"],
        )

    # Tight layout
    if params.get("tight_layout", True):
        fig.tight_layout(pad=1.5)

    output_path = params.get("output", "")
    if output_path:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(str(out), dpi=int(params.get("dpi", 150)),
                    bbox_inches="tight", facecolor=fig.get_facecolor())
        plt.close(fig)
        return str(out)

    # Return base64 PNG for stdout
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=int(params.get("dpi", 150)),
                bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("ascii")


def _value_label(ax, rects, fmt: str = ".1f", style: dict = None, horizontal: bool = False):
    """Add value labels above/beside bars."""
    s = style or STYLES["academic"]
    for rect in rects:
        if horizontal:
            width = rect.get_width()
            ax.text(width + 0.3, rect.get_y() + rect.get_height() / 2,
                    f"{width:{fmt}}", ha="left", va="center",
                    fontsize=s["annotation_size"], color=s["text_color"])
        else:
            height = rect.get_height()
            ax.text(rect.get_x() + rect.get_width() / 2, height + 0.3,
                    f"{height:{fmt}}", ha="center", va="bottom",
                    fontsize=s["annotation_size"], color=s["text_color"])


# ═══════════════════════════════════════════════════════════════
# Chart type implementations
# ═══════════════════════════════════════════════════════════════

def _chart_compare(params: dict, style: dict, fig, ax) -> str:
    """Multi-method comparison bar chart."""
    data = params.get("data", [])
    if not data:
        raise ValueError("data is required for compare chart")

    methods = [d.get("method", d.get("name", f"Method {i}")) for i, d in enumerate(data)]
    metrics_key = next((k for k in data[0] if k not in ("method", "name")), "metric")
    values = [float(d.get(metrics_key, 0)) for d in data]

    # Sort if requested
    if params.get("sort"):
        pairs = sorted(zip(values, methods), reverse=True)
        values, methods = zip(*pairs) if pairs else ([], [])
        values, methods = list(values), list(methods)

    colors = style["palette"][:len(methods)]
    horizontal = params.get("horizontal", False)

    if horizontal:
        bars = ax.barh(methods, values, color=colors, edgecolor="white", linewidth=0.5, height=0.6)
    else:
        x = np.arange(len(methods))
        bars = ax.bar(x, values, color=colors, edgecolor="white", linewidth=0.5, width=0.6)
        ax.set_xticks(x)
        ax.set_xticklabels(methods, fontsize=style["tick_size"])

    if params.get("annotate", True):
        _value_label(ax, bars, ".2f" if max(values) < 10 else ".1f", style, horizontal)

    # Highlight best
    best_idx = values.index(max(values))
    bars[best_idx].set_edgecolor("#222222")
    bars[best_idx].set_linewidth(1.5)
    # Add a subtle star pattern by hatching
    bars[best_idx].set_hatch("//")

    # Error bars
    error = params.get("error_bars")
    if error and len(error) == len(values):
        err = [float(e) for e in error]
        if horizontal:
            ax.errorbar(values, methods, xerr=err, fmt="none",
                        ecolor=style["spine_color"], capsize=3, linewidth=0.8)
        else:
            ax.errorbar(x, values, yerr=err, fmt="none",
                        ecolor=style["spine_color"], capsize=3, linewidth=0.8)

    return _finish_figure(fig, ax, params, style)


def _chart_ablation(params: dict, style: dict, fig, ax) -> str:
    """Ablation study chart — shows contribution delta of each component."""
    data = params.get("data", [])
    if not data:
        raise ValueError("data is required for ablation chart")

    components = [d.get("component", d.get("name", f"C{i}")) for i, d in enumerate(data)]
    values = [float(d.get("value", d.get("metric", 0))) for d in data]
    deltas = [float(d.get("delta", d.get("diff", 0))) for d in data]

    horizontal = params.get("horizontal", False)
    colors = []
    for v in values:
        if v == max(values):
            colors.append(style["palette"][0])  # best = primary
        else:
            colors.append(style["palette"][2] if len(style["palette"]) > 2 else "#999999")

    if horizontal:
        bars = ax.barh(components, values, color=colors, edgecolor="white", linewidth=0.5, height=0.6)
    else:
        x = np.arange(len(components))
        bars = ax.bar(x, values, color=colors, edgecolor="white", linewidth=0.5, width=0.6)
        ax.set_xticks(x)
        ax.set_xticklabels(components, fontsize=style["tick_size"])

    # Annotate with delta values
    if params.get("annotate", True):
        for i, (bar, delta) in enumerate(zip(bars, deltas)):
            if horizontal:
                w = bar.get_width()
                delta_text = f" ({delta:+.1f})" if delta != 0 else ""
                ax.text(w + 0.3, bar.get_y() + bar.get_height() / 2,
                        f"{values[i]:.1f}{delta_text}",
                        ha="left", va="center",
                        fontsize=style["annotation_size"], color=style["text_color"])
            else:
                h = bar.get_height()
                delta_text = f"\n({delta:+.1f})" if delta != 0 else ""
                ax.text(bar.get_x() + bar.get_width() / 2, h + 0.3,
                        f"{values[i]:.1f}{delta_text}",
                        ha="center", va="bottom",
                        fontsize=style["annotation_size"], color=style["text_color"])

    return _finish_figure(fig, ax, params, style)


def _chart_radar(params: dict, style: dict, fig, ax) -> str:
    """Radar/spider chart for multi-dimensional comparison."""
    data = params.get("data", {})
    if not data:
        raise ValueError("data is required for radar chart")

    dimensions = data.get("dimensions", [])
    models = data.get("models", [])

    if not dimensions or not models:
        raise ValueError("radar chart requires 'dimensions' and 'models' in data")

    N = len(dimensions)
    angles = np.linspace(0, 2 * np.pi, N, endpoint=False).tolist()
    angles += angles[:1]  # close the circle

    # Create polar subplot
    plt.close(fig)
    fig, ax = plt.subplots(figsize=(8, 8), subplot_kw=dict(polar=True),
                           dpi=int(params.get("dpi", 150)))
    fig.patch.set_facecolor(style["bg_color"])
    ax.set_facecolor(style["bg_color"])

    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(dimensions, fontsize=style["label_size"])
    ax.set_ylim(0, 100)
    ax.set_yticks([20, 40, 60, 80, 100])
    ax.set_yticklabels(["20", "40", "60", "80", "100"], fontsize=style["tick_size"], color="#999999")
    ax.grid(True, linestyle="--", alpha=0.3, color=style["grid_color"])

    for i, model in enumerate(models):
        values = model.get("values", [])
        values = values + values[:1]  # close
        color = style["palette"][i % len(style["palette"])]
        ax.fill(angles, values, alpha=0.1, color=color)
        ax.plot(angles, values, "o-", linewidth=2, color=color,
                label=model.get("name", f"Model {i}"), markersize=4)

    ax.legend(fontsize=style["tick_size"], loc="upper right", bbox_to_anchor=(1.3, 1.1))
    ax.set_title(params.get("title", ""), fontsize=style["title_size"],
                 fontweight="bold", pad=20)

    return _finish_figure(fig, ax, params, style)


def _chart_confusion(params: dict, style: dict, fig, ax) -> str:
    """Confusion matrix heatmap."""
    data = params.get("data", [])
    if not data:
        raise ValueError("data is required for confusion chart")

    # data can be [[tp, fp], [fn, tn]] or list of lists
    if isinstance(data, list) and all(isinstance(r, list) for r in data):
        matrix = np.array(data)
    elif isinstance(data, dict) and "matrix" in data:
        matrix = np.array(data["matrix"])
    else:
        raise ValueError("confusion data must be 2D array or {matrix: [[...]]}")

    labels = params.get("labels") or data.get("labels") if isinstance(data, dict) else None
    if not labels:
        labels = [f"Class {i}" for i in range(len(matrix))]

    im = ax.imshow(matrix, cmap="Blues", aspect="auto")

    # Annotate each cell
    for i in range(len(matrix)):
        for j in range(len(matrix[0])):
            val = matrix[i][j]
            text_color = "white" if val > matrix.max() / 2 else style["text_color"]
            ax.text(j, i, str(int(val)) if val == int(val) else f"{val:.1f}",
                    ha="center", va="center", fontsize=style["annotation_size"] + 2,
                    fontweight="bold", color=text_color)

    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, fontsize=style["tick_size"])
    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels, fontsize=style["tick_size"])

    # Colorbar
    cbar = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    cbar.ax.tick_params(labelsize=style["tick_size"])

    return _finish_figure(fig, ax, params, style)


def _chart_roc(params: dict, style: dict, fig, ax) -> str:
    """ROC / PR curve."""
    data = params.get("data", [])
    if not data:
        raise ValueError("data is required for roc chart")

    for i, model_data in enumerate(data):
        fpr = model_data.get("fpr", [])
        tpr = model_data.get("tpr", [])
        auc = model_data.get("auc", None)
        name = model_data.get("model", model_data.get("name", f"Model {i}"))
        color = style["palette"][i % len(style["palette"])]

        label = name
        if auc is not None:
            label = f"{name} (AUC={auc:.3f})"

        ax.plot(fpr, tpr, linewidth=2, color=color, label=label)

    # Diagonal reference line
    ax.plot([0, 1], [0, 1], "k--", linewidth=0.8, alpha=0.4, label="Random")

    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1.05)
    ax.set_xlabel(params.get("xlabel", "False Positive Rate"), fontsize=style["label_size"])
    ax.set_ylabel(params.get("ylabel", "True Positive Rate"), fontsize=style["label_size"])
    ax.set_aspect("equal")

    return _finish_figure(fig, ax, params, style)


def _chart_boxplot(params: dict, style: dict, fig, ax) -> str:
    """Box plot for distribution comparison."""
    data = params.get("data", [])
    if not data:
        raise ValueError("data is required for boxplot chart")

    # data: [{"name": "A", "values": [1,2,3,...]}, ...]
    names = [d.get("name", d.get("method", f"Group {i}")) for i, d in enumerate(data)]
    values_list = [d.get("values", d.get("data", [])) for d in data]

    bp = ax.boxplot(values_list, labels=names, patch_artist=True,
                    widths=0.5, showfliers=True, showmeans=True,
                    meanprops=dict(marker="D", markerfacecolor="red", markersize=5))

    for i, patch in enumerate(bp["boxes"]):
        patch.set_facecolor(style["palette"][i % len(style["palette"])])
        patch.set_alpha(0.7)
        patch.set_edgecolor(style["spine_color"])

    for whisker in bp["whiskers"]:
        whisker.set_color(style["spine_color"])
    for cap in bp["caps"]:
        cap.set_color(style["spine_color"])
    for median in bp["medians"]:
        median.set_color(style["spine_color"])
        median.set_linewidth(1.5)

    return _finish_figure(fig, ax, params, style)


def _chart_heatmap(params: dict, style: dict, fig, ax) -> str:
    """Generic heatmap (correlation, attention, etc.)."""
    data = params.get("data", [])
    if not data:
        raise ValueError("data is required for heatmap chart")

    if isinstance(data, list) and all(isinstance(r, list) for r in data):
        matrix = np.array(data)
    elif isinstance(data, dict) and "matrix" in data:
        matrix = np.array(data["matrix"])
    else:
        raise ValueError("heatmap data must be 2D array or {matrix: [[...]]}")

    labels = params.get("labels")
    if not labels and isinstance(data, dict):
        labels = data.get("labels")

    im = ax.imshow(matrix, cmap="RdBu_r" if matrix.min() < 0 < matrix.max() else "YlOrRd",
                   aspect="auto", vmin=matrix.min(), vmax=matrix.max())

    # Annotate
    for i in range(len(matrix)):
        for j in range(len(matrix[0])):
            val = matrix[i][j]
            ax.text(j, i, f"{val:.2f}", ha="center", va="center",
                    fontsize=style["annotation_size"],
                    color="white" if abs(val) > (abs(matrix.max()) + abs(matrix.min())) / 2
                    else style["text_color"])

    if labels:
        ax.set_xticks(range(len(labels)))
        ax.set_xticklabels(labels, fontsize=style["tick_size"], rotation=45, ha="right")
        ax.set_yticks(range(len(labels)))
        ax.set_yticklabels(labels, fontsize=style["tick_size"])

    cbar = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    cbar.ax.tick_params(labelsize=style["tick_size"])

    return _finish_figure(fig, ax, params, style)


def _chart_timeline(params: dict, style: dict, fig, ax) -> str:
    """Gantt/timeline chart."""
    data = params.get("data", [])
    if not data:
        raise ValueError("data is required for timeline chart")

    # data: [{"task": "Data Prep", "start": 0, "duration": 3, "color": "#xxx"}, ...]
    tasks = []
    for i, d in enumerate(data):
        tasks.append({
            "name": d.get("task", d.get("name", f"Task {i}")),
            "start": float(d.get("start", 0)),
            "duration": float(d.get("duration", d.get("end", 1)) - float(d.get("start", 0)))
            if "end" in d else float(d.get("duration", 1)),
            "color": d.get("color", style["palette"][i % len(style["palette"])]),
        })

    y_positions = range(len(tasks))
    for i, task in enumerate(tasks):
        ax.barh(i, task["duration"], left=task["start"], height=0.5,
                color=task["color"], edgecolor="white", linewidth=0.5, alpha=0.85)
        # Task label
        ax.text(task["start"] + task["duration"] / 2, i, task["name"],
                ha="center", va="center", fontsize=style["annotation_size"],
                color="white" if task["color"] not in ("#dddddd", "#cccccc", "#eeeeee")
                else style["text_color"])

    ax.set_yticks(list(y_positions))
    ax.set_yticklabels([t["name"] for t in tasks], fontsize=style["tick_size"])
    ax.set_xlabel(params.get("xlabel", "Time / Steps"), fontsize=style["label_size"])
    ax.invert_yaxis()

    return _finish_figure(fig, ax, params, style)


def _chart_scatter_fit(params: dict, style: dict, fig, ax) -> str:
    """Scatter plot with optional linear fit."""
    data = params.get("data", [])
    if not data:
        raise ValueError("data is required for scatter_fit chart")

    if isinstance(data, list) and all(isinstance(d, dict) for d in data):
        # [{"x": 1, "y": 2}, ...]
        xs = [float(d.get("x", d.get("x_value", 0))) for d in data]
        ys = [float(d.get("y", d.get("y_value", 0))) for d in data]
    elif isinstance(data, dict) and "x" in data and "y" in data:
        xs = list(data["x"])
        ys = list(data["y"])
    else:
        raise ValueError("scatter_fit data must be [{x, y}, ...] or {x: [...], y: [...]}")

    color = style["palette"][0]
    ax.scatter(xs, ys, c=color, alpha=0.6, s=40, edgecolors="white", linewidth=0.5,
               zorder=3)

    # Linear fit
    if params.get("fit", True):
        from numpy.polynomial.polynomial import polyfit
        coeffs = polyfit(xs, ys, 1)
        x_line = np.linspace(min(xs), max(xs), 100)
        y_line = coeffs[0] + coeffs[1] * x_line
        ax.plot(x_line, y_line, "--", color=style["palette"][1], linewidth=1.5,
                label=f"y={coeffs[0]:.2f}+{coeffs[1]:.2f}x", zorder=2)

        # R²
        y_pred = [coeffs[0] + coeffs[1] * x for x in xs]
        ss_res = sum((y - yp) ** 2 for y, yp in zip(ys, y_pred))
        ss_tot = sum((y - np.mean(ys)) ** 2 for y in ys)
        r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0
        ax.text(0.05, 0.95, f"$R^2$ = {r2:.3f}", transform=ax.transAxes,
                fontsize=style["annotation_size"], color=style["text_color"],
                va="top", bbox=dict(boxstyle="round,pad=0.3", facecolor=style["bg_color"],
                                   edgecolor=style["grid_color"], alpha=0.8))

    return _finish_figure(fig, ax, params, style)


def _chart_bar_error(params: dict, style: dict, fig, ax) -> str:
    """Bar chart with error bars."""
    data = params.get("data", [])
    if not data:
        raise ValueError("data is required for bar_error chart")

    methods = [d.get("method", d.get("name", f"Method {i}")) for i, d in enumerate(data)]
    values = [float(d.get("value", d.get("metric", 0))) for d in data]
    errors = [float(d.get("error", d.get("std", d.get("err", 0)))) for d in data]

    horizontal = params.get("horizontal", False)
    colors = style["palette"][:len(methods)]

    if horizontal:
        bars = ax.barh(methods, values, xerr=errors, color=colors,
                       edgecolor="white", linewidth=0.5, height=0.6,
                       capsize=4, error_kw={"linewidth": 1.0, "ecolor": style["spine_color"]})
    else:
        x = np.arange(len(methods))
        bars = ax.bar(x, values, yerr=errors, color=colors,
                      edgecolor="white", linewidth=0.5, width=0.6,
                      capsize=4, error_kw={"linewidth": 1.0, "ecolor": style["spine_color"]})
        ax.set_xticks(x)
        ax.set_xticklabels(methods, fontsize=style["tick_size"])

    if params.get("annotate", True):
        _value_label(ax, bars, ".2f", style, horizontal)

    return _finish_figure(fig, ax, params, style)


# ═══════════════════════════════════════════════════════════════
# Dispatch
# ═══════════════════════════════════════════════════════════════

CHART_DISPATCH = {
    "compare": _chart_compare,
    "ablation": _chart_ablation,
    "radar": _chart_radar,
    "confusion": _chart_confusion,
    "roc": _chart_roc,
    "boxplot": _chart_boxplot,
    "heatmap": _chart_heatmap,
    "timeline": _chart_timeline,
    "scatter_fit": _chart_scatter_fit,
    "bar_error": _chart_bar_error,
}


# ═══════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════

def main():
    if plt is None:
        print(json.dumps({
            "status": "error",
            "message": "matplotlib is required. Install: pip install matplotlib numpy",
            "completed": True,
        }, ensure_ascii=False))
        raise SystemExit(1)

    raw = sys.argv[1] if len(sys.argv) > 1 else "{}"
    try:
        params = json.loads(raw)
    except json.JSONDecodeError:
        print(json.dumps({"status": "error", "message": "Invalid JSON argument", "completed": True},
                         ensure_ascii=False))
        raise SystemExit(1)

    if not isinstance(params, dict):
        params = {}

    action = params.get("action", "compare")
    if action not in CHART_DISPATCH:
        print(json.dumps({
            "status": "error",
            "message": f"Unknown action: {action}. Supported: {list(CHART_DISPATCH.keys())}",
            "completed": True,
        }, ensure_ascii=False))
        raise SystemExit(1)

    # Load data from file if path given
    if params.get("path") and not params.get("data"):
        path = Path(params["path"])
        if path.exists():
            try:
                if path.suffix == ".csv":
                    import csv
                    with open(path, encoding="utf-8") as f:
                        params["data"] = list(csv.DictReader(f))
                elif path.suffix == ".json":
                    params["data"] = json.loads(path.read_text(encoding="utf-8"))
            except Exception as e:
                print(json.dumps({
                    "status": "error",
                    "message": f"Failed to read data file: {e}",
                    "completed": True,
                }, ensure_ascii=False))
                raise SystemExit(1)

    try:
        style = _get_style(params)
        fig, ax = _setup_figure(params, style)
        result = CHART_DISPATCH[action](params, style, fig, ax)

        output = {
            "status": "success",
            "completed": True,
            "action": action,
            "style": params.get("style", "academic"),
        }

        if params.get("output"):
            output["output_file"] = result
        else:
            output["image_base64"] = result
            output["mime_type"] = "image/png"

        print(json.dumps(output, ensure_ascii=False))

    except Exception as e:
        print(json.dumps({
            "status": "error",
            "message": str(e),
            "completed": True,
        }, ensure_ascii=False))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
