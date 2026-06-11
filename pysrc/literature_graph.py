"""LiteratureGraph - Citation graph for research skills.

Builds a directed citation graph from paper data and generates
Mermaid-based visualizations for inclusion in research reports.
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional


class LiteratureGraph:
    """A directed citation graph of research papers."""

    def __init__(self, title: str = "Literature Review"):
        self.title = title
        self.nodes: List[Dict[str, Any]] = []
        self.edges: List[Dict[str, Any]] = []
        self.timeline: List[Dict[str, Any]] = []
        self.top_venues: List[tuple] = []

    @classmethod
    def from_papers(cls, papers: List[Dict], title: str = None):
        """Build a LiteratureGraph from a list of paper dicts."""
        graph = cls(title or "Literature Review")
        for p in papers:
            pid = p.get("id") or p.get("doi", "paper_" + str(hash(p.get("title", "")) % 10**8))
            graph.nodes.append({
                "id": pid,
                "title": p.get("title", "Unknown"),
                "year": p.get("year"),
                "venue": p.get("venue", ""),
                "citations": p.get("citations", 0),
                "doi": p.get("doi", ""),
                "authors": p.get("authors", ""),
                "type": p.get("type", "journal"),
                "external": p.get("external", False),
                "concepts": p.get("concepts", []),
            })
            refs = p.get("references", [])
            if isinstance(refs, list):
                for ref in refs:
                    ref_id = ref.get("id") or ref.get("doi", "ref_" + str(hash(str(ref)) % 10**8))
                    ref_title = ref.get("title", "")
                    already_have = any(n["id"] == ref_id for n in graph.nodes)
                    already_have_title = ref_title and any(n.get("title","") == ref_title for n in graph.nodes)
                    if not already_have and not already_have_title:
                        graph.nodes.append({
                            "id": ref_id,
                            "title": ref.get("title", "Unknown"),
                            "year": ref.get("year"),
                            "venue": ref.get("venue", ""),
                            "citations": 0,
                            "doi": ref.get("doi", ""),
                            "authors": ref.get("authors", ""),
                            "type": "reference",
                            "external": True,
                            "concepts": [],
                        })
                    graph.edges.append({"source": pid, "target": ref_id, "type": "cites"})
        # Build timeline
        year_counts = {}
        for n in graph.nodes:
            y = n.get("year")
            if y:
                year_counts[y] = year_counts.get(y, 0) + 1
        graph.timeline = sorted([{"year": y, "count": c} for y, c in year_counts.items()], key=lambda x: x["year"], reverse=True)
        # Build venue summary
        venue_counts = {}
        for n in graph.nodes:
            v = n.get("venue", "") or ""
            if v:
                venue_counts[v] = venue_counts.get(v, 0) + 1
        graph.top_venues = sorted(venue_counts.items(), key=lambda x: x[1], reverse=True)
        return graph

    def _to_mermaid(self) -> str:
        lines = ["graph TB"]
        year_groups = {}
        for n in self.nodes:
            y = n.get("year")
            if y:
                decade = (int(y) // 5) * 5
                year_groups.setdefault(decade, []).append(n)
        for decade, group_nodes in sorted(year_groups.items()):
            gid = "YR" + str(decade)
            lines.append("    subgraph " + gid + "[" + str(decade) + "-" + str(decade+4) + "]")
            for n in group_nodes:
                nid = n["id"][:12]
                label = (n.get("title") or "?")[:35].replace("(","").replace(")","").replace("[","").replace("]","")
                lines.append("        " + nid + "[[" + label + "]]")
            lines.append("    end")
        for n in self.nodes:
            if not n.get("year"):
                nid = n["id"][:12]
                label = (n.get("title") or "?")[:35].replace("(","").replace(")","").replace("[","").replace("]","")
                lines.append("    " + nid + "[[" + label + "]]")
        for e in self.edges[:50]:
            s = e["source"][:12]
            t = e["target"][:12]
            node_ids = [n["id"][:12] for n in self.nodes]
            if s in node_ids and t in node_ids:
                lines.append("    " + s + " --> " + t)
        return chr(10).join(lines)

    def _to_citation_mermaid(self, max_nodes: int = 20) -> str:
        """Generate a Mermaid citation graph with proper labels and structure.
        
        Features:
        - Deduplicated nodes (by ID)
        - Color-coded by role (core paper vs reference)
        - Arrows show citation direction: A --> B means "A cites B"
        - Shows title (truncated) + year + venue on each node
        - TODO marker for incomplete citation info
        """
        lines = ["graph LR"]
        # Deduplicate nodes, keeping first occurrence
        seen_ids = set()
        unique_nodes = []
        for n in self.nodes:
            nid = n.get("id", "")
            if nid and nid not in seen_ids:
                seen_ids.add(nid)
                unique_nodes.append(n)
        
        node_count = len(unique_nodes)
        # Build node labels
        for n in unique_nodes[:max_nodes]:
            nid = n["id"][:20]
            title = (n.get("title") or "?")[:35]
            year = n.get("year") or ""
            venue = (n.get("venue") or "")[:15]
            external = n.get("external", False)
            citations = n.get("citations", 0)
            
            label_parts = [title]
            if year:
                label_parts.append(str(year))
            if venue:
                label_parts.append(venue)
            label = "<br/>".join(label_parts)
            
            if external:
                n_style = "(" + label + ")"  # rounded for external/references
            elif citations > 50:
                n_style = "[" + label + "]"  # stadium for high-citation
            else:
                n_style = "[" + label + "]"  # default
            
            lines.append("    " + nid + n_style)
        
        # Deduplicate edges
        seen_edges = set()
        for e in self.edges[:max_nodes * 2]:
            s = e["source"][:20]
            t = e["target"][:20]
            ekey = s + "|" + t
            if ekey not in seen_edges and s in seen_ids and t in seen_ids:
                seen_edges.add(ekey)
                lines.append("    " + s + " -->|" + (e.get("type", "cites")) + "| " + t)
        
        edges_count = len(seen_edges)
        # Add footer comment
        lines.append("")
        lines.append("    %% Nodes: " + str(node_count) + " (" + str(min(max_nodes, node_count)) + " shown), Edges: " + str(edges_count))
        return chr(10).join(lines)
    
    def _to_cluster_mermaid(self, max_nodes: int = 20) -> str:
        """Generate a Mermaid graph with semantic clustering.
        
        Clusters papers by topic/concept rather than by year.
        If no concepts available, falls back to venue-based clustering.
        """
        lines = ["graph TB"]
        
        # Deduplicate
        seen_ids = set()
        unique_nodes = []
        for n in self.nodes:
            nid = n.get("id", "")
            if nid and nid not in seen_ids:
                seen_ids.add(nid)
                unique_nodes.append(n)
        
        # Group by first concept (or venue as fallback)
        from collections import defaultdict
        clusters = defaultdict(list)
        cluster_order = []
        for n in unique_nodes[:max_nodes]:
            concepts = n.get("concepts", [])
            # Use first concept as cluster key, or venue
            key = "uncategorized"
            if concepts and isinstance(concepts, list) and len(concepts) > 0:
                key = str(concepts[0])[:20]
            elif n.get("venue"):
                key = str(n.get("venue", ""))[:20]
            if key not in cluster_order:
                cluster_order.append(key)
            clusters[key].append(n)
        
        # Render clusters
        for ckey in cluster_order:
            cluster_nodes = clusters[ckey]
            if len(cluster_nodes) >= 2:
                cid = "CL" + str(hash(ckey) % 10**8)[:6]
                lines.append("    subgraph " + cid + "[" + ckey + "]")
                for n in cluster_nodes:
                    nid = n["id"][:20]
                    label = (n.get("title") or "?")[:30]
                    year = n.get("year") or ""
                    lines.append("        " + nid + "[[" + label + " " + str(year) + "]]")
                lines.append("    end")
            else:
                # Single node outside cluster
                for n in cluster_nodes:
                    nid = n["id"][:20]
                    label = (n.get("title") or "?")[:30]
                    year = n.get("year") or ""
                    lines.append("    " + nid + "[[" + label + " " + str(year) + "]]")
        
        # Deduplicate edges
        seen_edges = set()
        for e in self.edges[:max_nodes * 2]:
            s = e["source"][:20]
            t = e["target"][:20]
            ekey = s + "|" + t
            if ekey not in seen_edges and s in seen_ids and t in seen_ids:
                seen_edges.add(ekey)
                lines.append("    " + s + " --> " + t)
        
        return chr(10).join(lines)
    def to_markdown(self, title=None):
        t = title or self.title
        parts = ["## " + t + chr(10)]
        core = [n for n in self.nodes if not n.get("external")]
        refs = [n for n in self.nodes if n.get("external")]
        parts.append("- **Papers**: " + str(len(core)) + " core + " + str(len(refs)) + " referenced")
        parts.append("- **Citations**: " + str(len(self.edges)))
        years = [n.get("year") for n in self.nodes if n.get("year")]
        if years:
            parts.append("- **Year range**: " + str(min(years)) + " - " + str(max(years)))
        parts.append("")
        parts.append("### Citation Graph")
        parts.append("```mermaid")
        parts.append(self._to_citation_mermaid())
        parts.append("```")
        sorted_papers = sorted([n for n in self.nodes if not n.get("external")], key=lambda n: int(n.get("citations") or 0), reverse=True)
        if sorted_papers:
            parts.append("### Top Papers by Citation")
            parts.append("| # | Title | Year | Venue | Citations |")
            parts.append("|---|-------|------|-------|-----------|")
            for idx, p in enumerate(sorted_papers[:10], 1):
                parts.append("| " + str(idx) + " | " + (p.get("title") or "?")[:50] + " | " + str(p.get("year") or "-") + " | " + (p.get("venue") or "-")[:30] + " | " + str(p.get("citations") or 0) + " |")
            parts.append("")
        if self.timeline:
            parts.append("### Timeline")
            parts.append("| Year | Papers |")
            parts.append("|------|--------|")
            for tl in self.timeline[:15]:
                parts.append("| " + str(tl.get("year", "")) + " | " + str(tl.get("count", 0)) + " |")
            parts.append("")
        if self.top_venues:
            parts.append("### Top Venues")
            for venue, count in self.top_venues[:8]:
                parts.append("- **" + venue + "**: " + str(count) + " papers")
            parts.append("")
        return chr(10).join(parts)
