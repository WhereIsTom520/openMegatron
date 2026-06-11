import os
import sys
import json
import uuid
import time
import hashlib
import asyncio
import logging
from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime, timezone

import redis.asyncio as redis
import asyncpg
from pgvector.asyncpg import register_vector
from neo4j import AsyncGraphDatabase
from neo4j.exceptions import Neo4jError
from openai import AsyncOpenAI, APIError, APIConnectionError
from sentence_transformers import SentenceTransformer, CrossEncoder

try:
    import tomllib
except ImportError:
    import tomli as tomllib

os.environ["TQDM_DISABLE"] = "1"
os.environ["TRANSFORMERS_VERBOSITY"] = "error"
os.environ["TOKENIZERS_PARALLELISM"] = "false"

for lib in ["sentence_transformers", "transformers", "torch", "urllib3", "asyncpg", "neo4j"]:
    logging.getLogger(lib).setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


class CriticalDependencyError(Exception):
    pass


class TransientError(Exception):
    pass


class MemoryConsistencyError(Exception):
    pass


def utc_ts() -> float:
    return time.time()


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


def safe_json_loads(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    try:
        return json.loads(value)
    except Exception:
        return default


def decode_redis(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def stable_id(prefix: str, payload: Any) -> str:
    raw = json_dumps(payload) if not isinstance(payload, str) else payload
    return f"{prefix}_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:24]}"


def load_config(config_path: str = "model.toml") -> dict:
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Config not found: {config_path}")
    with open(config_path, "rb") as f:
        config_data = tomllib.load(f)
    llm_block = config_data.get("llm", {})
    active_provider = llm_block.get("active_provider", "openai")
    provider_config = llm_block.get(active_provider, {})
    return {
        "llm": {
            "api_key": provider_config.get("api_key", ""),
            "base_url": provider_config.get("base_url"),
            "model": provider_config.get("model", "gpt-4o-mini"),
            "extra_params": provider_config.get("extra_params", {})
        },
        "neo4j": config_data.get("neo4j", {}),
        "redis": config_data.get("redis", {}),
        "postgres": config_data.get("postgres", {}),
        "embedding": config_data.get("embedding", {}),
        "rerank": config_data.get("rerank", {})
    }


class MemoryService:
    _embed_semaphore = asyncio.Semaphore(4)

    def __init__(self, pg_pool, neo4j_driver, redis_client, config: dict, embedder=None,
                 graph_engine=None, cache_engine=None):
        self.pg_pool = pg_pool
        self.neo4j_driver = neo4j_driver
        self.redis = redis_client
        self.config = config
        self.embedder = embedder
        self.embed_dim = int(config.get("embedding", {}).get("dim", 1024))
        # Multi-engine architecture
        self.graph = graph_engine   # Neo4j — graph traversals
        self.cache = cache_engine   # Redis — query cache + pub/sub
        memory_cfg = config.get("memory", {})
        runtime_cfg = config.get("runtime", {})
        self.short_term_ttl = int(memory_cfg.get("short_term_ttl", 86400))
        self.short_term_max = int(memory_cfg.get("short_term_max", 80))
        self.link_expansion_limit = int(runtime_cfg.get("amem_neighbor_expansion_limit", memory_cfg.get("neighbor_expansion_limit", 0)))
        self.link_confidence_threshold = float(runtime_cfg.get("amem_link_confidence_threshold", memory_cfg.get("link_confidence_threshold", 0.72)))

    def _zero_vector(self) -> List[float]:
        return [0.0] * self.embed_dim

    async def embed_text(self, text: str) -> List[float]:
        if not self.embedder:
            return self._zero_vector()
        async with self._embed_semaphore:
            try:
                vec = await asyncio.to_thread(self.embedder.encode, [text or ""])
                return vec[0].tolist()
            except Exception as e:
                logger.error(f"Embedding failed: {e}")
                return self._zero_vector()

    async def embed_many(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        if not self.embedder:
            return [self._zero_vector() for _ in texts]
        async with self._embed_semaphore:
            try:
                vecs = await asyncio.to_thread(self.embedder.encode, texts)
                return vecs.tolist()
            except Exception as e:
                logger.error(f"Batch embedding failed: {e}")
                return [self._zero_vector() for _ in texts]

    async def _enqueue_reconciliation(self, session_id: str, action: str, payload: dict = None):
        try:
            await self.redis.lpush("reconciliation_queue", json_dumps({
                "sid": session_id,
                "action": action,
                "payload": payload or {},
                "timestamp": utc_ts()
            }))
            logger.warning(f"Enqueued reconciliation task for {session_id}: {action}")
        except Exception as e:
            logger.error(f"Failed to enqueue reconciliation task: {e}")

    async def add_short_term(self, session_id: str, text: str, owner_id: str = "shared", scope: str = "shared"):
        try:
            key = f"chat:{scope}:{session_id}" if scope == "shared" else f"chat:private:{owner_id}:{session_id}"
            payload = json_dumps({
                "text": text,
                "owner_id": owner_id,
                "scope": scope,
                "timestamp": utc_ts()
            })
            await self.redis.rpush(key, payload)
            await self.redis.ltrim(key, -self.short_term_max, -1)
            await self.redis.expire(key, self.short_term_ttl)
        except Exception as e:
            logger.error(f"Redis short-term write failed: {e}")

    async def get_short_term(self, session_id: str, owner_id: str = "shared", limit: int = 30) -> List[str]:
        try:
            shared = await self.redis.lrange(f"chat:shared:{session_id}", -limit, -1)
            private = []
            if owner_id and owner_id != "shared":
                private = await self.redis.lrange(f"chat:private:{owner_id}:{session_id}", -limit, -1)
            results = []
            for item in shared + private:
                decoded = decode_redis(item)
                data = safe_json_loads(decoded)
                if isinstance(data, dict) and "text" in data:
                    results.append(data["text"])
                else:
                    results.append(str(decoded))
            return results
        except Exception as e:
            logger.error(f"Redis short-term read failed: {e}")
            return []

    async def add_memory(self, session_id: str, text: str, owner_id: str = "shared", scope: str = "shared", entities: List[str] = None, metadata: dict = None, memory_id: str = None):
        entities = entities or []
        metadata = metadata or {}
        episodic_id = memory_id or stable_id("mem", {"session_id": session_id, "text": text, "owner_id": owner_id, "scope": scope, "time": utc_iso()})
        vector = await self.embed_text(text)
        try:
            async with self.pg_pool.acquire() as conn:
                await register_vector(conn)
                await conn.execute(
                    """
                    INSERT INTO episodic_memory (id, text, embedding, owner_id, scope, session_id, metadata, created_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, NOW())
                    ON CONFLICT (id) DO UPDATE SET
                        text = EXCLUDED.text,
                        embedding = EXCLUDED.embedding,
                        owner_id = EXCLUDED.owner_id,
                        scope = EXCLUDED.scope,
                        session_id = EXCLUDED.session_id,
                        metadata = EXCLUDED.metadata
                    """,
                    episodic_id,
                    text,
                    vector,
                    owner_id,
                    scope,
                    session_id,
                    json_dumps(metadata)
                )
                for entity in entities:
                    if not entity:
                        continue
                    await conn.execute(
                        """
                        INSERT INTO topic_index (entity, topic, episodic_id, text, session_id, created_at)
                        VALUES ($1, $2, $3, $4, $5, NOW())
                        """,
                        str(entity),
                        str(metadata.get("topic", "")),
                        episodic_id,
                        text,
                        session_id
                    )
            return episodic_id
        except Exception as e:
            logger.error(f"PG memory add failed: {e}")
            raise TransientError("Database write failed")

    async def delete_memory(self, session_id: str):
        try:
            async with self.pg_pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute(
                        """
                        WITH doomed AS (
                            SELECT id FROM episodic_memory WHERE session_id = $1 OR id LIKE $2
                        )
                        DELETE FROM memory_links
                        WHERE source_id IN (SELECT id FROM doomed) OR target_id IN (SELECT id FROM doomed)
                        """,
                        session_id,
                        f"{session_id}_%"
                    )
                    await conn.execute(
                        """
                        WITH doomed AS (
                            SELECT id FROM episodic_memory WHERE session_id = $1 OR id LIKE $2
                        )
                        DELETE FROM memory_evolution_log
                        WHERE source_id IN (SELECT id FROM doomed) OR target_id IN (SELECT id FROM doomed)
                        """,
                        session_id,
                        f"{session_id}_%"
                    )
                    await conn.execute("DELETE FROM topic_index WHERE session_id = $1", session_id)
                    await conn.execute("DELETE FROM episodic_memory WHERE session_id = $1 OR id LIKE $2", session_id, f"{session_id}_%")
            if self.neo4j_driver:
                async with self.neo4j_driver.session() as session:
                    await session.run("MATCH (n {session_id: $sid}) DETACH DELETE n", sid=session_id)
        except Neo4jError as e:
            logger.error(f"Neo4j delete failed: {e}")
            await self._enqueue_reconciliation(session_id, "clean_neo4j")
            raise MemoryConsistencyError("Graph DB sync failed")
        except Exception as e:
            logger.error(f"PG delete failed: {e}")
            raise TransientError("Relational DB sync failed")

    async def get_episodic(self, query_emb: List[float], owner_id: str = "shared", limit: int = 5, link_expansion_limit: Optional[int] = None) -> List[str]:
        if not self.pg_pool:
            return []
        try:
            async with self.pg_pool.acquire() as conn:
                await register_vector(conn)
                rows = await conn.fetch(
                    """
                    SELECT id, text
                    FROM episodic_memory
                    WHERE scope = 'shared' OR owner_id = $1
                    ORDER BY embedding <=> $2
                    LIMIT $3
                    """,
                    owner_id,
                    query_emb,
                    limit
                )
                texts = [r["text"] for r in rows]
                expansion_limit = self.link_expansion_limit if link_expansion_limit is None else max(0, int(link_expansion_limit))
                linked = await self._get_linked_memory_records(conn, [r["id"] for r in rows], owner_id, expansion_limit)
                for row in linked:
                    relation = row.get("relation") or "related"
                    text = row.get("text") or ""
                    if text:
                        texts.append(f"[linked:{relation}] {text}")
                return list(dict.fromkeys(texts))
        except Exception as e:
            logger.error(f"PG vector search failed: {e}")
            return []

    async def _get_linked_memory_records(self, conn, source_ids: List[str], owner_id: str = "shared", limit: int = 4) -> List[dict]:
        source_ids = [str(x) for x in source_ids if x]
        if not source_ids or limit <= 0:
            return []
        try:
            rows = await conn.fetch(
                """
                WITH selected_links AS (
                    SELECT
                        source_id,
                        target_id,
                        relation,
                        confidence,
                        reason,
                        CASE WHEN source_id = ANY($1::varchar[]) THEN target_id ELSE source_id END AS linked_id
                    FROM memory_links
                    WHERE (source_id = ANY($1::varchar[]) OR target_id = ANY($1::varchar[]))
                      AND confidence >= $3
                )
                SELECT
                    l.source_id,
                    l.target_id,
                    l.relation,
                    l.confidence,
                    l.reason,
                    m.id,
                    m.text,
                    m.owner_id,
                    m.scope,
                    m.session_id,
                    m.metadata,
                    m.created_at
                FROM selected_links l
                JOIN episodic_memory m ON m.id = l.linked_id
                WHERE (m.scope = 'shared' OR m.owner_id = $2)
                ORDER BY l.confidence DESC, m.created_at DESC
                LIMIT $4
                """,
                source_ids,
                owner_id,
                self.link_confidence_threshold,
                limit
            )
            seen = set(source_ids)
            results = []
            for row in rows:
                item = dict(row)
                if item.get("id") in seen:
                    continue
                seen.add(item.get("id"))
                item["linked"] = True
                results.append(item)
            return results
        except Exception as e:
            logger.warning(f"A-MEM linked memory expansion failed: {e}")
            return []

    async def get_episodic_records(self, query_emb: List[float], owner_id: str = "shared", limit: int = 5, link_expansion_limit: Optional[int] = None) -> List[dict]:
        if not self.pg_pool:
            return []
        try:
            async with self.pg_pool.acquire() as conn:
                await register_vector(conn)
                rows = await conn.fetch(
                    """
                    SELECT id, text, owner_id, scope, session_id, metadata, created_at, (embedding <=> $2) AS distance
                    FROM episodic_memory
                    WHERE scope = 'shared' OR owner_id = $1
                    ORDER BY embedding <=> $2
                    LIMIT $3
                    """,
                    owner_id,
                    query_emb,
                    limit
                )
                records = [dict(r) for r in rows]
                expansion_limit = self.link_expansion_limit if link_expansion_limit is None else max(0, int(link_expansion_limit))
                linked = await self._get_linked_memory_records(conn, [r["id"] for r in rows], owner_id, expansion_limit)
                records.extend(linked)
                return records
        except Exception as e:
            logger.error(f"PG episodic record search failed: {e}")
            return []

    async def get_graph_context(self, entities: List[str]) -> List[str]:
        if not self.neo4j_driver or not entities:
            return []
        results = []
        try:
            async with self.neo4j_driver.session() as session:
                for ent in entities:
                    res = await session.run(
                        """
                        MATCH (n)-[r]-(m)
                        WHERE toLower(coalesce(n.id, n.name, '')) CONTAINS toLower($ent)
                        RETURN coalesce(n.id, n.name) AS source, type(r) AS rel, coalesce(m.id, m.name) AS target
                        LIMIT 5
                        """,
                        ent=str(ent)
                    )
                    async for row in res:
                        results.append(f"{row['source']}-{row['rel']}->{row['target']}")
            return list(dict.fromkeys(results))
        except Exception as e:
            logger.error(f"Neo4j query failed: {e}")
            return []

    async def add_workflow_pattern(self, pattern: dict) -> str:
        if not isinstance(pattern, dict):
            raise ValueError("pattern must be a dict")
        goal_type = str(pattern.get("goal_type") or "general_task").strip() or "general_task"
        successful_chain = pattern.get("successful_chain") or pattern.get("chain") or []
        if not isinstance(successful_chain, list):
            successful_chain = [str(successful_chain)]
        preconditions = pattern.get("preconditions") or []
        if not isinstance(preconditions, list):
            preconditions = [str(preconditions)]
        notes = str(pattern.get("notes") or pattern.get("summary") or "")
        examples = pattern.get("example_user_goals") or []
        if isinstance(examples, str):
            examples = [examples]
        consumes = pattern.get("consumes") or {}
        produces = pattern.get("produces") or {}
        metadata = pattern.get("metadata") or {}
        summary = "\n".join([
            f"goal_type: {goal_type}",
            f"preconditions: {json_dumps(preconditions)}",
            f"successful_chain: {json_dumps(successful_chain)}",
            f"notes: {notes}",
            f"examples: {json_dumps(examples)}"
        ])
        pattern_id = pattern.get("id") or stable_id("wf", {"goal_type": goal_type, "chain": successful_chain, "preconditions": preconditions})
        vector = await self.embed_text(summary)
        success_count = int(pattern.get("success_count", 1 if pattern.get("success", True) else 0))
        failure_count = int(pattern.get("failure_count", 0 if pattern.get("success", True) else 1))
        try:
            async with self.pg_pool.acquire() as conn:
                await register_vector(conn)
                existing = await conn.fetchrow("SELECT * FROM workflow_patterns WHERE id=$1", pattern_id)
                if existing:
                    old_examples = existing["example_user_goals"] or []
                    merged_examples = []
                    for item in list(old_examples) + list(examples):
                        if item and item not in merged_examples:
                            merged_examples.append(item)
                    await conn.execute(
                        """
                        UPDATE workflow_patterns
                        SET goal_type=$2,
                            summary=$3,
                            preconditions=$4::jsonb,
                            successful_chain=$5::jsonb,
                            notes=$6,
                            consumes=$7::jsonb,
                            produces=$8::jsonb,
                            metadata=$9::jsonb,
                            success_count=success_count + $10,
                            failure_count=failure_count + $11,
                            example_user_goals=$12::jsonb,
                            embedding=$13,
                            updated_at=NOW()
                        WHERE id=$1
                        """,
                        pattern_id,
                        goal_type,
                        summary,
                        json_dumps(preconditions),
                        json_dumps(successful_chain),
                        notes,
                        json_dumps(consumes),
                        json_dumps(produces),
                        json_dumps(metadata),
                        success_count,
                        failure_count,
                        json_dumps(merged_examples[:12]),
                        vector
                    )
                else:
                    await conn.execute(
                        """
                        INSERT INTO workflow_patterns
                        (id, goal_type, summary, preconditions, successful_chain, notes, consumes, produces, metadata, success_count, failure_count, example_user_goals, embedding, created_at, updated_at)
                        VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6, $7::jsonb, $8::jsonb, $9::jsonb, $10, $11, $12::jsonb, $13, NOW(), NOW())
                        """,
                        pattern_id,
                        goal_type,
                        summary,
                        json_dumps(preconditions),
                        json_dumps(successful_chain),
                        notes,
                        json_dumps(consumes),
                        json_dumps(produces),
                        json_dumps(metadata),
                        success_count,
                        failure_count,
                        json_dumps(examples[:12]),
                        vector
                    )
                await conn.execute("DELETE FROM workflow_steps WHERE pattern_id=$1", pattern_id)
                for idx, step in enumerate(successful_chain):
                    if isinstance(step, dict):
                        tool_name = step.get("tool") or step.get("tool_name") or ""
                        skill_name = step.get("skill") or step.get("skill_name") or ""
                        step_consumes = step.get("consumes") or {}
                        step_produces = step.get("produces") or {}
                    else:
                        tool_name = str(step)
                        skill_name = str(step)
                        step_consumes = {}
                        step_produces = {}
                    await conn.execute(
                        """
                        INSERT INTO workflow_steps (pattern_id, step_index, tool_name, skill_name, consumes, produces, success_count, failure_count, created_at)
                        VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7, $8, NOW())
                        """,
                        pattern_id,
                        idx,
                        tool_name,
                        skill_name,
                        json_dumps(step_consumes),
                        json_dumps(step_produces),
                        success_count,
                        failure_count
                    )
            await self._sync_workflow_graph(pattern_id, goal_type, successful_chain, consumes, produces)
            await self.redis.hset("workflow_patterns_cache", pattern_id, json_dumps({
                "id": pattern_id,
                "goal_type": goal_type,
                "successful_chain": successful_chain,
                "notes": notes,
                "success_count": success_count,
                "failure_count": failure_count,
                "example_user_goals": examples[:5],
                "updated_at": utc_ts()
            }))
            await self.redis.expire("workflow_patterns_cache", 86400 * 90)
            return pattern_id
        except Exception as e:
            logger.error(f"Workflow pattern add failed: {e}")
            raise TransientError("Workflow pattern write failed")

    async def search_workflow_patterns(self, query: str, query_emb: Optional[List[float]] = None, limit: int = 5) -> List[dict]:
        if not self.pg_pool:
            return []
        vector = query_emb or await self.embed_text(query)
        try:
            async with self.pg_pool.acquire() as conn:
                await register_vector(conn)
                rows = await conn.fetch(
                    """
                    SELECT id, goal_type, summary, preconditions, successful_chain, notes, consumes, produces, metadata,
                           success_count, failure_count, example_user_goals, created_at, updated_at,
                           (embedding <=> $1) AS distance
                    FROM workflow_patterns
                    ORDER BY embedding <=> $1
                    LIMIT $2
                    """,
                    vector,
                    limit
                )
                patterns = []
                for row in rows:
                    item = dict(row)
                    item["score"] = 1.0 / (1.0 + float(item.get("distance") or 0.0))
                    patterns.append(item)
                return patterns
        except Exception as e:
            logger.error(f"Workflow pattern search failed: {e}")
            return []

    async def update_workflow_pattern_stats(self, pattern_id: str, success_delta: int = 0, failure_delta: int = 0, notes: str = None):
        if not pattern_id:
            return
        try:
            async with self.pg_pool.acquire() as conn:
                if notes:
                    await conn.execute(
                        """
                        UPDATE workflow_patterns
                        SET success_count = success_count + $2,
                            failure_count = failure_count + $3,
                            notes = CASE WHEN notes IS NULL OR notes = '' THEN $4 ELSE notes || E'\n' || $4 END,
                            updated_at = NOW()
                        WHERE id=$1
                        """,
                        pattern_id,
                        success_delta,
                        failure_delta,
                        notes
                    )
                else:
                    await conn.execute(
                        """
                        UPDATE workflow_patterns
                        SET success_count = success_count + $2,
                            failure_count = failure_count + $3,
                            updated_at = NOW()
                        WHERE id=$1
                        """,
                        pattern_id,
                        success_delta,
                        failure_delta
                    )
        except Exception as e:
            logger.error(f"Workflow stats update failed: {e}")

    async def _sync_workflow_graph(self, pattern_id: str, goal_type: str, chain: List[Any], consumes: Any, produces: Any):
        if not self.neo4j_driver:
            return
        try:
            async with self.neo4j_driver.session() as session:
                await session.run(
                    """
                    MERGE (p:WorkflowPattern {id:$pattern_id})
                    SET p.goal_type=$goal_type, p.updated_at=$updated_at
                    """,
                    pattern_id=pattern_id,
                    goal_type=goal_type,
                    updated_at=utc_iso()
                )
                previous = None
                for idx, step in enumerate(chain):
                    if isinstance(step, dict):
                        skill_name = str(step.get("skill") or step.get("skill_name") or step.get("tool") or step.get("tool_name") or f"step_{idx}")
                    else:
                        skill_name = str(step)
                    await session.run(
                        """
                        MERGE (s:Skill {id:$skill_name})
                        MERGE (p:WorkflowPattern {id:$pattern_id})
                        MERGE (p)-[r:USES {step_index:$idx}]->(s)
                        SET r.updated_at=$updated_at
                        """,
                        skill_name=skill_name,
                        pattern_id=pattern_id,
                        idx=idx,
                        updated_at=utc_iso()
                    )
                    if previous:
                        await session.run(
                            """
                            MERGE (a:Skill {id:$previous})
                            MERGE (b:Skill {id:$current})
                            MERGE (a)-[r:CAN_FEED]->(b)
                            SET r.pattern_id=$pattern_id, r.updated_at=$updated_at
                            """,
                            previous=previous,
                            current=skill_name,
                            pattern_id=pattern_id,
                            updated_at=utc_iso()
                        )
                    previous = skill_name
        except Exception as e:
            logger.error(f"Workflow graph sync failed: {e}")
            await self._enqueue_reconciliation(pattern_id, "sync_workflow_graph", {"goal_type": goal_type})

    async def add_skill_metadata(
        self,
        skill_name: str,
        description: str,
        parameters: dict,
        success_count: int = 0,
        failure_count: int = 0,
        related_tasks: List[str] = None,
        session_id: str = None
    ) -> str:
        metadata = {
            "type": "skill_metadata",
            "skill_name": skill_name,
            "description": description,
            "parameters": parameters,
            "success_count": success_count,
            "failure_count": failure_count,
            "related_tasks": related_tasks or [],
            "created_by": session_id or "auto_generate"
        }
        memory_id = f"skill_meta_{skill_name}"
        text = f"Skill: {skill_name}\nDescription: {description}\nParameters: {json.dumps(parameters, ensure_ascii=False)}\nRelated tasks: {', '.join(related_tasks or [])}"
        try:
            vector = await self.embed_text(text)
            async with self.pg_pool.acquire() as conn:
                await register_vector(conn)
                await conn.execute(
                    """
                    INSERT INTO episodic_memory (id, text, embedding, owner_id, scope, session_id, metadata, created_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, NOW())
                    ON CONFLICT (id) DO UPDATE SET
                        text = EXCLUDED.text,
                        embedding = EXCLUDED.embedding,
                        metadata = EXCLUDED.metadata,
                        created_at = NOW()
                    """,
                    memory_id,
                    text,
                    vector,
                    "system",
                    "shared",
                    session_id or "system",
                    json_dumps(metadata)
                )
            return memory_id
        except Exception as e:
            logger.error(f"Failed to add skill metadata: {e}")
            raise TransientError("Skill metadata write failed")

    async def record_reminder_history(
        self,
        session_id: str,
        prompt: str,
        cron_expr: str,
        channel: str,
        triggered: bool = True,
        result: str = None
    ) -> str:
        metadata = {
            "type": "reminder_history",
            "cron_expr": cron_expr,
            "channel": channel,
            "triggered": triggered,
            "result": result
        }
        text = f"Reminder: {prompt} scheduled at {cron_expr} via {channel}"
        vector = await self.embed_text(text)
        memory_id = f"reminder_{session_id}_{int(time.time())}"
        async with self.pg_pool.acquire() as conn:
            await register_vector(conn)
            await conn.execute(
                """
                INSERT INTO episodic_memory (id, text, embedding, owner_id, scope, session_id, metadata, created_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, NOW())
                """,
                memory_id,
                text,
                vector,
                session_id,
                "private",
                session_id,
                json_dumps(metadata)
            )
        return memory_id




    async def _upsert_ontology_node(self, conn, node_id, kind, label, properties=None):
        import json as _j
        d = chr(36)
        await conn.execute(
            "INSERT INTO ontology_nodes (id, kind, label, properties, created_at, updated_at) VALUES ("
            + d + "1, " + d + "2, " + d + "3, " + d + "4::jsonb, NOW(), NOW())"
            + " ON CONFLICT (id) DO UPDATE SET"
            + " kind = EXCLUDED.kind, label = EXCLUDED.label, properties = EXCLUDED.properties, updated_at = NOW()",
            node_id, kind, label[:500], _j.dumps(properties or {})
        )
        # Mirror to Neo4j for native graph traversals
        if self.graph and self.graph.is_available():
            await self.graph.upsert_node(node_id, kind, label, properties)

    async def _upsert_link(self, conn, source_id, target_id, relation, confidence=1.0, metadata=None):
        """Write a memory_link to PostgreSQL AND mirror to Neo4j."""
        import json as _j
        d = chr(36)
        await conn.execute(
            "INSERT INTO memory_links (source_id, target_id, relation, confidence, metadata, created_at) VALUES ("
            + d + "1, " + d + "2, " + d + "3, " + d + "4, " + d + "5::jsonb, NOW())"
            + " ON CONFLICT (source_id, target_id, relation) DO UPDATE SET"
            + " confidence = EXCLUDED.confidence, metadata = EXCLUDED.metadata",
            source_id, target_id, relation, confidence, _j.dumps(metadata or {})
        )
        # Mirror to Neo4j
        if self.graph and self.graph.is_available():
            await self.graph.upsert_edge(source_id, target_id, relation, confidence, metadata)

    async def _upsert_hyperedge_member(self, conn, hyperedge_id, node_id, role, node_kind, weight=1.0, metadata=None):
        import json as _j
        d = chr(36)
        await conn.execute(
            "INSERT INTO memory_hyperedge_members (hyperedge_id, node_id, role, weight, metadata, created_at) VALUES ("
            + d + "1, " + d + "2, " + d + "3, " + d + "4, " + d + "5::jsonb, NOW())"
            + " ON CONFLICT (hyperedge_id, node_id) DO UPDATE SET"
            + " role = EXCLUDED.role, weight = EXCLUDED.weight, metadata = EXCLUDED.metadata",
            hyperedge_id, node_id, role, weight, _j.dumps(metadata or {})
        )

    def ontology_node_id(self, kind, label):
        raw = f"onto:{kind}:{label}"
        return f"onto_{hashlib.sha256(raw.encode()).hexdigest()[:24]}"

    async def query_ontology_nodes(self, kind=None, limit=200):
        d = chr(36)
        async with self.pg_pool.acquire() as conn:
            if kind:
                rows = await conn.fetch(
                    "SELECT id, kind, label, properties, created_at FROM ontology_nodes WHERE kind = " + d + "1 ORDER BY created_at DESC LIMIT " + d + "2",
                    kind, limit
                )
            else:
                rows = await conn.fetch(
                    "SELECT id, kind, label, properties, created_at FROM ontology_nodes ORDER BY created_at DESC LIMIT " + d + "1",
                    limit
                )
        result = []
        for r in rows:
            p = r["properties"]
            result.append({"id": r["id"], "kind": r["kind"], "label": r["label"],
                "properties": json.loads(p) if isinstance(p, str) else (p or {}),
                "created_at": str(r["created_at"]) if r["created_at"] else None})
        return result

    async def query_hyperedges(self, edge_type=None, limit=100):
        d = chr(36)
        async with self.pg_pool.acquire() as conn:
            if edge_type:
                rows = await conn.fetch(
                    "SELECT id, edge_type, label, summary, confidence, metadata, created_at FROM memory_hyperedges WHERE edge_type = " + d + "1 ORDER BY created_at DESC LIMIT " + d + "2",
                    edge_type, limit
                )
            else:
                rows = await conn.fetch(
                    "SELECT id, edge_type, label, summary, confidence, metadata, created_at FROM memory_hyperedges ORDER BY created_at DESC LIMIT " + d + "1",
                    limit
                )
        result = []
        for r in rows:
            members = []
            async with self.pg_pool.acquire() as conn2:
                mrows = await conn2.fetch(
                    "SELECT mh.node_id, mh.role, mh.weight, mh.metadata, o.kind, o.label FROM memory_hyperedge_members mh LEFT JOIN ontology_nodes o ON o.id = mh.node_id WHERE mh.hyperedge_id = " + d + "1",
                    r["id"]
                )
                for mr in mrows:
                    members.append({"node_id": mr["node_id"], "role": mr["role"],
                        "kind": mr["kind"], "label": mr["label"], "weight": mr["weight"]})
            md = r["metadata"]
            result.append({"id": r["id"], "edge_type": r["edge_type"], "label": r["label"],
                "summary": r["summary"], "confidence": r["confidence"],
                "metadata": json.loads(md) if isinstance(md, str) else (md or {}),
                "members": members,
                "created_at": str(r["created_at"]) if r["created_at"] else None})
        return result

    async def query_hypergraph(self, limit_nodes=200, limit_edges=100):
        nodes = await self.query_ontology_nodes(limit=limit_nodes)
        hyperedges = await self.query_hyperedges(limit=limit_edges)
        edges = []
        for he in hyperedges:
            for m in he.get("members", []):
                edges.append({"id": he["id"] + "->" + m["node_id"],
                    "source": he["id"], "target": m["node_id"],
                    "type": "member", "label": m.get("role", "participant"),
                    "edge_type": he["edge_type"]})
        return {"nodes": nodes, "hyperedges": hyperedges, "edges": edges}

    # ── Neo4j-powered graph traversals (with Redis caching) ──

    async def graph_citation_path(self, from_id: str, to_id: str, max_depth: int = 5) -> dict:
        """Find shortest citation path between two papers (Neo4j native)."""
        if not self.graph or not self.graph.is_available():
            return {"found": False, "error": "Neo4j unavailable"}
        # Try Redis cache first
        cache_key = {"from": from_id, "to": to_id, "depth": max_depth}
        cached = await self.cache.get_cached_graph_query("citation_path", cache_key) if self.cache else None
        if cached:
            return cached
        result = await self.graph.citation_path(from_id, to_id, max_depth)
        if self.cache and result.get("found"):
            await self.cache.cache_graph_query("citation_path", cache_key, result, ttl=300)
        return result

    async def graph_cited_by_chain(self, paper_id: str, depth: int = 2) -> dict:
        """Multi-hop inbound citation chain (Neo4j native)."""
        if not self.graph or not self.graph.is_available():
            return {"error": "Neo4j unavailable"}
        cache_key = {"paper": paper_id, "depth": depth}
        cached = await self.cache.get_cached_graph_query("cited_by_chain", cache_key) if self.cache else None
        if cached:
            return cached
        result = await self.graph.cited_by_chain(paper_id, depth)
        if self.cache:
            await self.cache.cache_graph_query("cited_by_chain", cache_key, result, ttl=300)
        return result

    async def graph_coauthor_network(self, author: str, depth: int = 2) -> dict:
        """Co-authorship network (Neo4j native)."""
        if not self.graph or not self.graph.is_available():
            return {"error": "Neo4j unavailable"}
        cache_key = {"author": author, "depth": depth}
        cached = await self.cache.get_cached_graph_query("coauthor", cache_key) if self.cache else None
        if cached:
            return cached
        result = await self.graph.coauthor_network(author, depth)
        if self.cache:
            await self.cache.cache_graph_query("coauthor", cache_key, result, ttl=600)
        return result

    async def graph_influential_papers(self, topic: str = "", limit: int = 10) -> list:
        """Rank papers by citation count (Neo4j native)."""
        if not self.graph or not self.graph.is_available():
            return []
        cache_key = {"topic": topic, "limit": limit}
        cached = await self.cache.get_cached_graph_query("influential", cache_key) if self.cache else None
        if cached:
            return cached
        result = await self.graph.find_influential_papers(topic, limit)
        if self.cache:
            await self.cache.cache_graph_query("influential", cache_key, result, ttl=300)
        return result

    async def graph_research_gaps(self, keyword: str, limit: int = 10) -> list:
        """Find under-cited papers (Neo4j native)."""
        if not self.graph or not self.graph.is_available():
            return []
        result = await self.graph.find_research_gaps(keyword, limit)
        return result

class MemoryDatabases:
    def __init__(self, config: dict, embedder=None):
        self.cfg = config
        self.embedder = embedder
        self.embed_dim = int(config.get("embedding", {}).get("dim", 1024))
        redis_cfg = config.get("redis", {})
        redis_url = self._redis_url(redis_cfg)
        self.redis = redis.from_url(redis_url)
        self.pg_pool = None
        self.neo4j = None
        self.service = None
        try:
            neo4j_cfg = config.get("neo4j", {})
            self.neo4j = AsyncGraphDatabase.driver(
                neo4j_cfg.get("uri", "bolt://localhost:7687"),
                auth=(neo4j_cfg.get("user", "neo4j"), neo4j_cfg.get("password", "root"))
            )
        except Exception as e:
            logger.error(f"Neo4j init failed: {e}")

    def _redis_url(self, redis_cfg: dict) -> str:
        host = redis_cfg.get("host", "localhost")
        port = redis_cfg.get("port", 6379)
        password = redis_cfg.get("password", "")
        db = redis_cfg.get("db", 0)
        if password:
            return f"redis://:{password}@{host}:{port}/{db}"
        return f"redis://{host}:{port}/{db}"

    async def connect_pg(self):
        try:
            pg_cfg = self.cfg.get("postgres", {})
            pg_dsn = pg_cfg.get("dsn") or f"postgresql://{pg_cfg.get('user', 'postgres')}:{pg_cfg.get('password', 'password')}@{pg_cfg.get('host', 'localhost')}:{pg_cfg.get('port', 5432)}/{pg_cfg.get('database', 'postgres')}"
            self.pg_pool = await asyncpg.create_pool(pg_dsn, min_size=1, max_size=int(pg_cfg.get("pool_max_size", 10)))
            async with self.pg_pool.acquire() as conn:
                await self._init_schema(conn)
            # Initialize graph engine (Neo4j) and cache engine (Redis)
            from graph_engine import GraphEngine
            from cache_engine import CacheEngine
            self.graph_engine = GraphEngine(self.neo4j, self.pg_pool)
            self.cache_engine = CacheEngine(self.redis)
            if self.neo4j:
                await self.graph_engine.ensure_schema()
                logger.info("GraphEngine (Neo4j) initialized")
            logger.info("CacheEngine (Redis) initialized")
            # Pass all three engines to MemoryService
            self.service = MemoryService(
                self.pg_pool, self.neo4j, self.redis, self.cfg, self.embedder,
                graph_engine=self.graph_engine, cache_engine=self.cache_engine,
            )
        except Exception as e:
            logger.critical(f"Postgres connect critical failed: {e}")
            raise CriticalDependencyError("Database connection failed")

    async def _init_schema(self, conn):
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector;")

        await conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS episodic_memory (
                id VARCHAR PRIMARY KEY,
                text TEXT,
                embedding vector({self.embed_dim}),
                owner_id VARCHAR DEFAULT 'shared',
                scope VARCHAR DEFAULT 'shared',
                session_id VARCHAR,
                metadata JSONB DEFAULT '{{}}'::jsonb,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        await conn.execute("ALTER TABLE episodic_memory ADD COLUMN IF NOT EXISTS text TEXT;")
        await conn.execute(f"ALTER TABLE episodic_memory ADD COLUMN IF NOT EXISTS embedding vector({self.embed_dim});")
        await conn.execute("ALTER TABLE episodic_memory ADD COLUMN IF NOT EXISTS owner_id VARCHAR DEFAULT 'shared';")
        await conn.execute("ALTER TABLE episodic_memory ADD COLUMN IF NOT EXISTS scope VARCHAR DEFAULT 'shared';")
        await conn.execute("ALTER TABLE episodic_memory ADD COLUMN IF NOT EXISTS session_id VARCHAR;")
        await conn.execute("ALTER TABLE episodic_memory ADD COLUMN IF NOT EXISTS metadata JSONB DEFAULT '{}'::jsonb;")
        await conn.execute("ALTER TABLE episodic_memory ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;")

        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS topic_index (
                id SERIAL PRIMARY KEY,
                entity VARCHAR(255),
                topic VARCHAR(255),
                episodic_id VARCHAR(255),
                text TEXT,
                session_id VARCHAR(255),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        await conn.execute("ALTER TABLE topic_index ADD COLUMN IF NOT EXISTS entity VARCHAR(255);")
        await conn.execute("ALTER TABLE topic_index ADD COLUMN IF NOT EXISTS topic VARCHAR(255);")
        await conn.execute("ALTER TABLE topic_index ADD COLUMN IF NOT EXISTS episodic_id VARCHAR(255);")
        await conn.execute("ALTER TABLE topic_index ADD COLUMN IF NOT EXISTS text TEXT;")
        await conn.execute("ALTER TABLE topic_index ADD COLUMN IF NOT EXISTS session_id VARCHAR(255);")
        await conn.execute("ALTER TABLE topic_index ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;")

        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_links (
                source_id VARCHAR NOT NULL,
                target_id VARCHAR NOT NULL,
                relation VARCHAR DEFAULT 'related',
                confidence DOUBLE PRECISION DEFAULT 0.0,
                reason TEXT,
                metadata JSONB DEFAULT '{}'::jsonb,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (source_id, target_id, relation)
            );
            """
        )
        await conn.execute("ALTER TABLE memory_links ADD COLUMN IF NOT EXISTS source_id VARCHAR;")
        await conn.execute("ALTER TABLE memory_links ADD COLUMN IF NOT EXISTS target_id VARCHAR;")
        await conn.execute("ALTER TABLE memory_links ADD COLUMN IF NOT EXISTS relation VARCHAR DEFAULT 'related';")
        await conn.execute("ALTER TABLE memory_links ADD COLUMN IF NOT EXISTS confidence DOUBLE PRECISION DEFAULT 0.0;")
        await conn.execute("ALTER TABLE memory_links ADD COLUMN IF NOT EXISTS reason TEXT;")
        await conn.execute("ALTER TABLE memory_links ADD COLUMN IF NOT EXISTS metadata JSONB DEFAULT '{}'::jsonb;")
        await conn.execute("ALTER TABLE memory_links ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;")

        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_evolution_log (
                id VARCHAR PRIMARY KEY,
                source_id VARCHAR NOT NULL,
                target_id VARCHAR,
                action VARCHAR,
                reason TEXT,
                suggested_metadata JSONB DEFAULT '{}'::jsonb,
                applied BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        await conn.execute("ALTER TABLE memory_evolution_log ADD COLUMN IF NOT EXISTS source_id VARCHAR;")
        await conn.execute("ALTER TABLE memory_evolution_log ADD COLUMN IF NOT EXISTS target_id VARCHAR;")
        await conn.execute("ALTER TABLE memory_evolution_log ADD COLUMN IF NOT EXISTS action VARCHAR;")
        await conn.execute("ALTER TABLE memory_evolution_log ADD COLUMN IF NOT EXISTS reason TEXT;")
        await conn.execute("ALTER TABLE memory_evolution_log ADD COLUMN IF NOT EXISTS suggested_metadata JSONB DEFAULT '{}'::jsonb;")
        await conn.execute("ALTER TABLE memory_evolution_log ADD COLUMN IF NOT EXISTS applied BOOLEAN DEFAULT FALSE;")
        await conn.execute("ALTER TABLE memory_evolution_log ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;")

        await conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS workflow_patterns (
                id VARCHAR PRIMARY KEY,
                goal_type TEXT,
                summary TEXT,
                preconditions JSONB DEFAULT '[]'::jsonb,
                successful_chain JSONB DEFAULT '[]'::jsonb,
                notes TEXT,
                consumes JSONB DEFAULT '{{}}'::jsonb,
                produces JSONB DEFAULT '{{}}'::jsonb,
                metadata JSONB DEFAULT '{{}}'::jsonb,
                success_count INTEGER DEFAULT 0,
                failure_count INTEGER DEFAULT 0,
                example_user_goals JSONB DEFAULT '[]'::jsonb,
                embedding vector({self.embed_dim}),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        await conn.execute("ALTER TABLE workflow_patterns ADD COLUMN IF NOT EXISTS goal_type TEXT;")
        await conn.execute("ALTER TABLE workflow_patterns ADD COLUMN IF NOT EXISTS summary TEXT;")
        await conn.execute("ALTER TABLE workflow_patterns ADD COLUMN IF NOT EXISTS preconditions JSONB DEFAULT '[]'::jsonb;")
        await conn.execute("ALTER TABLE workflow_patterns ADD COLUMN IF NOT EXISTS successful_chain JSONB DEFAULT '[]'::jsonb;")
        await conn.execute("ALTER TABLE workflow_patterns ADD COLUMN IF NOT EXISTS notes TEXT;")
        await conn.execute("ALTER TABLE workflow_patterns ADD COLUMN IF NOT EXISTS consumes JSONB DEFAULT '{}'::jsonb;")
        await conn.execute("ALTER TABLE workflow_patterns ADD COLUMN IF NOT EXISTS produces JSONB DEFAULT '{}'::jsonb;")
        await conn.execute("ALTER TABLE workflow_patterns ADD COLUMN IF NOT EXISTS metadata JSONB DEFAULT '{}'::jsonb;")
        await conn.execute("ALTER TABLE workflow_patterns ADD COLUMN IF NOT EXISTS success_count INTEGER DEFAULT 0;")
        await conn.execute("ALTER TABLE workflow_patterns ADD COLUMN IF NOT EXISTS failure_count INTEGER DEFAULT 0;")
        await conn.execute("ALTER TABLE workflow_patterns ADD COLUMN IF NOT EXISTS example_user_goals JSONB DEFAULT '[]'::jsonb;")
        await conn.execute(f"ALTER TABLE workflow_patterns ADD COLUMN IF NOT EXISTS embedding vector({self.embed_dim});")
        await conn.execute("ALTER TABLE workflow_patterns ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;")
        await conn.execute("ALTER TABLE workflow_patterns ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;")

        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS workflow_steps (
                id SERIAL PRIMARY KEY,
                pattern_id VARCHAR,
                step_index INTEGER,
                tool_name TEXT,
                skill_name TEXT,
                consumes JSONB DEFAULT '{}'::jsonb,
                produces JSONB DEFAULT '{}'::jsonb,
                success_count INTEGER DEFAULT 0,
                failure_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        await conn.execute("ALTER TABLE workflow_steps ADD COLUMN IF NOT EXISTS pattern_id VARCHAR;")
        await conn.execute("ALTER TABLE workflow_steps ADD COLUMN IF NOT EXISTS step_index INTEGER;")
        await conn.execute("ALTER TABLE workflow_steps ADD COLUMN IF NOT EXISTS tool_name TEXT;")
        await conn.execute("ALTER TABLE workflow_steps ADD COLUMN IF NOT EXISTS skill_name TEXT;")
        await conn.execute("ALTER TABLE workflow_steps ADD COLUMN IF NOT EXISTS consumes JSONB DEFAULT '{}'::jsonb;")
        await conn.execute("ALTER TABLE workflow_steps ADD COLUMN IF NOT EXISTS produces JSONB DEFAULT '{}'::jsonb;")
        await conn.execute("ALTER TABLE workflow_steps ADD COLUMN IF NOT EXISTS success_count INTEGER DEFAULT 0;")
        await conn.execute("ALTER TABLE workflow_steps ADD COLUMN IF NOT EXISTS failure_count INTEGER DEFAULT 0;")
        await conn.execute("ALTER TABLE workflow_steps ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;")

        await conn.execute("CREATE INDEX IF NOT EXISTS idx_episodic_owner_scope ON episodic_memory(owner_id, scope);")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_episodic_session ON episodic_memory(session_id);")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_topic_entity ON topic_index(entity);")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_topic_session ON topic_index(session_id);")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_links_source ON memory_links(source_id);")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_links_target ON memory_links(target_id);")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_links_confidence ON memory_links(confidence);")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_evolution_source ON memory_evolution_log(source_id);")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_evolution_target ON memory_evolution_log(target_id);")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_workflow_goal_type ON workflow_patterns(goal_type);")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_workflow_stats ON workflow_patterns(success_count, failure_count);")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_workflow_steps_pattern ON workflow_steps(pattern_id);")

        try:
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_episodic_embedding ON episodic_memory USING ivfflat (embedding vector_cosine_ops);")
        except Exception:
            pass

        try:
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_workflow_embedding ON workflow_patterns USING ivfflat (embedding vector_cosine_ops);")
        except Exception:
            pass

        # -- Ontology nodes --
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ontology_nodes (
                id VARCHAR PRIMARY KEY,
                kind VARCHAR(64) NOT NULL,
                label VARCHAR(500) NOT NULL,
                properties JSONB DEFAULT '{}'::jsonb,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_onto_kind ON ontology_nodes(kind);")
        # -- Hyperedge table --
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_hyperedges (
                id VARCHAR PRIMARY KEY,
                edge_type VARCHAR(64) NOT NULL,
                label VARCHAR(500),
                summary TEXT,
                confidence DOUBLE PRECISION DEFAULT 1.0,
                metadata JSONB DEFAULT '{}'::jsonb,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_hyper_type ON memory_hyperedges(edge_type);")
        # -- Hyperedge member table --
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_hyperedge_members (
                hyperedge_id VARCHAR NOT NULL REFERENCES memory_hyperedges(id) ON DELETE CASCADE,
                node_id VARCHAR NOT NULL REFERENCES ontology_nodes(id) ON DELETE CASCADE,
                role VARCHAR(64) DEFAULT 'participant',
                weight DOUBLE PRECISION DEFAULT 1.0,
                metadata JSONB DEFAULT '{}'::jsonb,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (hyperedge_id, node_id)
            );
            """
        )
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_member_node ON memory_hyperedge_members(node_id);")

    async def close(self):
        if self.redis:
            await self.redis.aclose()
        if self.neo4j:
            await self.neo4j.close()
        if self.pg_pool:
            await self.pg_pool.close()

    async def get_episodic_decay(self, query_emb: List[float], owner_id: str = "shared", limit: int = 5) -> List[str]:
        return await self.service.get_episodic(query_emb, owner_id, limit) if self.service else []

    async def get_semantic_graph(self, entities: List[str]) -> List[str]:
        return await self.service.get_graph_context(entities) if self.service else []

    async def add_memory(self, *args, **kwargs):
        if not self.service:
            raise CriticalDependencyError("Memory service not initialized")
        return await self.service.add_memory(*args, **kwargs)

    async def add_workflow_pattern(self, pattern: dict) -> str:
        if not self.service:
            raise CriticalDependencyError("Memory service not initialized")
        return await self.service.add_workflow_pattern(pattern)

    async def search_workflow_patterns(self, query: str, query_emb: Optional[List[float]] = None, limit: int = 5) -> List[dict]:
        if not self.service:
            return []
        return await self.service.search_workflow_patterns(query, query_emb, limit)

    async def update_workflow_pattern_stats(self, pattern_id: str, success_delta: int = 0, failure_delta: int = 0, notes: str = None):
        if self.service:
            await self.service.update_workflow_pattern_stats(pattern_id, success_delta, failure_delta, notes)

    async def add_skill_metadata(self, skill_name: str, description: str, parameters: dict,
                                 success_count: int = 0, failure_count: int = 0,
                                 related_tasks: List[str] = None, session_id: str = None) -> str:
        if not self.service:
            raise CriticalDependencyError("Memory service not initialized")
        return await self.service.add_skill_metadata(skill_name, description, parameters,
                                                      success_count, failure_count,
                                                      related_tasks, session_id)

    async def record_reminder_history(self, session_id: str, prompt: str, cron_expr: str,
                                      channel: str, triggered: bool = True, result: str = None) -> str:
        if not self.service:
            raise CriticalDependencyError("Memory service not initialized")
        return await self.service.record_reminder_history(session_id, prompt, cron_expr,
                                                          channel, triggered, result)


class MemoryRecallEngine:
    _embed_semaphore = asyncio.Semaphore(4)

    def __init__(self, config: dict):
        self.cfg = config
        llm_cfg = config["llm"].copy()
        self.model = llm_cfg.pop("model", "gpt-4o-mini")
        self.extra_params = llm_cfg.pop("extra_params", {})
        self.llm = AsyncOpenAI(**llm_cfg)
        embed_cfg = config.get("embedding", {})
        rerank_cfg = config.get("rerank", {})
        self.embed_dim = int(embed_cfg.get("dim", 1024))
        self.top_k = int(config.get("memory", {}).get("top_k", 5))
        memory_cfg = config.get("memory", {})
        runtime_cfg = config.get("runtime", {})
        self.max_auto_link_expansion = int(runtime_cfg.get("amem_auto_neighbor_expansion_limit", memory_cfg.get("auto_neighbor_expansion_limit", 2)))
        try:
            self.emb_model = SentenceTransformer(embed_cfg.get("model_path", "")) if embed_cfg.get("model_path") else None
            if self.emb_model:
                try:
                    self.embed_dim = int(self.emb_model.get_sentence_embedding_dimension())
                except Exception:
                    pass
            self.reranker = CrossEncoder(rerank_cfg.get("model_path", "")) if rerank_cfg.get("model_path") else None
        except Exception as e:
            logger.error(f"Model load failed: {e}")
            self.emb_model = None
            self.reranker = None
        self.db = MemoryDatabases(config, self.emb_model)

    async def initialize(self):
        await self.db.connect_pg()

    async def close(self):
        await self.db.close()
        await self.llm.close()

    async def embed_text(self, text: str) -> List[float]:
        if not self.emb_model:
            return [0.0] * self.embed_dim
        async with self._embed_semaphore:
            try:
                vec = await asyncio.to_thread(self.emb_model.encode, [text or ""])
                return vec[0].tolist()
            except Exception as e:
                logger.error(f"Engine embedding failed: {e}")
                return [0.0] * self.embed_dim

    async def _get_entities(self, query: str) -> List[str]:
        try:
            res = await self.llm.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "Return JSON only: {\"entities\": [\"...\"]}. Extract up to 5 concrete entities, tools, files, people, projects, or task concepts."},
                    {"role": "user", "content": query}
                ],
                response_format={"type": "json_object"},
                **self.extra_params
            )
            data = safe_json_loads(res.choices[0].message.content, {})
            entities = data.get("entities", [])
            return [str(x) for x in entities if x][:5] if isinstance(entities, list) else []
        except Exception as e:
            logger.error(f"Entity extraction failed: {e}")
            return []

    def _link_expansion_budget(self, query: str, entities: List[str]) -> int:
        if self.max_auto_link_expansion <= 0:
            return 0
        lowered = (query or "").lower()
        markers = [
            "关联", "关系", "脉络", "上下文", "之前", "历史", "长期", "记忆",
            "为什么", "原因", "因果", "演化", "多跳", "对比", "证据",
            "related", "relationship", "context", "history", "why", "cause", "evidence"
        ]
        score = 0
        if any(marker in lowered for marker in markers):
            score += 2
        if len(entities) >= 2:
            score += 1
        if len(query or "") >= 160:
            score += 1
        if score >= 3:
            return self.max_auto_link_expansion
        if score >= 2:
            return min(1, self.max_auto_link_expansion)
        return 0

    async def recall_context(self, query: str, session_id: str = "default", owner_id: str = "shared", limit: int = None) -> Dict[str, Any]:
        limit = limit or self.top_k
        query_vec = await self.embed_text(query)
        entities = await self._get_entities(query)
        link_expansion_limit = self._link_expansion_budget(query, entities)
        short_mem, episodic_records, graph_mem, workflow_patterns = await asyncio.gather(
            self.db.service.get_short_term(session_id, owner_id, limit=30) if self.db.service else asyncio.sleep(0, result=[]),
            self.db.service.get_episodic_records(query_vec, owner_id, limit, link_expansion_limit=link_expansion_limit) if self.db.service else asyncio.sleep(0, result=[]),
            self.db.service.get_graph_context(entities) if self.db.service else asyncio.sleep(0, result=[]),
            self.db.service.search_workflow_patterns(query, query_vec, limit) if self.db.service else asyncio.sleep(0, result=[])
        )
        episodic_texts = [r.get("text", "") for r in episodic_records]
        if self.reranker:
            candidates = episodic_texts + [p.get("summary", "") for p in workflow_patterns]
            try:
                scores = await asyncio.to_thread(self.reranker.predict, [[query, doc] for doc in candidates])
                ranked = sorted(zip(candidates, scores), key=lambda x: x[1], reverse=True)
                reranked = [doc for doc, _ in ranked[:limit]]
            except Exception:
                reranked = candidates[:limit]
        else:
            reranked = episodic_texts[:limit]
        return {
            "short_term": short_mem,
            "episodic": episodic_texts,
            "episodic_records": episodic_records,
            "graph": graph_mem,
            "workflow_patterns": workflow_patterns,
            "reranked": reranked,
            "entities": entities,
            "link_expansion_limit": link_expansion_limit
        }

    async def chat(self, session_id: str, query: str, owner_id: str = "shared", role_description: str = "AI") -> Dict[str, Any]:
        if self.db.service and not query.startswith("SYSTEM_INTERNAL"):
            await self.db.service.add_short_term(session_id, f"User: {query}", owner_id, "shared")
        context = await self.recall_context(query, session_id, owner_id, self.top_k)
        context_str = json_dumps({
            "short_term": context["short_term"],
            "episodic": context["episodic"],
            "graph": context["graph"],
            "workflow_patterns": [
                {
                    "goal_type": p.get("goal_type"),
                    "successful_chain": p.get("successful_chain"),
                    "notes": p.get("notes"),
                    "success_count": p.get("success_count"),
                    "failure_count": p.get("failure_count")
                }
                for p in context["workflow_patterns"]
            ]
        })
        prompt = f"Role: {role_description}\nContext:\n{context_str}\n\nQuery: {query}"
        try:
            res = await self.llm.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                **self.extra_params
            )
            answer = res.choices[0].message.content
        except APIConnectionError as e:
            logger.error(f"API connection error: {e}")
            raise TransientError("LLM Service Unavailable")
        except APIError as e:
            logger.error(f"LLM API error: {e}")
            answer = "Error processing request."
        if self.db.service:
            await self.db.service.add_short_term(session_id, f"AI: {answer}", owner_id, "shared")
        return {"answer": answer, "context": context_str, "memory": context}

    async def align_and_decide(self, session_id: str, query: str, agents: List[dict]) -> str:
        if not agents:
            return (await self.chat(session_id, query, "default", "General analyst")).get("answer", "")
        max_experts = int(self.cfg.get("runtime", {}).get("max_arbitration_experts", 6))
        agents = agents[:max_experts]
        tasks = [self.chat(session_id, query, a.get("id", f"expert_{idx}"), a.get("role", "Domain expert")) for idx, a in enumerate(agents)]
        try:
            raw_thoughts = await asyncio.gather(*tasks, return_exceptions=True)
            thoughts = []
            for idx, thought in enumerate(raw_thoughts):
                if isinstance(thought, Exception):
                    thoughts.append({"expert": agents[idx].get("id", f"expert_{idx}"), "error": str(thought)})
                else:
                    thoughts.append({"expert": agents[idx].get("id", f"expert_{idx}"), "opinion": thought})
            arb_prompt = (
                f"Query: {query}\n"
                f"Opinions: {json_dumps(thoughts)}\n"
                "Resolve conflicts, identify assumptions, and return the best actionable answer."
            )
            res = await self.llm.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "Chief Arbitrator"},
                    {"role": "user", "content": arb_prompt}
                ],
                **self.extra_params
            )
            return res.choices[0].message.content
        except Exception as e:
            logger.error(f"Arbitration failed: {e}")
            return "Arbitration failed."

    async def add_workflow_pattern(self, pattern: dict) -> str:
        return await self.db.add_workflow_pattern(pattern)

    async def search_workflow_patterns(self, query: str, limit: int = 5) -> List[dict]:
        query_vec = await self.embed_text(query)
        return await self.db.search_workflow_patterns(query, query_vec, limit)

    async def find_skill_by_name(self, skill_name: str) -> Optional[dict]:
        query = f"Skill: {skill_name}"
        vec = await self.embed_text(query)
        async with self.db.pg_pool.acquire() as conn:
            await register_vector(conn)
            row = await conn.fetchrow(
                """
                SELECT text, metadata, created_at
                FROM episodic_memory
                WHERE metadata->>'type' = 'skill_metadata' AND metadata->>'skill_name' = $1
                ORDER BY created_at DESC LIMIT 1
                """,
                skill_name
            )
            if row:
                return {
                    "text": row["text"],
                    "metadata": row["metadata"],
                    "created_at": row["created_at"]
                }
        return None


async def main():
    try:
        config = load_config("model.toml")
        engine = MemoryRecallEngine(config)
        await engine.initialize()
        res = await engine.chat("s1", "Hello, how are you?", "user1")
        print(res["answer"])
        await engine.close()
    except CriticalDependencyError:
        logger.critical("System shutting down due to critical errors.")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
