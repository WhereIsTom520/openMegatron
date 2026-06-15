from __future__ import annotations

import csv
import json
import math
import sys
from pathlib import Path


def parse_cli_args() -> dict:
    if len(sys.argv) <= 1:
        return {}
    raw = sys.argv[1]
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    return {}


def _load_data(args: dict) -> list[dict]:
    if args.get("data"):
        return args["data"]
    path = args.get("path", "")
    if not path:
        return []
    p = Path(str(path)).expanduser()
    if not p.exists():
        return []
    raw = p.read_text(encoding="utf-8", errors="replace")
    if p.suffix.lower() == ".json":
        data = json.loads(raw)
        return data if isinstance(data, list) else [data]
    # CSV fallback
    reader = csv.DictReader(raw.splitlines())
    return list(reader)


def _num(val) -> float:
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


def _hex_color(name: str) -> str:
    named = {
        "red": "#ef4444", "blue": "#2563eb", "green": "#22c55e",
        "yellow": "#eab308", "purple": "#a855f7", "orange": "#f97316",
        "pink": "#ec4899", "teal": "#14b8a6", "gray": "#6b7280",
        "slate": "#64748b", "indigo": "#6366f1", "cyan": "#06b6d4",
    }
    return named.get(name.lower(), name)


def _esc(val: str) -> str:
    return str(val).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _palette(colors: list | None) -> list[str]:
    defaults = ["#2563eb", "#f97316", "#22c55e", "#eab308", "#a855f7",
                "#ec4899", "#14b8a6", "#6366f1", "#ef4444", "#06b6d4"]
    if colors:
        return [_hex_color(c) for c in colors] + defaults
    return defaults


def build_bar_svg(data: list[dict], x_key: str, y_key: str, title: str,
                  xlabel: str, ylabel: str, width: int, height: int,
                  color: str, horizontal: bool, stacked: bool, group_by: str | None,
                  colors: list | None) -> str:
    margin = {"t": 50, "r": 30, "b": 60, "l": 70}
    pw, ph = width, height
    iw, ih = pw - margin["l"] - margin["r"], ph - margin["t"] - margin["b"]

    raw = [_num(r.get(y_key, 0)) for r in data]
    cats = [str(r.get(x_key, "")) for r in data]
    y_max = max(raw) if raw else 1
    y_max = math.ceil(y_max * 1.15)

    cols = _palette(colors)
    base_color = _hex_color(color)

    lines = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {pw} {ph}" width="{pw}" height="{ph}">',
             f'<rect width="{pw}" height="{ph}" fill="#ffffff"/>']

    # Title
    if title:
        lines.append(f'<text x="{pw/2}" y="30" text-anchor="middle" font-size="18" font-weight="600" fill="#111">{_esc(title)}</text>')

    # Y-axis gridlines
    n_ticks = max(4, min(10, int(y_max)))
    for i in range(n_ticks + 1):
        val = y_max * i / n_ticks
        y = ph - margin["b"] - ih * i / n_ticks
        lines.append(f'<line x1="{margin["l"]}" y1="{y}" x2="{pw-margin["r"]}" y2="{y}" stroke="#e5e7eb" stroke-width="1"/>')
        lines.append(f'<text x="{margin["l"]-8}" y="{y+4}" text-anchor="end" font-size="12" fill="#6b7280">{int(val)}</text>')

    # Y-axis label
    if ylabel:
        lines.append(f'<text transform="rotate(-90,20,{ph/2})" x="-{ph/2}" y="20" text-anchor="middle" font-size="13" fill="#6b7280">{_esc(ylabel)}</text>')

    # X-axis label
    if xlabel:
        lines.append(f'<text x="{pw/2}" y="{ph-8}" text-anchor="middle" font-size="13" fill="#6b7280">{_esc(xlabel)}</text>')

    # Bars
    n = len(raw)
    bar_w = max(8, min(60, iw / n * 0.7)) if n else 20
    gap = (iw - bar_w * n) / (n + 1) if n > 1 else iw / 3

    for i, (cat, val) in enumerate(zip(cats, raw)):
        x = margin["l"] + gap + i * (bar_w + gap)
        h = ih * val / y_max if y_max > 0 else 0
        y = ph - margin["b"] - h
        bar_color = cols[i % len(cols)]
        lines.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{h:.1f}" fill="{bar_color}" rx="3"/>')
        # X-axis label
        lines.append(f'<text x="{x+bar_w/2}" y="{ph-margin["b"]+16}" text-anchor="end" font-size="11" fill="#374151" transform="rotate(-25,{x+bar_w/2},{ph-margin["b"]})">{_esc(cat[:12])}</text>')
        # Value label
        if h > 20:
            lines.append(f'<text x="{x+bar_w/2}" y="{y-6}" text-anchor="middle" font-size="11" fill="#374151">{int(val)}</text>')

    # Axes
    lines.append(f'<line x1="{margin["l"]}" y1="{ph-margin["b"]}" x2="{pw-margin["r"]}" y2="{ph-margin["b"]}" stroke="#d1d5db" stroke-width="1"/>')
    lines.append(f'<line x1="{margin["l"]}" y1="{margin["t"]}" x2="{margin["l"]}" y2="{ph-margin["b"]}" stroke="#d1d5db" stroke-width="1"/>')

    lines.append("</svg>")
    return "\n".join(lines)


def build_line_svg(data: list[dict], x_key: str, y_key: str, title: str,
                   xlabel: str, ylabel: str, width: int, height: int,
                   color: str, smooth: bool, colors: list | None) -> str:
    margin = {"t": 50, "r": 30, "b": 60, "l": 70}
    pw, ph = width, height
    iw, ih = pw - margin["l"] - margin["r"], ph - margin["t"] - margin["b"]

    raw = [_num(r.get(y_key, 0)) for r in data]
    cats = [str(r.get(x_key, "")) for r in data]
    y_max = max(raw) if raw else 1
    y_min = min(raw) if raw else 0
    y_range = max(y_max - y_min, 1)
    y_ceil = math.ceil((y_max + y_range * 0.1) / (10 ** max(0, len(str(int(y_range)))-2))) * (10 ** max(0, len(str(int(y_range)))-2)) if y_range > 1 else y_max * 1.2 or 1

    base_color = _hex_color(color)
    cols = _palette(colors)

    lines = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {pw} {ph}" width="{pw}" height="{ph}">',
             f'<rect width="{pw}" height="{ph}" fill="#ffffff"/>']
    if title:
        lines.append(f'<text x="{pw/2}" y="30" text-anchor="middle" font-size="18" font-weight="600" fill="#111">{_esc(title)}</text>')

    n_ticks = max(4, min(8, int(y_ceil)))
    step = y_ceil / n_ticks if n_ticks > 0 else 1
    for i in range(n_ticks + 1):
        val = step * i
        y = ph - margin["b"] - ih * val / y_ceil
        lines.append(f'<line x1="{margin["l"]}" y1="{y}" x2="{pw-margin["r"]}" y2="{y}" stroke="#e5e7eb" stroke-width="1"/>')
        lines.append(f'<text x="{margin["l"]-8}" y="{y+4}" text-anchor="end" font-size="12" fill="#6b7280">{int(val)}</text>')

    if ylabel:
        lines.append(f'<text transform="rotate(-90,20,{ph/2})" x="-{ph/2}" y="20" text-anchor="middle" font-size="13" fill="#6b7280">{_esc(ylabel)}</text>')
    if xlabel:
        lines.append(f'<text x="{pw/2}" y="{ph-8}" text-anchor="middle" font-size="13" fill="#6b7280">{_esc(xlabel)}</text>')

    # Points and line
    n = len(raw)
    if n > 1:
        points = []
        for i, val in enumerate(raw):
            x = margin["l"] + iw * i / (n - 1)
            y = ph - margin["b"] - ih * (val - y_min) / y_ceil
            points.append((x, y))

        # Line segments
        path_d = " ".join(f'L{x:.1f},{y:.1f}' if i > 0 else f'M{x:.1f},{y:.1f}' for i, (x, y) in enumerate(points))
        if smooth:
            lines.append(f'<path d="{path_d}" fill="none" stroke="{cols[0]}" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>')
        else:
            lines.append(f'<polyline points="{" ".join(f"{x:.1f},{y:.1f}" for x,y in points)}" fill="none" stroke="{cols[0]}" stroke-width="2.5" stroke-linejoin="round"/>')

        # Dots
        for x, y in points:
            lines.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4.5" fill="{cols[0]}" stroke="#fff" stroke-width="1.5"/>')

        # X-axis labels
        for i, cat in enumerate(cats):
            x = margin["l"] + iw * i / (n - 1)
            lines.append(f'<text x="{x}" y="{ph-margin["b"]+16}" text-anchor="end" font-size="11" fill="#374151" transform="rotate(-25,{x},{ph-margin["b"]})">{_esc(cat[:12])}</text>')

    lines.append(f'<line x1="{margin["l"]}" y1="{ph-margin["b"]}" x2="{pw-margin["r"]}" y2="{ph-margin["b"]}" stroke="#d1d5db" stroke-width="1"/>')
    lines.append(f'<line x1="{margin["l"]}" y1="{margin["t"]}" x2="{margin["l"]}" y2="{ph-margin["b"]}" stroke="#d1d5db" stroke-width="1"/>')
    lines.append("</svg>")
    return "\n".join(lines)


def build_pie_svg(data: list[dict], label_key: str, value_key: str, title: str,
                  width: int, height: int, colors: list | None) -> str:
    pw, ph = width, height
    cx, cy = pw // 2, ph // 2
    radius = min(pw, ph) // 3

    labels = [str(r.get(label_key, "")) for r in data]
    values = [_num(r.get(value_key, 0)) for r in data]
    total = sum(values) or 1

    cols = _palette(colors)

    lines = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {pw} {ph}" width="{pw}" height="{ph}">',
             f'<rect width="{pw}" height="{ph}" fill="#ffffff"/>']
    if title:
        lines.append(f'<text x="{pw/2}" y="30" text-anchor="middle" font-size="18" font-weight="600" fill="#111">{_esc(title)}</text>')

    # Pie slices
    angle = -90
    for i, val in enumerate(values):
        frac = val / total
        a2 = angle + 360 * frac
        start_rad = math.radians(angle)
        end_rad = math.radians(a2)
        lf = 1 if a2 - angle > 180 else 0
        x1 = cx + radius * math.cos(start_rad)
        y1 = cy + radius * math.sin(start_rad)
        x2 = cx + radius * math.cos(end_rad)
        y2 = cy + radius * math.sin(end_rad)
        color = cols[i % len(cols)]
        lines.append(f'<path d="M{cx},{cy} L{x1:.1f},{y1:.1f} A{radius},{radius} 0 {lf},1 {x2:.1f},{y2:.1f} Z" fill="{color}" stroke="#fff" stroke-width="1"/>')
        angle = a2

    # Legend
    leg_x = pw - 160
    leg_y = 50 if title else 40
    for i, (label, val) in enumerate(zip(labels, values)):
        pct = round(val / total * 100, 1)
        y = leg_y + i * 22
        lines.append(f'<rect x="{leg_x}" y="{y}" width="14" height="14" fill="{cols[i % len(cols)]}" rx="2"/>')
        lines.append(f'<text x="{leg_x+20}" y="{y+12}" font-size="12" fill="#374151">{_esc(label)} ({pct}%)</text>')

    lines.append("</svg>")
    return "\n".join(lines)


def build_scatter_svg(data: list[dict], x_key: str, y_key: str, title: str,
                      xlabel: str, ylabel: str, width: int, height: int,
                      color: str, colors: list | None) -> str:
    margin = {"t": 50, "r": 30, "b": 60, "l": 70}
    pw, ph = width, height
    iw, ih = pw - margin["l"] - margin["r"], ph - margin["t"] - margin["b"]

    xs = [_num(r.get(x_key, 0)) for r in data]
    ys = [_num(r.get(y_key, 0)) for r in data]
    x_min, x_max = min(xs) if xs else 0, max(xs) if xs else 1
    y_min, y_max = min(ys) if ys else 0, max(ys) if ys else 1
    x_rng = max(x_max - x_min, 1)
    y_rng = max(y_max - y_min, 1)

    col = _hex_color(color)

    lines = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {pw} {ph}" width="{pw}" height="{ph}">',
             f'<rect width="{pw}" height="{ph}" fill="#ffffff"/>']
    if title:
        lines.append(f'<text x="{pw/2}" y="30" text-anchor="middle" font-size="18" font-weight="600" fill="#111">{_esc(title)}</text>')

    n_ticks = 5
    for i in range(n_ticks + 1):
        yv = y_min + y_rng * i / n_ticks
        y = ph - margin["b"] - ih * i / n_ticks
        xv = x_min + x_rng * i / n_ticks
        x = margin["l"] + iw * i / n_ticks
        lines.append(f'<line x1="{margin["l"]}" y1="{y}" x2="{pw-margin["r"]}" y2="{y}" stroke="#e5e7eb" stroke-width="1"/>')
        lines.append(f'<line x1="{x}" y1="{ph-margin["b"]}" x2="{x}" y2="{margin["t"]}" stroke="#e5e7eb" stroke-width="1"/>')
        lines.append(f'<text x="{margin["l"]-8}" y="{y+4}" text-anchor="end" font-size="11" fill="#6b7280">{yv:.1f}</text>')
        lines.append(f'<text x="{x}" y="{ph-margin["b"]+16}" text-anchor="middle" font-size="11" fill="#6b7280">{xv:.1f}</text>')

    if ylabel:
        lines.append(f'<text transform="rotate(-90,20,{ph/2})" x="-{ph/2}" y="20" text-anchor="middle" font-size="13" fill="#6b7280">{_esc(ylabel)}</text>')
    if xlabel:
        lines.append(f'<text x="{pw/2}" y="{ph-8}" text-anchor="middle" font-size="13" fill="#6b7280">{_esc(xlabel)}</text>')

    for i, (xi, yi) in enumerate(zip(xs, ys)):
        x = margin["l"] + iw * (xi - x_min) / x_rng
        y = ph - margin["b"] - ih * (yi - y_min) / y_rng
        lines.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" fill="{col}" opacity="0.7"/>')

    lines.append("</svg>")
    return "\n".join(lines)


def main() -> int:
    args = parse_cli_args()
    action = args.get("action", "")
    if not action:
        print(json.dumps({"status": "error", "error": "Missing 'action'."}, ensure_ascii=False))
        return 2

    data = _load_data(args)
    if not data:
        print(json.dumps({"status": "error", "error": "No data provided. Use 'data' or 'path'."}, ensure_ascii=False))
        return 2

    x_key = args.get("x", "")
    y_key = args.get("y", "")
    if not x_key and action != "pie":
        x_key = list(data[0].keys())[0] if data else ""
    if not y_key:
        y_key = list(data[0].keys())[1] if data and len(data[0]) > 1 else x_key

    width = int(args.get("width", 800))
    height = int(args.get("height", 500))
    color = args.get("color", "#2563eb")
    colors = args.get("colors")
    title = args.get("title", "")
    xlabel = args.get("xlabel", "")
    ylabel = args.get("ylabel", "")

    svg = ""
    if action == "bar":
        svg = build_bar_svg(data, x_key, y_key, title, xlabel, ylabel,
                            width, height, color, args.get("horizontal", False),
                            args.get("stacked", False), args.get("group_by"), colors)
    elif action == "line":
        svg = build_line_svg(data, x_key, y_key, title, xlabel, ylabel,
                             width, height, color, args.get("smooth", False), colors)
    elif action == "pie":
        svg = build_pie_svg(data, x_key or "label", y_key or "value", title, width, height, colors)
    elif action == "scatter":
        svg = build_scatter_svg(data, x_key, y_key, title, xlabel, ylabel,
                                width, height, color, colors)
    else:
        print(json.dumps({"status": "error", "error": f"Unknown action: {action}"}, ensure_ascii=False))
        return 2

    output = args.get("output", "")
    if output:
        Path(str(output)).expanduser().write_text(svg, encoding="utf-8")
        print(json.dumps({"status": "success", "action": action, "format": "svg",
                          "width": width, "height": height, "points": len(data),
                          "output": str(Path(output).expanduser().resolve())}, ensure_ascii=False, indent=2))
    else:
        # Print SVG directly to stdout as the result
        print(json.dumps({"status": "success", "action": action, "format": "svg",
                          "width": width, "height": height, "points": len(data),
                          "svg": svg}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
