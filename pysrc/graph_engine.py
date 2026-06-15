"""
Graph Engine — Neo4j-native graph operations for openMegatron.

Mirrors ontology_nodes and memory_links from PostgreSQL into Neo4j,
enabling native multi-hop traversals that PostgreSQL can't do efficiently.

Capabilities:
  - Mirror writes: sync ontology_nodes + memory_links → Neo4j
  - Multi-hop traversals: "find papers cited by papers that cite X"
  - Citation path finding: shortest path between two papers
  - Author networks: co-authorship communities
  - Graph algorithms: PageRank, community detection
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections import deque
from typing import Any, Optional

logger = logging.getLogger(__name__)


class GraphEngine:
    """Neo4j-backed graph engine for native traversals."""

    def __init__(self, neo4j_driver=None, pg_pool=None):
        self._driver = neo4j_driver
        self._pg_pool = pg_pool
        self._indexes_created = False
        self._nodes: dict[str, dict] = {}
        self._edges: dict[str, dict[str, dict]] = {}
        self._entity_index: dict[str, str] = {}

    def add_node(self, node_id: str, **attrs):
        self._nodes[node_id] = dict(attrs)
        name = attrs.get("name")
        if name:
            self._entity_index[str(name)] = node_id

    def get_node(self, node_id: str) -> Optional[dict]:
        return self._nodes.get(node_id)

    def add_edge(self, source_id: str, target_id: str, relation: str = "related", **attrs):
        self._nodes.setdefault(source_id, {})
        self._nodes.setdefault(target_id, {})
        self._edges.setdefault(source_id, {})[target_id] = {"relation": relation, **attrs}

    def get_neighbors(self, node_id: str) -> list[str]:
        return list(self._edges.get(node_id, {}).keys())

    def shortest_path(self, source_id: str, target_id: str) -> Optional[list[str]]:
        if source_id not in self._nodes or target_id not in self._nodes:
            return None
        queue = deque([(source_id, [source_id])])
        seen = {source_id}
        while queue:
            current, path = queue.popleft()
            if current == target_id:
                return path
            for neighbor in self.get_neighbors(current):
                if neighbor not in seen:
                    seen.add(neighbor)
                    queue.append((neighbor, path + [neighbor]))
        return None

    def find_by_entity(self, name: str) -> Optional[str]:
        return self._entity_index.get(name)

    def bfs_from(self, node_id: str, max_depth: int = 1) -> list[str]:
        if node_id not in self._nodes:
            return []
        reached = []
        queue = deque([(node_id, 0)])
        seen = {node_id}
        while queue:
            current, depth = queue.popleft()
            reached.append(current)
            if depth >= max_depth:
                continue
            for neighbor in self.get_neighbors(current):
                if neighbor not in seen:
                    seen.add(neighbor)
                    queue.append((neighbor, depth + 1))
        return reached

    @property
    def node_count(self) -> int:
        return len(self._nodes)

    @property
    def edge_count(self) -> int:
        return sum(len(targets) for targets in self._edges.values())

    # ── Schema initialization ──────────────────────────

    async def ensure_schema(self):
        """Create indexes and constraints in Neo4j."""
        if self._indexes_created:
            return
        async with self._driver.session() as session:
            # Constraints
            for label in ["OntologyNode", "Paper", "Author", "Venue", "Topic",
                          "Skill", "Decision", "Owner", "Memory", "Evidence",
                          "Claim", "Artifact", "Entity", "Alternative",
                          "LiteratureReview", "Project", "Session", "Scope",
                          "HyperEdge", "Tool", "ConversationSession", "MemoryRecord",
                          "RAGEntity", "Document", "Community", "MemoryCard"]:
                try:
                    await session.run(
                        f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) REQUIRE n.id IS UNIQUE"
                    )
                except Exception:
                    pass  # Constraint may already exist
            # Indexes for fast lookup
            for prop in ["kind", "name", "doi", "year"]:
                try:
                    await session.run(f"CREATE INDEX IF NOT EXISTS FOR (n:OntologyNode) ON (n.{prop})")
                except Exception:
                    pass
            self._indexes_created = True
            logger.info("GraphEngine: Neo4j schema ready")

    # ── Mirror writes from PostgreSQL ──────────────────

    async def upsert_node(self, node_id: str, kind: str, label: str, properties: dict = None):
        """Mirror an ontology node into Neo4j."""
        props = properties or {}
        # Map kind to Neo4j label
        label_map = {
            "paper": "Paper", "author": "Author", "venue": "Venue",
            "topic": "Topic", "skill": "Skill", "tool": "Tool",
            "decision": "Decision", "owner": "Owner", "memory": "Memory",
            "evidence": "Evidence", "claim": "Claim", "artifact": "Artifact",
            "entity": "Entity",
            "rag_entity": "RAGEntity", "alternative": "Alternative",
            "literature_review": "LiteratureReview",
            "project": "Project", "session": "Session",
            "scope": "Scope", "hyperedge": "HyperEdge",
            "document": "Document", "community": "Community",
            "memory_card": "MemoryCard",
        }
        neo_label = label_map.get(kind, "OntologyNode")

        try:
            async with self._driver.session() as session:
                await session.run(
                    f"""
                    MERGE (n:{neo_label}:OntologyNode {{id: $id}})
                    SET n.kind = $kind,
                        n.label = $label,
                        n.name = $name,
                        n.doi = $doi,
                        n.year = $year,
                        n.properties = $props,
                        n.updated_at = datetime()
                    """,
                    id=node_id,
                    kind=kind,
                    label=label[:500],
                    name=props.get("title") or props.get("name") or label[:200],
                    doi=props.get("doi", ""),
                    year=props.get("year"),
                    props=json.dumps(props, ensure_ascii=False),
                )
        except Exception as e:
            logger.warning(f"GraphEngine: Neo4j upsert_node failed for {node_id}: {e}")

    async def upsert_edge(self, source_id: str, target_id: str, relation: str,
                          confidence: float = 1.0, metadata: dict = None):
        """Mirror a memory_link into Neo4j as a relationship."""
        props = metadata or {}
        try:
            async with self._driver.session() as session:
                await session.run(
                    f"""
                    MATCH (a:OntologyNode {{id: $src}})
                    MATCH (b:OntologyNode {{id: $tgt}})
                    MERGE (a)-[r:{_safe_rel_type(relation)}]->(b)
                    SET r.confidence = $conf,
                        r.metadata = $meta,
                        r.updated_at = datetime()
                    """,
                    src=source_id, tgt=target_id,
                    conf=confidence,
                    meta=json.dumps(props, ensure_ascii=False),
                )
        except Exception as e:
            logger.warning(f"GraphEngine: Neo4j upsert_edge failed {source_id}--[{relation}]-->{target_id}: {e}")

    async def delete_node(self, node_id: str):
        """Remove a node and all its relationships from Neo4j."""
        try:
            async with self._driver.session() as session:
                await session.run(
                    "MATCH (n:OntologyNode {id: $id}) DETACH DELETE n",
                    id=node_id,
                )
        except Exception as e:
            logger.warning(f"GraphEngine: Neo4j delete_node failed for {node_id}: {e}")

    # ── Multi-hop graph traversals ─────────────────────

    async def citation_path(self, from_paper_id: str, to_paper_id: str, max_depth: int = 5) -> dict:
        """Find the shortest citation path between two papers."""
        try:
            async with self._driver.session() as session:
                result = await session.run(
                    """
                    MATCH path = shortestPath(
                        (a:Paper {id: $from})-[*1..$depth]-(b:Paper {id: $to})
                    )
                    RETURN [n IN nodes(path) | {id: n.id, label: n.label, kind: n.kind}] AS nodes,
                           [r IN relationships(path) | {type: type(r), confidence: r.confidence}] AS edges,
                           length(path) AS hops
                    """,
                    from_paper_id=from_paper_id, to=to_paper_id, depth=max_depth,
                )
                record = await result.single()
                if record:
                    return {
                        "found": True,
                        "hops": record["hops"],
                        "path_nodes": record["nodes"],
                        "path_edges": record["edges"],
                    }
                return {"found": False, "hops": None}
        except Exception as e:
            logger.error(f"GraphEngine: citation_path failed: {e}")
            return {"found": False, "error": str(e)}

    async def cited_by_chain(self, paper_id: str, depth: int = 2, limit: int = 20) -> dict:
        """Find papers that cite papers that cite this paper (multi-hop inbound)."""
        try:
            async with self._driver.session() as session:
                result = await session.run(
                    """
                    MATCH (cited:Paper {id: $id})<-[r:cites*1..$depth]-(citer:Paper)
                    RETURN DISTINCT citer.id AS id, citer.label AS title,
                           citer.doi AS doi, citer.year AS year,
                           length(r) AS distance
                    ORDER BY distance, citer.year DESC
                    LIMIT $limit
                    """,
                    id=paper_id, depth=depth, limit=limit,
                )
                papers = []
                async for record in result:
                    papers.append({
                        "id": record["id"], "title": record["title"],
                        "doi": record["doi"], "year": record["year"],
                        "distance": record["distance"],
                    })
                return {"paper_id": paper_id, "depth": depth, "count": len(papers), "papers": papers}
        except Exception as e:
            logger.error(f"GraphEngine: cited_by_chain failed: {e}")
            return {"error": str(e)}

    async def coauthor_network(self, author_name: str, depth: int = 2, limit: int = 30) -> dict:
        """Find an author's collaboration network."""
        try:
            async with self._driver.session() as session:
                result = await session.run(
                    """
                    MATCH (a:Author {name: $name})-[*1..$depth]-(collab:Author)
                    WHERE collab <> a
                    WITH collab, count(*) AS strength
                    RETURN collab.name AS name, collab.id AS id, strength
                    ORDER BY strength DESC
                    LIMIT $limit
                    """,
                    name=author_name, depth=depth, limit=limit,
                )
                collaborators = []
                async for record in result:
                    collaborators.append({
                        "name": record["name"], "id": record["id"],
                        "collaboration_strength": record["strength"],
                    })
                return {"author": author_name, "collaborators": collaborators, "count": len(collaborators)}
        except Exception as e:
            logger.error(f"GraphEngine: coauthor_network failed: {e}")
            return {"error": str(e)}

    async def find_influential_papers(self, topic_hint: str = "", limit: int = 10) -> list[dict]:
        """Find influential papers by PageRank-like citation count in Neo4j."""
        try:
            async with self._driver.session() as session:
                query = """
                    MATCH (p:Paper)<-[r:cites]-(citer:Paper)
                    WITH p, count(r) AS citation_count
                    ORDER BY citation_count DESC
                    LIMIT $limit
                    RETURN p.id AS id, p.label AS title, p.doi AS doi,
                           p.year AS year, citation_count
                """
                result = await session.run(query, limit=limit)
                papers = []
                async for record in result:
                    papers.append({
                        "id": record["id"], "title": record["title"],
                        "doi": record["doi"], "year": record["year"],
                        "citations_within_graph": record["citation_count"],
                    })
                return papers
        except Exception as e:
            logger.error(f"GraphEngine: find_influential_papers failed: {e}")
            return []

    async def find_research_gaps(self, topic_keyword: str, limit: int = 10) -> list[dict]:
        """Find papers with few/no incoming citations — potential research gaps."""
        try:
            async with self._driver.session() as session:
                result = await session.run(
                    """
                    MATCH (p:Paper)
                    WHERE p.label CONTAINS $kw OR p.name CONTAINS $kw
                    OPTIONAL MATCH (p)<-[r:cites]-(:Paper)
                    WITH p, count(r) AS citations
                    WHERE citations <= 2
                    RETURN p.id AS id, p.label AS title, p.year AS year,
                           p.doi AS doi, citations
                    ORDER BY citations, p.year DESC
                    LIMIT $limit
                    """,
                    kw=topic_keyword, limit=limit,
                )
                gaps = []
                async for record in result:
                    gaps.append({
                        "id": record["id"], "title": record["title"],
                        "year": record["year"], "doi": record["doi"],
                        "citations": record["citations"],
                        "gap_signal": "high" if record["citations"] == 0 else "medium",
                    })
                return gaps
        except Exception as e:
            logger.error(f"GraphEngine: find_research_gaps failed: {e}")
            return []

    async def community_detect(self, label: str = "Paper", limit: int = 50) -> dict:
        """Detect communities using label propagation (approximate)."""
        try:
            async with self._driver.session() as session:
                result = await session.run(
                    f"""
                    MATCH (n:{label})
                    OPTIONAL MATCH (n)-[r]-(m:{label})
                    WITH n, collect(DISTINCT m.id)[0..5] AS neighbors
                    RETURN n.id AS id, n.label AS label,
                           n.kind AS kind, size(neighbors) AS degree
                    ORDER BY degree DESC
                    LIMIT $limit
                    """,
                    limit=limit,
                )
                nodes = []
                async for record in result:
                    nodes.append({
                        "id": record["id"], "label": record["label"],
                        "kind": record["kind"], "degree": record["degree"],
                    })
                # Simple community grouping by degree tiers
                hubs = [n for n in nodes if n["degree"] >= 10]
                mid = [n for n in nodes if 3 <= n["degree"] < 10]
                peripheral = [n for n in nodes if n["degree"] < 3]
                return {
                    "total_nodes": len(nodes),
                    "communities": {
                        "hubs": len(hubs),
                        "mid_connectivity": len(mid),
                        "peripheral": len(peripheral),
                    },
                    "hubs": hubs[:5],
                }
        except Exception as e:
            logger.error(f"GraphEngine: community_detect failed: {e}")
            return {"error": str(e)}

    def is_available(self) -> bool:
        """Check if Neo4j is connected."""
        return self._driver is not None


def _safe_rel_type(relation: str) -> str:
    """Convert a relation name to a Neo4j-safe relationship type.

    Preserves the original case to match ontology relation IDs exactly.
    """
    # Replace non-alphanumeric chars with underscore
    import re
    safe = re.sub(r'[^a-zA-Z0-9]', '_', relation).strip('_')
    return safe or "related"
