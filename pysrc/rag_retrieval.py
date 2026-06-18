"""Tri-Store Hybrid RAG — Retrieval Engine.

Implements three retrieval strategies:
  1. Local Search  (PostgreSQL): hybrid vector + full-text, with ACL filtering
  2. Global Search (Neo4j): entity graph traversal + community matching
  3. Fused Search  (both): parallel retrieval + CrossEncoder rerank

Query Router auto-selects strategy based on intent classification.
Redis semantic cache intercepts repeated queries before any DB access.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_TOP_K = 10
MAX_CHUNKS_GLOBAL = 20


# ── Strategy & Result Types ──────────────────────────────────────────────────


class SearchStrategy(str, Enum):
    LOCAL = "local"       # PostgreSQL only
    GLOBAL = "global"     # Neo4j only
    FUSED = "fused"       # Both, reranked
    AUTO = "auto"         # Auto-detect from query


@dataclass
class ChunkResult:
    chunk_id: str
    doc_id: str
    text: str
    score: float
    source: str  # "vector", "fulltext", "graph", "community"
    metadata: dict = field(default_factory=dict)


@dataclass
class EntityContext:
    name: str
    entity_type: str
    relationships: List[Dict[str, str]] = field(default_factory=list)


@dataclass
class CommunityContext:
    community_id: int
    summary: str
    entity_count: int
    score: float = 0.0


@dataclass
class RetrievalResult:
    chunks: List[ChunkResult]
    entities: List[EntityContext]
    communities: List[CommunityContext]
    strategy: SearchStrategy
    elapsed_ms: float
    from_cache: bool = False


# ── Query Classification ─────────────────────────────────────────────────────


# Heuristic patterns for query type detection
LOCAL_PATTERNS = [
    re.compile(r"什么(是|叫)|定义|含义|概念", re.IGNORECASE),
    re.compile(r"多少|什么时候|哪里|谁|哪个|何时|何地", re.IGNORECASE),
    re.compile(r"^(what|when|where|who|which|how much|how many)\b", re.IGNORECASE),
    re.compile(r"(列出|列举|查询|查找|找出)\b", re.IGNORECASE),
]

GLOBAL_PATTERNS = [
    re.compile(r"关系|联系|关联|影响|依赖|因果", re.IGNORECASE),
    re.compile(r"总结|概述|综述|概况|趋势|演变|发展", re.IGNORECASE),
    re.compile(r"(how does|how do|relationship|connection|depend|influence|impact|trend|evolution)\b", re.IGNORECASE),
    re.compile(r"(compare|contrast|difference|similar|versus|vs\.?)\b", re.IGNORECASE),
    re.compile(r"全局|整体|全景|宏观|架构", re.IGNORECASE),
]


def classify_query(query: str) -> SearchStrategy:
    """Classify query intent: local (fact lookup) vs global (relation/synthesis).

    Uses fast regex heuristics. For production, can optionally call LLM
    for ambiguous cases.
    """
    local_score = sum(1 for p in LOCAL_PATTERNS if p.search(query))
    global_score = sum(1 for p in GLOBAL_PATTERNS if p.search(query))

    if global_score > local_score:
        return SearchStrategy.GLOBAL
    elif local_score > global_score:
        return SearchStrategy.LOCAL
    elif global_score >= 2:
        return SearchStrategy.FUSED
    else:
        # Default: try local first, fallback to fused
        return SearchStrategy.LOCAL


# ── Redis Semantic Cache ─────────────────────────────────────────────────────


class SemanticCache:
    """Redis-based semantic query cache.

    Stores (query_embedding_hash, answer) pairs. On cache hit,
    returns the cached answer directly — skipping all DB access.
    """

    def __init__(self, redis_client, embedder, ttl: int = 3600):
        self._redis = redis_client
        self._embedder = embedder
        self._ttl = ttl

    async def get(self, query: str, config: dict = None) -> Optional[dict]:
        """Check if a semantically similar query was cached."""
        try:
            emb = await self._embedder.embed_single(query, config)
            # Hash the top-N dimensions for fuzzy matching
            top_dims = sorted(range(len(emb)), key=lambda i: abs(emb[i]), reverse=True)[:32]
            cache_key = f"rag:cache:{hashlib.sha256(str(top_dims).encode()).hexdigest()[:16]}"
            cached = await self._redis.get(cache_key)
            if cached:
                data = json.loads(cached)
                logger.debug(f"RAG cache hit for query: {query[:80]}")
                return data
        except Exception as e:
            logger.debug(f"RAG cache lookup failed: {e}")
        return None

    async def set(self, query: str, answer_data: dict, config: dict = None):
        """Cache an answer for future semantic matching."""
        try:
            emb = await self._embedder.embed_single(query, config)
            top_dims = sorted(range(len(emb)), key=lambda i: abs(emb[i]), reverse=True)[:32]
            cache_key = f"rag:cache:{hashlib.sha256(str(top_dims).encode()).hexdigest()[:16]}"
            data = json.dumps(answer_data, ensure_ascii=False, default=str)
            await self._redis.setex(cache_key, self._ttl, data)
        except Exception as e:
            logger.debug(f"RAG cache write failed: {e}")

    async def invalidate(self, doc_id: str = None):
        """Invalidate cache entries (called after document changes)."""
        # Pattern-based invalidation: clear all rag:cache:* keys
        # (simplified; production would use a more targeted approach)
        try:
            keys = await self._redis.keys("rag:cache:*")
            if keys:
                await self._redis.delete(*keys)
                logger.info(f"Invalidated {len(keys)} RAG cache entries")
        except Exception:
            pass


# ── Local Search (PostgreSQL) ────────────────────────────────────────────────


class LocalSearcher:
    """Hybrid vector + full-text search on PostgreSQL/pgvector."""

    def __init__(self, pg_pool, embedder, config: dict = None):
        self._pool = pg_pool
        self._embedder = embedder
        self._config = config or {}

    async def search(self, query: str, owner_id: str = "default",
                     scope: str = "shared", top_k: int = DEFAULT_TOP_K,
                     filters: dict = None) -> List[ChunkResult]:
        """Hybrid search: vector similarity + full-text, weighted combination."""
        query_emb = await self._embedder.embed_single(query, self._config)

        async with self._pool.acquire() as conn:
            # Vector search
            vec_rows = await conn.fetch(
                """SELECT c.id, c.doc_id, c.text, c.metadata,
                          1.0 - (c.embedding <=> $1::vector) AS score
                   FROM rag_chunks c
                   WHERE c.owner_id = $2 AND c.scope = $3
                   ORDER BY c.embedding <=> $1::vector
                   LIMIT $4""",
                json.dumps(query_emb), owner_id, scope, top_k * 2,
            )

            # Full-text search (if tsvector available)
            try:
                ft_rows = await conn.fetch(
                    """SELECT c.id, c.doc_id, c.text, c.metadata,
                              ts_rank(to_tsvector('simple', c.text), plainto_tsquery('simple', $1)) AS score
                       FROM rag_chunks c
                       WHERE c.owner_id = $2 AND c.scope = $3
                         AND to_tsvector('simple', c.text) @@ plainto_tsquery('simple', $1)
                       ORDER BY score DESC
                       LIMIT $4""",
                    query, owner_id, scope, top_k,
                )
            except Exception:
                ft_rows = []

            # Merge results
            results: Dict[str, ChunkResult] = {}

            for row in vec_rows:
                rid = row["id"]
                results[rid] = ChunkResult(
                    chunk_id=rid,
                    doc_id=row["doc_id"],
                    text=row["text"],
                    score=row["score"] * 0.7,
                    source="vector",
                    metadata=json.loads(row["metadata"]) if row["metadata"] else {},
                )

            for row in ft_rows:
                rid = row["id"]
                ft_score = row["score"] * 0.3
                if rid in results:
                    results[rid].score += ft_score
                    results[rid].source = "hybrid"
                else:
                    results[rid] = ChunkResult(
                        chunk_id=rid,
                        doc_id=row["doc_id"],
                        text=row["text"],
                        score=ft_score,
                        source="fulltext",
                        metadata=json.loads(row["metadata"]) if row["metadata"] else {},
                    )

            # Sort by score, return top_k
            sorted_results = sorted(results.values(), key=lambda r: r.score, reverse=True)
            return sorted_results[:top_k]


# ── Global Search (Neo4j) ────────────────────────────────────────────────────


class GlobalSearcher:
    """Entity-graph + community search via Neo4j."""

    def __init__(self, neo4j_driver, embedder, config: dict = None):
        self._driver = neo4j_driver
        self._embedder = embedder
        self._config = config or {}

    async def search(self, query: str, owner_id: str = "default",
                     top_k: int = DEFAULT_TOP_K) -> Tuple[List[ChunkResult], List[EntityContext], List[CommunityContext]]:
        """Global search: find relevant entities → traverse graph → get communities."""
        # Extract query entities (deterministic first)
        from rag_ingest import extract_entities_deterministic
        query_entities = extract_entities_deterministic(query)

        entity_names = [e["name"] for e in query_entities[:10]]
        if not entity_names:
            # Fallback: use query keywords
            entity_names = [w for w in re.findall(r"[a-zA-Z一-鿿]{3,}", query) if len(w) >= 3][:5]
        if not entity_names:
            return [], [], []

        entity_contexts: List[EntityContext] = []
        chunk_ids: set = set()

        with self._driver.session(database="neo4j") as session:
            # Find matching entities and their 1-hop neighborhood
            for name in entity_names[:5]:
                result = session.run(
                    """MATCH (e:OntologyNode {kind: 'rag_entity'})
                       WHERE toLower(e.label) CONTAINS toLower($name)
                       OPTIONAL MATCH (e)-[r:related|cites|supports|contradicts]-(other:OntologyNode)
                       RETURN e, r, other
                       LIMIT 20""",
                    name=name,
                )
                records = list(result)
                if records:
                    relationships = []
                    related_entities: set = set()
                    for rec in records:
                        if rec["r"] and rec["other"]:
                            relationships.append({
                                "relation": rec["r"].type if hasattr(rec["r"], "type") else str(rec["r"]),
                                "target": rec["other"].get("label", ""),
                                "target_type": rec["other"].get("kind", ""),
                            })
                            related_entities.add(rec["other"].get("label", ""))

                    entity_contexts.append(EntityContext(
                        name=name,
                        entity_type=records[0]["e"].get("type", "unknown") if records else "unknown",
                        relationships=relationships,
                    ))

                    # Find chunk references via MENTIONS edges
                    chunk_result = session.run(
                        """MATCH (e:OntologyNode {kind: 'rag_entity'})-[m:rag_mentions]->(c)
                           WHERE toLower(e.label) CONTAINS toLower($name)
                           RETURN c.id AS chunk_id, m.confidence AS confidence
                           LIMIT 30""",
                        name=name,
                    )
                    for cr in chunk_result:
                        if cr["chunk_id"]:
                            chunk_ids.add(cr["chunk_id"])

        # Fetch chunk texts from PostgreSQL
        chunks = await self._fetch_chunks_by_ids(chunk_ids, owner_id) if chunk_ids else []

        # Community search
        communities = await self._search_communities(query)

        return chunks, entity_contexts, communities

    async def _fetch_chunks_by_ids(self, chunk_ids: set, owner_id: str) -> List[ChunkResult]:
        """Fetch chunk texts from PostgreSQL by chunk IDs."""
        if not chunk_ids:
            return []
        results = []
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT id, doc_id, text, metadata
                   FROM rag_chunks
                   WHERE id = ANY($1) AND owner_id = $2
                   LIMIT $3""",
                list(chunk_ids), owner_id, MAX_CHUNKS_GLOBAL,
            )
            for row in rows:
                results.append(ChunkResult(
                    chunk_id=row["id"],
                    doc_id=row["doc_id"],
                    text=row["text"],
                    score=0.6,  # graph-derived results get moderate base score
                    source="graph",
                    metadata=json.loads(row["metadata"]) if row["metadata"] else {},
                ))
        return results

    async def _search_communities(self, query: str) -> List[CommunityContext]:
        """Match query to community summaries via vector search."""
        query_emb = await self._embedder.embed_single(query, self._config)
        communities = []
        async with self._pool.acquire() as conn:
            try:
                rows = await conn.fetch(
                    """SELECT community_id, entity_count, summary,
                              1.0 - (embedding <=> $1::vector) AS score
                       FROM rag_communities
                       ORDER BY embedding <=> $1::vector
                       LIMIT 3""",
                    json.dumps(query_emb),
                )
                for row in rows:
                    communities.append(CommunityContext(
                        community_id=row["community_id"],
                        summary=row["summary"],
                        entity_count=row["entity_count"],
                        score=row["score"],
                    ))
            except Exception:
                pass
        return communities

    @property
    def _pool(self):
        # Delegate to the same pg pool as LocalSearcher
        # (set externally after construction)
        return getattr(self, "__pool", None)

    @_pool.setter
    def _pool(self, value):
        self.__pool = value


# ── Fused Search ─────────────────────────────────────────────────────────────


class FusedSearcher:
    """Combines local + global search with CrossEncoder reranking."""

    def __init__(self, local: LocalSearcher, global_searcher: GlobalSearcher,
                 reranker=None):
        self._local = local
        self._global = global_searcher
        self._reranker = reranker  # CrossEncoder instance, if available

    async def search(self, query: str, owner_id: str = "default",
                     scope: str = "shared", top_k: int = DEFAULT_TOP_K,
                     filters: dict = None) -> RetrievalResult:
        """Run local + global in parallel, merge and rerank."""
        t0 = time.monotonic()

        # Run both in parallel
        local_task = self._local.search(query, owner_id, scope, top_k, filters)
        global_task = self._global.search(query, owner_id, top_k)

        local_chunks, (global_chunks, entities, communities) = await asyncio.gather(
            local_task, global_task,
        )

        # Merge chunks, deduplicate by chunk_id
        merged: Dict[str, ChunkResult] = {}
        for ch in local_chunks:
            merged[ch.chunk_id] = ch
        for ch in global_chunks:
            if ch.chunk_id in merged:
                merged[ch.chunk_id].score = max(merged[ch.chunk_id].score, ch.score)
                merged[ch.chunk_id].source = "fused"
            else:
                merged[ch.chunk_id] = ch

        all_chunks = sorted(merged.values(), key=lambda r: r.score, reverse=True)

        # Rerank with CrossEncoder if available
        if self._reranker is not None and len(all_chunks) > top_k:
            try:
                pairs = [(query, ch.text) for ch in all_chunks[:top_k * 2]]
                scores = await asyncio.to_thread(self._reranker.predict, pairs)
                for ch, score in zip(all_chunks[:top_k * 2], scores):
                    ch.score = float(score)
                all_chunks.sort(key=lambda r: r.score, reverse=True)
            except Exception as e:
                logger.debug(f"Reranker failed, using original scores: {e}")

        elapsed = (time.monotonic() - t0) * 1000
        return RetrievalResult(
            chunks=all_chunks[:top_k],
            entities=entities,
            communities=communities,
            strategy=SearchStrategy.FUSED,
            elapsed_ms=round(elapsed, 1),
        )


# ── Main Retrieval Engine ────────────────────────────────────────────────────


class RAGRetrievalEngine:
    """Top-level retrieval API. Routes queries, manages cache, returns results."""

    def __init__(self, pg_pool, neo4j_driver, redis_client,
                 embedder, reranker=None, config: dict = None):
        self._config = config or {}
        self._local = LocalSearcher(pg_pool, embedder, config)
        self._global = GlobalSearcher(neo4j_driver, embedder, config)
        self._global._pool = pg_pool  # Share pg pool for chunk fetching
        self._fused = FusedSearcher(self._local, self._global, reranker)
        self._cache = SemanticCache(redis_client, embedder)

    async def query(self, query_text: str, owner_id: str = "default",
                    scope: str = "shared", strategy: SearchStrategy = SearchStrategy.AUTO,
                    top_k: int = DEFAULT_TOP_K,
                    filters: dict = None) -> RetrievalResult:
        """Main RAG query entry point."""
        t0 = time.monotonic()

        # Step 0: Check Redis semantic cache
        cached = await self._cache.get(query_text, self._config)
        if cached:
            return RetrievalResult(
                chunks=[ChunkResult(**c) for c in cached.get("chunks", [])],
                entities=[EntityContext(**e) for e in cached.get("entities", [])],
                communities=[CommunityContext(**c) for c in cached.get("communities", [])],
                strategy=SearchStrategy(cached.get("strategy", "local")),
                elapsed_ms=(time.monotonic() - t0) * 1000,
                from_cache=True,
            )

        # Step 1: Classify query
        if strategy == SearchStrategy.AUTO:
            strategy = classify_query(query_text)

        # Step 2: Execute search
        if strategy == SearchStrategy.LOCAL:
            chunks = await self._local.search(query_text, owner_id, scope, top_k, filters)
            result = RetrievalResult(
                chunks=chunks, entities=[], communities=[],
                strategy=strategy, elapsed_ms=(time.monotonic() - t0) * 1000,
            )
        elif strategy == SearchStrategy.GLOBAL:
            chunks, entities, communities = await self._global.search(query_text, owner_id, top_k)
            result = RetrievalResult(
                chunks=chunks, entities=entities, communities=communities,
                strategy=strategy, elapsed_ms=(time.monotonic() - t0) * 1000,
            )
        else:
            result = await self._fused.search(query_text, owner_id, scope, top_k, filters)

        # Step 3: Cache the result (async, fire-and-forget)
        asyncio.ensure_future(self._cache_result(query_text, result))

        return result

    async def _cache_result(self, query: str, result: RetrievalResult):
        """Cache search results for future queries."""
        try:
            cache_data = {
                "chunks": [{"chunk_id": c.chunk_id, "doc_id": c.doc_id,
                            "text": c.text, "score": c.score,
                            "source": c.source, "metadata": c.metadata}
                           for c in result.chunks],
                "entities": [{"name": e.name, "entity_type": e.entity_type,
                              "relationships": e.relationships}
                             for e in result.entities],
                "communities": [{"community_id": c.community_id,
                                 "summary": c.summary,
                                 "entity_count": c.entity_count, "score": c.score}
                                for c in result.communities],
                "strategy": result.strategy.value,
            }
            await self._cache.set(query, cache_data, self._config)
        except Exception:
            pass

    async def invalidate_cache(self, doc_id: str = None):
        """Invalidate cache after document changes."""
        await self._cache.invalidate(doc_id)


# ── Convenience API ──────────────────────────────────────────────────────────

async def hybrid_search(query: str, owner_id: str = "default",
                        scope: str = "shared", strategy: str = "auto",
                        top_k: int = DEFAULT_TOP_K,
                        engine: RAGRetrievalEngine = None,
                        config: dict = None) -> dict:
    """One-liner: search the RAG knowledge base. Returns serializable dict."""
    if engine is None:
        return {"chunks": [], "entities": [], "communities": [],
                "error": "No retrieval engine configured"}

    strategy_enum = SearchStrategy(strategy) if strategy != "auto" else SearchStrategy.AUTO
    result = await engine.query(query_text=query, owner_id=owner_id,
                                scope=scope, strategy=strategy_enum, top_k=top_k)

    return {
        "chunks": [
            {"chunk_id": c.chunk_id, "doc_id": c.doc_id,
             "text": c.text, "score": c.score, "source": c.source,
             "metadata": c.metadata}
            for c in result.chunks
        ],
        "entities": [
            {"name": e.name, "type": e.entity_type,
             "relationships": e.relationships}
            for e in result.entities
        ],
        "communities": [
            {"community_id": c.community_id, "summary": c.summary,
             "entity_count": c.entity_count, "score": c.score}
            for c in result.communities
        ],
        "strategy": result.strategy.value,
        "elapsed_ms": result.elapsed_ms,
        "from_cache": result.from_cache,
    }
