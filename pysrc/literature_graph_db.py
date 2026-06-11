"""literature_graph_db.py - 文献图谱持久化层

提供给 MemoryDatabases 的 store_paper_graph / query_literature_graph / search_papers_by_embedding 方法。
这些方法使用 pg_pool 和 service 进行操作。
"""


async def store_paper_graph(memory_db, graph_data: dict) -> str:
    """Persist a literature graph into the hypergraph store."""
    import json as _j
    d = chr(36)
    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])
    review_id = graph_data.get("review_id") or memory_db.service.stable_id(
        "litrev", {"time": memory_db.service.utc_iso()}
    )
    async with memory_db.pg_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO memory_hyperedges (id, edge_type, label, summary, confidence, metadata, created_at, updated_at) VALUES ("
            + d + "1, " + d + "2, " + d + "3, " + d + "4, " + d + "5, " + d + "6::jsonb, NOW(), NOW())"
            + " ON CONFLICT (id) DO UPDATE SET edge_type = EXCLUDED.edge_type, label = EXCLUDED.label,"
            + " summary = EXCLUDED.summary, confidence = EXCLUDED.confidence, metadata = EXCLUDED.metadata, updated_at = NOW()",
            review_id, "literature_review", "Literature Review",
            _j.dumps({"node_count": len(nodes), "edge_count": len(edges), "review_id": review_id}),
            1.0,
            _j.dumps({"review_id": review_id, "ontology": "ontology-guided-hypergraph-memory.v2"}),
        )
        for n in nodes:
            pid = n.get("id") or memory_db.service.ontology_node_id("paper", n.get("title", ""))
            label = (n.get("title") or "")[:200] or pid
            await memory_db.service._upsert_ontology_node(conn, pid, "paper", label, {
                "source": "citation_graph", "title": (n.get("title") or "")[:500],
                "year": n.get("year"), "venue": (n.get("venue") or "")[:200],
                "citations": n.get("citations", 0), "doi": (n.get("doi") or "")[:200],
                "authors": (n.get("authors") or "")[:500], "type": n.get("type", "journal"),
                "external": n.get("external", False),
                "concepts": _j.dumps(n.get("concepts", [])),
            })
            await memory_db.service._upsert_hyperedge_member(conn, review_id, pid, "paper", "paper", 0.9, {"title": label[:200]})
            sql_link = ("INSERT INTO memory_links (source_id, target_id, relation, confidence, metadata, created_at) VALUES ("
                        + d + "1, " + d + "2, " + d + "3, " + d + "4, " + d + "5::jsonb, NOW())"
                        + " ON CONFLICT (source_id, target_id, relation) DO NOTHING")
            for a_name in [a.strip() for a in str(n.get("authors") or "").split(";") if a.strip()]:
                aid = memory_db.service.ontology_node_id("author", a_name)
                await memory_db.service._upsert_ontology_node(conn, aid, "author", a_name, {"source": "citation_graph"})
                await conn.execute(sql_link, pid, aid, "authored_by", 1.0, _j.dumps({"author": a_name}))
            venue_str = str(n.get("venue") or "").strip()
            if venue_str:
                vid = memory_db.service.ontology_node_id("venue", venue_str)
                await memory_db.service._upsert_ontology_node(conn, vid, "venue", venue_str, {"source": "citation_graph"})
                await conn.execute(sql_link, pid, vid, "published_in", 0.9, _j.dumps({"venue": venue_str}))
        known = {n.get("id"): True for n in nodes if n.get("id")}
        for e in edges:
            s, t = e.get("source", ""), e.get("target", "")
            if s in known and t in known:
                await conn.execute(sql_link, s, t, str(e.get("type", "cites")), 0.85, _j.dumps({"edge_type": e.get("type", "cites")}))
    return review_id


async def query_literature_graph(memory_db, paper_title: str = None, topic: str = None, limit: int = 50) -> dict:
    """Retrieve stored literature graph with indexed ILIKE search."""
    if not memory_db.service:
        return {"nodes": [], "edges": []}
    d = chr(36)
    async with memory_db.pg_pool.acquire() as conn:
        if paper_title:
            fuzzy = "%" + paper_title.replace("%", "\\%") + "%"
            sql = ("SELECT source_id, metadata FROM memory_links WHERE relation = 'is_a'"
                   " AND metadata->>'kind' = 'paper' AND metadata->>'title' ILIKE "
                   + d + "1 LIMIT " + d + "2")
            link_rows = await conn.fetch(sql, fuzzy, limit)
        else:
            sql = ("SELECT source_id, metadata FROM memory_links WHERE relation = 'is_a'"
                   " AND metadata->>'kind' = 'paper' ORDER BY created_at DESC LIMIT "
                   + d + "1")
            link_rows = await conn.fetch(sql, limit)
        nodes = []
        seen = set()
        for r in link_rows:
            pid = r["source_id"]
            if pid in seen:
                continue
            seen.add(pid)
            md = r["metadata"] or {}
            nodes.append({
                "id": pid, "title": md.get("title", md.get("label", "")),
                "year": md.get("year"), "venue": md.get("venue", ""),
                "citations": md.get("citations", 0), "doi": md.get("doi", ""),
                "authors": md.get("authors", ""), "type": md.get("type", "journal"),
            })
        edges = []
        if nodes:
            pids = [n["id"] for n in nodes]
            sql2 = ("SELECT source_id, target_id, relation FROM memory_links"
                    " WHERE source_id = ANY(" + d + "1::text[]) AND target_id = ANY(" + d + "2::text[])"
                    " AND relation IN ('cites','surveys','extends')")
            erows = await conn.fetch(sql2, pids, pids)
            for er in erows:
                edges.append({"source": er["source_id"], "target": er["target_id"], "type": er["relation"]})
    return {"nodes": nodes, "edges": edges}

async def search_papers_by_embedding(memory_db, query: str, top_k: int = 10) -> list[dict]:
    """Real vector-based semantic search over stored papers."""
    if not memory_db.service:
        return []
    if not hasattr(memory_db, 'embedder') or not memory_db.embedder:
        return []
    import numpy as np
    import asyncio
    vec = await asyncio.to_thread(memory_db.embedder.encode, [query])
    query_emb = vec[0].tolist() if hasattr(vec[0], "tolist") else list(vec[0])
    async with memory_db.pg_pool.acquire() as conn:
        await register_vector(conn)
        rows = await conn.fetch(
            "SELECT ml.source_id AS paper_id, ml.metadata, em.text,",
            " (em.embedding <=> $1) AS distance",
            " FROM memory_links ml",
            " JOIN episodic_memory em ON em.id = ml.source_id",
            " WHERE ml.relation = 'is_a' AND ml.metadata->>'kind' = 'paper'",
            " ORDER BY em.embedding <=> $1",
            " LIMIT $2",
            query_emb, top_k,
        )
    results = []
    for r in rows:
        md = r["metadata"] or {}
        md["paper_id"] = r["paper_id"]
        md["score"] = 1.0 / (1.0 + float(r.get("distance") or 0.0))
        md["excerpt"] = (r["text"] or "")[:200]
        results.append(md)
    return results

