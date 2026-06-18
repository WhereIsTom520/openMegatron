"""
LiteratureGraph 2.0 - 增强版文献知识图谱系统

核心改进:
- NetworkX 图数据结构
- PyVis 交互式 HTML 可视化
- PageRank 中心性计算
- Louvain 社区发现
- 共引分析 (Co-citation)
- 文献耦合 (Bibliographic Coupling)
- 引用路径追溯
"""

from __future__ import annotations
import json
import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, DefaultDict
from collections import defaultdict

try:
    import networkx as nx
    HAS_NETWORKX = True
except ImportError:
    HAS_NETWORKX = False

try:
    from pyvis.network import Network
    HAS_PYVIS = True
except ImportError:
    HAS_PYVIS = False


class LiteratureGraphV2:
    """增强版文献知识图谱，基于 NetworkX 实现。"""

    def __init__(self, title: str = "Literature Review"):
        self.title = title
        self.graph = nx.DiGraph() if HAS_NETWORKX else None
        self.nodes: Dict[str, Dict[str, Any]] = {}  # paper_id -> metadata
        self.edges: Dict[Tuple[str, str], Dict[str, Any]] = {}  # (source, target) -> edge_data
        self._communities: Optional[Dict[str, int]] = None
        self._pagerank: Optional[Dict[str, float]] = None
        self._centrality: Optional[Dict[str, Dict[str, float]]] = None

    @classmethod
    def from_papers(cls, papers: List[Dict], title: str = None) -> "LiteratureGraphV2":
        """从论文列表构建知识图谱。"""
        g = cls(title or "Literature Review")

        for p in papers:
            paper_id = g._paper_id(p)
            g._add_paper_node(paper_id, p, is_core=True)

            # 添加引用关系
            refs = p.get("references", [])
            if isinstance(refs, list):
                for ref in refs:
                    ref_id = g._paper_id(ref)
                    g._add_paper_node(ref_id, ref, is_core=False)
                    g._add_citation_edge(paper_id, ref_id)

        g._compute_metrics()
        return g

    def _paper_id(self, paper: Dict) -> str:
        """生成论文的稳定 ID。"""
        doi = (paper.get("doi") or "").strip()
        if doi:
            return f"doi:{doi}"

        oa_id = (paper.get("openalex_id") or paper.get("id") or "").strip()
        if oa_id:
            return f"oa:{oa_id}"

        title = (paper.get("title") or "").strip()
        authors = paper.get("authors", "")
        if isinstance(authors, list):
            authors = ", ".join(str(a) for a in authors[:3])

        h = hashlib.md5(f"{title}|{authors}".encode()).hexdigest()[:12]
        return f"hash:{h}"

    def _add_paper_node(self, node_id: str, paper: Dict, is_core: bool = False):
        """添加论文节点。"""
        if node_id in self.nodes:
            if is_core and not self.nodes[node_id].get("is_core"):
                self.nodes[node_id]["is_core"] = True
            return

        concepts = paper.get("concepts", [])
        if not isinstance(concepts, list):
            concepts = []

        node_data = {
            "id": node_id,
            "title": paper.get("title", "Unknown"),
            "year": paper.get("year"),
            "venue": paper.get("venue", ""),
            "citations": int(paper.get("citations") or 0),
            "doi": paper.get("doi", ""),
            "authors": paper.get("authors", ""),
            "type": paper.get("type", "journal"),
            "is_core": is_core,
            "concepts": concepts,
            "external": paper.get("external", not is_core),
        }

        self.nodes[node_id] = node_data

        if self.graph is not None:
            self.graph.add_node(node_id, **node_data)

    def _add_citation_edge(self, citing_id: str, cited_id: str):
        """添加引用边：citing_id → cited_id（A 引用 B）。"""
        edge_key = (citing_id, cited_id)
        if edge_key in self.edges:
            return

        edge_data = {
            "source": citing_id,
            "target": cited_id,
            "type": "cites",
        }

        self.edges[edge_key] = edge_data

        if self.graph is not None:
            self.graph.add_edge(citing_id, cited_id, **edge_data)

    def _compute_metrics(self):
        """计算所有图指标：PageRank、中心性、社区。"""
        if self.graph is None or len(self.graph) == 0:
            return

        # PageRank (识别核心论文)
        try:
            self._pagerank = nx.pagerank(self.graph, alpha=0.85, max_iter=1000)
        except Exception:
            self._pagerank = {n: 1.0 / len(self.graph) for n in self.graph}

        # 中心性指标
        self._centrality = {}
        try:
            self._centrality["degree"] = nx.degree_centrality(self.graph)
            self._centrality["betweenness"] = nx.betweenness_centrality(self.graph, k=min(50, len(self.graph)))
            self._centrality["closeness"] = nx.closeness_centrality(self.graph)
        except Exception:
            pass

        # Louvain 社区发现（转无向图）
        try:
            undirected = self.graph.to_undirected()
            self._communities = nx.algorithms.community.louvain_communities(undirected, seed=42)
            # 转换为 node -> community_id
            comm_map = {}
            for i, comm in enumerate(self._communities):
                for node in comm:
                    comm_map[node] = i
            self._communities = comm_map
        except Exception:
            self._communities = {n: 0 for n in self.graph}

    # ──────────────────────────────────────────────────────────────
    # 核心论文发现
    # ──────────────────────────────────────────────────────────────

    def get_top_papers(self, top_k: int = 10, by: str = "pagerank") -> List[Dict]:
        """获取排名靠前的论文。

        Args:
            top_k: 返回数量
            by: 排序方式 'pagerank' | 'citations' | 'betweenness' | 'degree'
        """
        scored = []
        for node_id, data in self.nodes.items():
            if by == "pagerank" and self._pagerank:
                score = self._pagerank.get(node_id, 0)
            elif by == "citations":
                score = data.get("citations", 0)
            elif by == "betweenness" and self._centrality:
                score = self._centrality.get("betweenness", {}).get(node_id, 0)
            elif by == "degree" and self._centrality:
                score = self._centrality.get("degree", {}).get(node_id, 0)
            else:
                score = data.get("citations", 0)

            scored.append((score, node_id, data))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            {"rank": i + 1, "score": float(s), **d}
            for i, (s, _, d) in enumerate(scored[:top_k])
        ]

    # ──────────────────────────────────────────────────────────────
    # 社区发现
    # ──────────────────────────────────────────────────────────────

    def get_communities(self) -> List[Dict]:
        """获取研究社区摘要。"""
        if not self._communities:
            return []

        comm_papers: DefaultDict[int, List[str]] = defaultdict(list)
        for node_id, comm_id in self._communities.items():
            comm_papers[comm_id].append(node_id)

        communities = []
        for comm_id, paper_ids in comm_papers.items():
            # 找社区内 PageRank 最高的论文作为代表
            if self._pagerank:
                top_in_comm = max(paper_ids, key=lambda p: self._pagerank.get(p, 0))
            else:
                top_in_comm = paper_ids[0] if paper_ids else None

            # 提取社区概念标签
            all_concepts = []
            for pid in paper_ids:
                concepts = self.nodes.get(pid, {}).get("concepts", [])
                if isinstance(concepts, list):
                    all_concepts.extend(str(c) for c in concepts[:2])

            # 统计最常见的概念
            concept_counts: Dict[str, int] = defaultdict(int)
            for c in all_concepts:
                concept_counts[c] += 1

            top_concepts = sorted(concept_counts.keys(), key=lambda x: concept_counts[x], reverse=True)[:3]

            communities.append({
                "community_id": comm_id,
                "size": len(paper_ids),
                "representative_paper": self.nodes.get(top_in_comm, {}) if top_in_comm else {},
                "top_concepts": top_concepts,
                "paper_ids": paper_ids,
            })

        return sorted(communities, key=lambda x: x["size"], reverse=True)

    # ──────────────────────────────────────────────────────────────
    # 共引分析 (Co-citation)
    # ──────────────────────────────────────────────────────────────

    def compute_cocitation(self, min_count: int = 2) -> List[Dict]:
        """计算共引关系：论文 A 和论文 B 被同一组论文引用的次数。

        共引强度高 → 研究主题高度相关。
        """
        cocitations: DefaultDict[Tuple[str, str], int] = defaultdict(int)

        # 对每篇引用者，看它引用了哪些论文
        for citing_id in self.nodes:
            cited = [target for (src, target), _ in self.edges.items() if src == citing_id]
            # 两两配对
            for i in range(len(cited)):
                for j in range(i + 1, len(cited)):
                    a, b = cited[i], cited[j]
                    key = (a, b) if a < b else (b, a)
                    cocitations[key] += 1

        result = []
        for (a, b), count in cocitations.items():
            if count >= min_count:
                result.append({
                    "paper_a": self.nodes.get(a, {}),
                    "paper_b": self.nodes.get(b, {}),
                    "cocitation_count": count,
                    "strength": count / (1 + min(
                        self._get_in_degree(a),
                        self._get_in_degree(b)
                    ))
                })

        return sorted(result, key=lambda x: x["strength"], reverse=True)

    # ──────────────────────────────────────────────────────────────
    # 文献耦合 (Bibliographic Coupling)
    # ──────────────────────────────────────────────────────────────

    def compute_bibliographic_coupling(self, min_count: int = 2) -> List[Dict]:
        """计算文献耦合：论文 A 和论文 B 共同引用了多少相同的参考文献。

        耦合强度高 → 研究背景/方法高度相似。
        """
        # 每篇论文的参考文献集合
        references: Dict[str, Set[str]] = defaultdict(set)
        for (src, target), _ in self.edges.items():
            references[src].add(target)

        couplings: List[Dict] = []
        paper_ids = list(references.keys())

        for i in range(len(paper_ids)):
            for j in range(i + 1, len(paper_ids)):
                a, b = paper_ids[i], paper_ids[j]
                common = references[a] & references[b]
                if len(common) >= min_count:
                    couplings.append({
                        "paper_a": self.nodes.get(a, {}),
                        "paper_b": self.nodes.get(b, {}),
                        "common_references_count": len(common),
                        "common_references": list(common)[:10],
                        "strength": len(common) / len(references[a] | references[b]) if (references[a] | references[b]) else 0
                    })

        return sorted(couplings, key=lambda x: x["strength"], reverse=True)

    # ──────────────────────────────────────────────────────────────
    # 引用路径追溯
    # ──────────────────────────────────────────────────────────────

    def find_citation_path(self, from_paper: str, to_paper: str, max_depth: int = 5) -> List[Dict]:
        """找到从论文 A 到论文 B 的引用路径（知识传递轨迹）。

        例如：最新论文 → 关键中间论文 → 奠基性论文。
        """
        if self.graph is None:
            return []

        try:
            path = nx.shortest_path(self.graph, from_paper, to_paper)
            if len(path) <= max_depth:
                return [
                    {"step": i + 1, "paper": self.nodes.get(pid, {})}
                    for i, pid in enumerate(path)
                ]
        except nx.NetworkXNoPath:
            pass

        return []

    def get_knowledge_burst_subgraph(self, center_paper: str, depth: int = 2) -> "LiteratureGraphV2":
        """获取以某篇论文为中心的知识爆发子图（引用链前后扩展）。"""
        if self.graph is None:
            return self

        # 向前：哪些论文引用了它（后继知识）
        successors = set()
        current = {center_paper}
        for _ in range(depth):
            next_layer = set()
            for n in current:
                next_layer.update(self.graph.successors(n))
            successors.update(next_layer)
            current = next_layer

        # 向后：它引用了哪些论文（知识来源）
        predecessors = set()
        current = {center_paper}
        for _ in range(depth):
            next_layer = set()
            for n in current:
                next_layer.update(self.graph.predecessors(n))
            predecessors.update(next_layer)
            current = next_layer

        # 构建子图
        all_nodes = {center_paper} | successors | predecessors
        subgraph = LiteratureGraphV2(f"Subgraph: {self.nodes.get(center_paper, {}).get('title', '')[:30]}")
        for nid in all_nodes:
            if nid in self.nodes:
                subgraph._add_paper_node(nid, self.nodes[nid], is_core=(nid == center_paper))
        for (src, tgt), edge_data in self.edges.items():
            if src in all_nodes and tgt in all_nodes:
                subgraph._add_citation_edge(src, tgt)

        subgraph._compute_metrics()
        return subgraph

    # ──────────────────────────────────────────────────────────────
    # 可视化输出
    # ──────────────────────────────────────────────────────────────

    def to_pyvis_html(self, output_path: str | Path = None, max_nodes: int = 200) -> str:
        """生成 PyVis 交互式 HTML 可视化。

        返回 HTML 内容，如果指定 output_path 则写入文件。
        """
        if not HAS_PYVIS:
            return self._fallback_mermaid()

        # 节点太多时过滤，优先保留核心论文 + 高 PageRank
        node_ids = list(self.nodes.keys())
        if len(node_ids) > max_nodes:
            scored = [(self._pagerank.get(nid, 0) if self._pagerank else 0, nid) for nid in node_ids]
            scored.sort(reverse=True)
            node_ids = [nid for _, nid in scored[:max_nodes]]

        net = Network(
            height="800px",
            width="100%",
            bgcolor="#ffffff",
            font_color="#333333",
            directed=True,
            notebook=False,
        )

        # 社区配色
        colors = [
            "#e6194b", "#3cb44b", "#ffe119", "#4363d8", "#f58231",
            "#911eb4", "#42d4f4", "#f032e6", "#bfef45", "#fabed4",
            "#469990", "#dcbeff", "#9A6324", "#fffac8", "#800000",
            "#aaffc3", "#808000", "#ffd8b1", "#000075", "#a9a9a9",
        ]

        for nid in node_ids:
            data = self.nodes[nid]
            comm_id = self._communities.get(nid, 0) if self._communities else 0
            pr = self._pagerank.get(nid, 0.01) if self._pagerank else 0.01

            title_parts = [
                f"<b>{data.get('title', 'Unknown')}</b>",
                f"<br/>Year: {data.get('year', 'N/A')}",
                f"<br/>Venue: {data.get('venue', 'N/A')}",
                f"<br/>Citations: {data.get('citations', 0)}",
                f"<br/>PageRank: {pr:.4f}",
                f"<br/>Community: {comm_id}",
            ]

            # 节点大小基于 PageRank
            size = 10 + 40 * (pr / max(self._pagerank.values())) if self._pagerank else 15

            net.add_node(
                nid,
                label=data.get("title", "")[:25] + "...",
                title="".join(title_parts),
                size=size,
                color=colors[comm_id % len(colors)],
                shape="dot" if data.get("is_core") else "circle",
                borderWidth=3 if data.get("is_core") else 1,
            )

        # 添加边
        for (src, tgt), _ in self.edges.items():
            if src in node_ids and tgt in node_ids:
                net.add_edge(src, tgt, color="#999999", width=1.5, arrows="to")

        # 物理引擎配置
        net.set_options("""{
            "physics": {
                "forceAtlas2Based": {
                    "gravitationalConstant": -50,
                    "centralGravity": 0.01,
                    "springLength": 100,
                    "springConstant": 0.08
                },
                "maxVelocity": 50,
                "solver": "forceAtlas2Based",
                "timestep": 0.35,
                "stabilization": {
                    "iterations": 150,
                    "fit": true
                }
            },
            "nodes": {
                "font": {
                    "size": 11
                }
            }
        }""")

        html = net.generate_html()

        if output_path:
            Path(output_path).write_text(html, encoding="utf-8")

        return html

    def _fallback_mermaid(self) -> str:
        """无依赖时的降级方案：生成 Mermaid 图。"""
        lines = ["graph LR"]
        for nid, data in self.nodes.items():
            label = data.get("title", "")[:30].replace('"', "'")
            lines.append(f'    "{nid[:15]}"["{label}"]')
        for (src, tgt), _ in self.edges.items():
            lines.append(f'    "{src[:15]}" --> "{tgt[:15]}"')
        return "\n".join(lines)

    def get_summary(self) -> Dict:
        """获取图谱统计摘要。"""
        in_degrees = {n: self._get_in_degree(n) for n in self.nodes}
        out_degrees = {n: self._get_out_degree(n) for n in self.nodes}

        return {
            "title": self.title,
            "total_papers": len(self.nodes),
            "core_papers": sum(1 for d in self.nodes.values() if d.get("is_core")),
            "referenced_papers": sum(1 for d in self.nodes.values() if d.get("external")),
            "total_citations": len(self.edges),
            "communities_count": len(set(self._communities.values())) if self._communities else 0,
            "max_in_degree": max(in_degrees.values()) if in_degrees else 0,
            "max_out_degree": max(out_degrees.values()) if out_degrees else 0,
            "year_range": self._get_year_range(),
            "top_papers_by_pagerank": self.get_top_papers(5, "pagerank"),
            "top_papers_by_citation": self.get_top_papers(5, "citations"),
        }

    def _get_in_degree(self, node_id: str) -> int:
        if self.graph:
            return self.graph.in_degree(node_id)
        return sum(1 for (src, tgt), _ in self.edges.items() if tgt == node_id)

    def _get_out_degree(self, node_id: str) -> int:
        if self.graph:
            return self.graph.out_degree(node_id)
        return sum(1 for (src, tgt), _ in self.edges.items() if src == node_id)

    def _get_year_range(self) -> Tuple[int, int]:
        years = [d.get("year") for d in self.nodes.values() if d.get("year")]
        years = [y for y in years if isinstance(y, int)]
        if not years:
            return (0, 0)
        return (min(years), max(years))

    def to_dict(self) -> Dict:
        """导出为字典（用于持久化）。"""
        return {
            "title": self.title,
            "nodes": list(self.nodes.values()),
            "edges": [{"source": s, "target": t, **d} for (s, t), d in self.edges.items()],
            "summary": self.get_summary(),
            "communities": self.get_communities(),
        }

    def to_json(self, output_path: str | Path = None) -> str:
        """导出为 JSON。"""
        data = self.to_dict()
        json_str = json.dumps(data, ensure_ascii=False, indent=2)
        if output_path:
            Path(output_path).write_text(json_str, encoding="utf-8")
        return json_str

    # ──────────────────────────────────────────────────────────────
    # 辅助方法
    # ──────────────────────────────────────────────────────────────

    @property
    def has_networkx(self) -> bool:
        return HAS_NETWORKX

    @property
    def has_pyvis(self) -> bool:
        return HAS_PYVIS
