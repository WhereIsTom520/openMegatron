"""Evolution Visualizer: Interactive visualization for the ontology evolution system.

Three levels of visualization:

1. MACRO: Evolution Dashboard
   - 7-level evolution stack status for all categories
   - Pareto frontier 2D projection (scatter plot matrix)
   - Causal effect forest plot

2. MESO: Ontology Hypergraph
   - Force-directed graph of all evolution entities
   - Nodes: categories, promotions, demotions, causal_insights, tradeoffs
   - Edges: produces, has_causal_insight, justifies_adjustment, correlates_with

3. MICRO: Counterfactual Comparison
   - Side-by-side comparison of treated vs synthetic control
   - ATT (Average Treatment Effect) with confidence intervals
   - Trend lines before/after promotion
"""

from __future__ import annotations

import json
import math
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


logger = logging.getLogger(__name__)


@dataclass
class EvolutionVisualization:
    """Container for all visualization data."""
    dashboard: Dict[str, Any]
    hypergraph: Dict[str, Any]
    causal_forest: Dict[str, Any]
    pareto_frontier: Dict[str, Any]


class EvolutionVisualizer:
    """Generates visualization data for the evolution system.

    All data is returned in a format directly usable by:
    - D3.js / React Force Graph 2D
    - Matplotlib / Seaborn
    - Plotly

    Usage:
        vis = EvolutionVisualizer(guided_evolution_instance)
        dashboard = vis.dashboard()
        graph_json = vis.interactive_hypergraph()
        causal_plot = vis.causal_forest_plot()
        pareto_3d = vis.pareto_3d_projection()
    """

    OBJECTIVE_COLORS = {
        "success_rate": "#2ECC71",   # Green
        "efficiency": "#3498DB",     # Blue
        "cost_effective": "#E74C3C", # Red
        "stability": "#9B59B6",      # Purple
    }

    LEVEL_COLORS = {
        "REACTIVE": "#95A5A6",
        "PREDICTIVE": "#3498DB",
        "EXPLORATION": "#9B59B6",
        "AUTONOMOUS": "#F1C40F",
    }

    def __init__(self, guided_evolution):
        self.evo = guided_evolution
        self.tracker = guided_evolution._ontology_tracker

    def dashboard(self) -> Dict[str, Any]:
        """Generate high-level evolution dashboard data.

        Returns structure compatible with any dashboard framework.
        """
        states = self.evo._states
        now = __import__('time').time()

        categories = []
        for cat, state in states.items():
            categories.append({
                "name": cat,
                "level": state.level.name,
                "level_value": state.level.value,
                "level_color": self.LEVEL_COLORS.get(state.level.name, "#95A5A6"),
                "success_rate": round(state.success_rate, 3),
                "total_executions": state.total_executions,
                "demotion_count": state.demotion_count,
                "is_frozen": state.frozen_until > now,
                "frozen_remaining_seconds": max(0, int(state.frozen_until - now)),
                "consecutive_successes": state.consecutive_successes,
                "pre_checks_enabled": state.pre_checks_enabled,
                "exploration_enabled": state.exploration_enabled,
            })

        # Evolution stack height visualization (for a "tower" visualization)
        stack_heights = {lvl: 0 for lvl in self.LEVEL_COLORS.keys()}
        for cat in categories:
            stack_heights[cat["level"]] += 1

        # Multi-objective radar chart data per category
        radar_data = []
        for cat, state in states.items():
            metrics = self.evo._get_current_pareto_metrics(state)
            radar_data.append({
                "category": cat,
                "level": state.level.name,
                "axes": [
                    {"axis": "success_rate", "value": metrics.success_rate, "color": self.OBJECTIVE_COLORS["success_rate"]},
                    {"axis": "efficiency", "value": metrics.efficiency, "color": self.OBJECTIVE_COLORS["efficiency"]},
                    {"axis": "cost_effective", "value": metrics.cost_effective, "color": self.OBJECTIVE_COLORS["cost_effective"]},
                    {"axis": "stability", "value": metrics.stability, "color": self.OBJECTIVE_COLORS["stability"]},
                ]
            })

        return {
            "categories": categories,
            "evolution_stack": stack_heights,
            "radar_chart": radar_data,
            "total_categories": len(categories),
            "average_level": round(sum(c["level_value"] for c in categories) / max(1, len(categories)), 2),
        }

    def interactive_hypergraph(self) -> Dict[str, Any]:
        """Generate force-directed hypergraph visualization data.

        Directly consumable by react-force-graph-2d / d3-force.

        Node types and their visual properties:
        - evolution_category: Large circle, color by current level
        - promotion_event: Small circle, green = success, red = failure
        - demotion_event: Small triangle, always red
        - causal_insight: Diamond, color by ATT sign (green = +, red = -)
        - pareto_tradeoff: Hexagon, opacity = |correlation|
        - objective: Large labeled circle, objective-specific color
        """
        nodes: List[Dict[str, Any]] = []
        links: List[Dict[str, Any]] = []
        node_ids = set()

        def add_node(node_id: str, kind: str, label: str, **kwargs) -> None:
            """Add a node if not already present."""
            if node_id in node_ids:
                return
            node_ids.add(node_id)

            # Visual defaults per node type
            defaults = {
                "evolution_category": {"size": 20, "shape": "circle"},
                "promotion_event": {"size": 8, "shape": "circle"},
                "demotion_event": {"size": 8, "shape": "triangle"},
                "causal_insight": {"size": 12, "shape": "diamond"},
                "pareto_tradeoff": {"size": 10, "shape": "hexagon"},
                "objective": {"size": 15, "shape": "circle"},
                "evolution_threshold": {"size": 10, "shape": "square"},
            }

            vis = defaults.get(kind, {"size": 6, "shape": "circle"})
            vis.update(kwargs.pop("visual", {}))

            nodes.append({
                "id": node_id,
                "kind": kind,
                "label": label,
                "visual": vis,
                **kwargs
            })

        def add_link(source: str, target: str, relation: str, weight: float = 1.0, **kwargs) -> None:
            """Add a directed edge between two nodes."""
            # Color by relation type
            color_map = {
                "produces": "#2ECC71",
                "has_causal_insight": "#9B59B6",
                "justifies_adjustment": "#E74C3C",
                "positively_correlates_with": "#2ECC71",
                "negatively_correlates_with": "#E74C3C",
                "informs_evolution_of": "#3498DB",
            }

            links.append({
                "source": source,
                "target": target,
                "relation": relation,
                "weight": weight,
                "color": color_map.get(relation, "#95A5A6"),
                "width": max(0.5, weight * 3),
                **kwargs
            })

        # 1. Add all evolution category nodes
        for cat, state in self.evo._states.items():
            add_node(
                f"cat:{cat}",
                "evolution_category",
                cat,
                level=state.level.name,
                success_rate=state.success_rate,
                visual={
                    "color": self.LEVEL_COLORS.get(state.level.name, "#95A5A6"),
                    "size": 20 + state.total_executions / 100,  # Larger = more experience
                }
            )

        # 2. Add category-to-category "informs evolution of" edges
        for (source, target, rel), edge in self.tracker._edges.items():
            if rel == "informs_evolution_of":
                add_node(f"cat:{source}", "evolution_category", source)
                add_node(f"cat:{target}", "evolution_category", target)
                add_link(
                    f"cat:{source}", f"cat:{target}", rel,
                    weight=edge.weight,
                    evidence_count=edge.evidence_count,
                )

        # 3. Add objective nodes for Pareto tradeoffs
        for obj in ["success_rate", "efficiency", "cost_effective", "stability"]:
            add_node(
                f"obj:{obj}",
                "objective",
                obj.replace("_", " ").title(),
                visual={
                    "color": self.OBJECTIVE_COLORS[obj],
                    "size": 15,
                }
            )

        # 4. Add tradeoffs from Pareto learner
        tradeoffs = self.evo._pareto_learner.get_tradeoff_analysis()
        for t in tradeoffs:
            tid = f"tradeoff:{t.objective_a}_{t.objective_b}"
            add_node(
                tid,
                "pareto_tradeoff",
                f"Tradeoff\nr={t.correlation:+.2f}",
                correlation=t.correlation,
                strength=t.strength,
                visual={
                    "opacity": min(1.0, abs(t.correlation) + 0.3),
                    "color": "#2ECC71" if t.correlation > 0 else "#E74C3C",
                }
            )
            add_link(f"obj:{t.objective_a}", tid, "participates_in", abs(t.correlation))
            add_link(tid, f"obj:{t.objective_b}", "participates_in", abs(t.correlation))

            # Direct correlation link between objectives
            rel_type = "positively_correlates_with" if t.correlation > 0 else "negatively_correlates_with"
            add_link(f"obj:{t.objective_a}", f"obj:{t.objective_b}", rel_type, abs(t.correlation))

        # 5. Add causal insights
        insights = self.evo._causal_learner.compute_causal_effects()
        for insight in insights:
            iid = f"causal:{insight.category}:{insight.from_level}_{insight.to_level}"
            add_node(
                iid,
                "causal_insight",
                f"ATT={insight.att:+.2f}",
                att=insight.att,
                confidence=insight.confidence,
                recommendation=insight.recommendation,
                visual={
                    "color": "#2ECC71" if insight.att > 0 else "#E74C3C",
                    "size": 8 + abs(insight.att) * 20,  # Larger = bigger effect
                }
            )
            # Link to category
            add_link(f"cat:{insight.category}", iid, "has_causal_insight", insight.confidence)

        return {
            "nodes": nodes,
            "links": links,
            "metadata": {
                "total_nodes": len(nodes),
                "total_links": len(links),
                "node_types": list(set(n["kind"] for n in nodes)),
            }
        }

    def causal_forest_plot(self) -> Dict[str, Any]:
        """Generate data for a forest plot of causal treatment effects.

        Forest plot = one row per promotion, shows ATT with 95% CI
        Classic medical statistics visualization for treatment effects.
        """
        observations = self.evo._causal_learner._observations

        rows = []
        for obs in observations:
            if obs.att is None:
                continue

            # Approximate confidence interval (±1.96 * SE)
            # For simplicity we use heuristic SE based on sample size
            se = 0.05 + 0.1 / max(1, math.sqrt(obs.post_executions or 1))
            ci_low = obs.att - 1.96 * se
            ci_high = obs.att + 1.96 * se
            significant = (ci_low > 0 and ci_high > 0) or (ci_low < 0 and ci_high < 0)

            rows.append({
                "category": obs.category,
                "transition": f"{obs.from_level} → {obs.to_level}",
                "att": round(obs.att, 4),
                "ci_low": round(ci_low, 4),
                "ci_high": round(ci_high, 4),
                "significant": significant,
                "was_randomized": getattr(obs, 'was_randomized', False),
                "control_match": getattr(obs, 'synthetic_control_match', None),
                "post_executions": obs.post_executions,
            })

        # Sort by magnitude of effect
        rows.sort(key=lambda r: abs(r["att"]), reverse=True)

        return {
            "rows": rows,
            "summary": {
                "total_analyzed": len(rows),
                "significant_count": sum(1 for r in rows if r["significant"]),
                "positive_count": sum(1 for r in rows if r["att"] > 0),
                "negative_count": sum(1 for r in rows if r["att"] < 0),
                "mean_att": round(sum(r["att"] for r in rows) / max(1, len(rows)), 4),
            },
            "visualization_hint": {
                "x_axis": "Average Treatment Effect (ATT)",
                "zero_line": "No effect",
                "positive_color": self.OBJECTIVE_COLORS["success_rate"],
                "negative_color": self.OBJECTIVE_COLORS["cost_effective"],
            }
        }

    def pareto_3d_projection(self) -> Dict[str, Any]:
        """3D projection of the Pareto frontier (4D → 3D via PCA-like projection).

        4 objectives → project to 3D using first 3 principal components
        (simplified: we use direct 3-object subset + color for 4th)
        """
        frontier = self.evo._pareto_learner.get_pareto_frontier()
        points = []

        for point_id, metrics in frontier:
            category, level = point_id.split(":")
            points.append({
                "id": point_id,
                "category": category,
                "level": level,
                # x = success, y = efficiency, z = cost_effective
                "x": metrics.success_rate,
                "y": metrics.efficiency,
                "z": metrics.cost_effective,
                # color = stability (4th dimension)
                "color_value": metrics.stability,
                "metrics": {
                    "success_rate": metrics.success_rate,
                    "efficiency": metrics.efficiency,
                    "cost_effective": metrics.cost_effective,
                    "stability": metrics.stability,
                },
                "level_color": self.LEVEL_COLORS.get(level, "#95A5A6"),
            })

        # Compute ideal point (utopia) and nadir point
        if points:
            ideal = {
                "x": max(p["x"] for p in points),
                "y": max(p["y"] for p in points),
                "z": max(p["z"] for p in points),
            }
            nadir = {
                "x": min(p["x"] for p in points),
                "y": min(p["y"] for p in points),
                "z": min(p["z"] for p in points),
            }
        else:
            ideal = nadir = None

        return {
            "points": points,
            "ideal_point": ideal,
            "nadir_point": nadir,
            "frontier_size": len(points),
            "axes": {
                "x": {"name": "Success Rate", "color": self.OBJECTIVE_COLORS["success_rate"]},
                "y": {"name": "Efficiency", "color": self.OBJECTIVE_COLORS["efficiency"]},
                "z": {"name": "Cost Effectiveness", "color": self.OBJECTIVE_COLORS["cost_effective"]},
                "color": {"name": "Stability", "color": self.OBJECTIVE_COLORS["stability"]},
            },
            "convex_hull_hint": "Draw the convex hull of points to show the efficient frontier",
        }

    def export_all(self) -> EvolutionVisualization:
        """Export all visualizations in one call."""
        return EvolutionVisualization(
            dashboard=self.dashboard(),
            hypergraph=self.interactive_hypergraph(),
            causal_forest=self.causal_forest_plot(),
            pareto_frontier=self.pareto_3d_projection(),
        )

    def to_json(self, pretty: bool = True) -> str:
        """Export all visualization data to JSON for frontend consumption."""
        data = {
            "dashboard": self.dashboard(),
            "hypergraph": self.interactive_hypergraph(),
            "causal_forest": self.causal_forest_plot(),
            "pareto_frontier": self.pareto_3d_projection(),
        }
        indent = 2 if pretty else None
        return json.dumps(data, indent=indent)
