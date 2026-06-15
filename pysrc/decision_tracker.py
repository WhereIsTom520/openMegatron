"""
Decision Tracker v2 — ontology-guided hypergraph-based decision tracking.

Every decision is an ontology node (kind="decision"). The decision, its owner,
topic, chosen option, and alternatives are connected via a hyperedge of type
"decision_record". Conflict detection traverses the hypergraph instead of
doing ILIKE text matching.

Depends on: memory.py (ontology_nodes, memory_hyperedges, memory_links)
"""

from __future__ import annotations

import json
import hashlib
import logging
import re
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ── Data classes ──────────────────────────────────────

@dataclass
class DecisionRecord:
    id: str
    session_id: str
    owner_id: str
    owner_name: str
    topic: str
    question: str
    chosen: str
    alternatives: list[str] = field(default_factory=list)
    rationale: str = ""
    tags: list[str] = field(default_factory=list)
    created_at: str = ""
    scope: str = "project"
    hyperedge_id: str = ""


@dataclass
class ConflictResult:
    has_conflict: bool
    prior_decisions: list[DecisionRecord]
    conflict_description: str
    suggestion: str
    conflict_pairs: list[dict] = field(default_factory=list)


# ── Helper: ontology-stable IDs ───────────────────────

def _onto_id(kind: str, label: str) -> str:
    """Generate a stable ontology node id from kind + label."""
    safe_kind = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(kind).strip().lower()).strip("_") or "entity"
    clean = re.sub(r"\s+", " ", str(label)).strip()
    digest = hashlib.sha1(clean.encode("utf-8", errors="ignore")).hexdigest()[:12]
    return f"{safe_kind}:{digest}"


def _decision_id(owner_id: str, topic: str, chosen: str) -> str:
    """Stable decision ID — same owner+topic+chosen → same id."""
    payload = f"{owner_id}|{topic}|{chosen}"
    digest = hashlib.sha1(payload.encode()).hexdigest()[:12]
    return f"decision:{digest}"


# ── DecisionTracker (hypergraph-backed) ───────────────

class DecisionTracker:
    """Tracks decisions as ontology nodes in the memory hypergraph.

    Requires a memory_db with:
      - pg_pool (asyncpg Pool)
      - service._upsert_ontology_node(conn, id, kind, label, props)
      - service._upsert_hyperedge_member(conn, he_id, node_id, role, kind, weight, meta)
      - service.ontology_node_id(kind, label)
    """

    def __init__(self, memory_db_or_pool):
        # Accept either a memory_db instance or a raw asyncpg pool
        self._db = memory_db_or_pool
        if hasattr(memory_db_or_pool, 'service'):
            self._svc = memory_db_or_pool.service
            self._pool = memory_db_or_pool.pg_pool
        else:
            self._svc = None
            self._pool = memory_db_or_pool

    @property
    def service(self):
        return self._svc

    @property
    def pg_pool(self):
        return self._pool

    # ── Record a decision ──────────────────────────────

    async def record(
        self,
        session_id: str = "default",
        owner_id: str = "shared",
        owner_name: str = "",
        topic: str = "",
        question: str = "",
        chosen: str = "",
        alternatives: list[str] | None = None,
        rationale: str = "",
        tags: list[str] | None = None,
        scope: str = "project",
    ) -> str:
        """Record a decision into the hypergraph. Returns decision_id."""
        alternatives = alternatives or []
        tags = tags or []
        now_dt = datetime.now(timezone.utc)

        # Stable IDs
        decision_id = _decision_id(owner_id, topic, chosen)
        topic_id = _onto_id("topic", topic)
        chosen_id = _onto_id("option", f"{topic}:{chosen}")
        owner_node_id = _onto_id("owner", owner_id)

        if not self._svc:
            raise RuntimeError("DecisionTracker needs a memory_db with .service (ontology methods)")

        async with self.pg_pool.acquire() as conn:
            # 1. Upsert ontology nodes
            await self._svc._upsert_ontology_node(conn, decision_id, "decision",
                f"Decision: {topic} → {chosen}", {
                    "owner_id": owner_id,
                    "owner_name": owner_name,
                    "topic": topic,
                    "question": question,
                    "chosen": chosen,
                    "rationale": rationale,
                    "tags": tags,
                    "scope": scope,
                    "session_id": session_id,
                    "alternatives": alternatives,
                    "source": "decision_tracker",
                },
            )
            await self._svc._upsert_ontology_node(conn, topic_id, "topic", topic, {})
            await self._svc._upsert_ontology_node(conn, chosen_id, "alternative",
                chosen, {"topic": topic, "is_chosen": True},
            )
            await self._svc._upsert_ontology_node(conn, owner_node_id, "owner",
                owner_name or owner_id, {"owner_id": owner_id},
            )

            # 2. Create hyperedge connecting all roles
            he_id = f"he:decision:{decision_id.split(':')[1]}"
            import json as _j
            d = chr(36)  # dollar sign for asyncpg params
            await conn.execute(
                "INSERT INTO memory_hyperedges (id, edge_type, label, summary, confidence, metadata, created_at, updated_at) VALUES ("
                + d + "1, " + d + "2, " + d + "3, " + d + "4, " + d + "5, " + d + "6::jsonb, NOW(), NOW())"
                + " ON CONFLICT (id) DO UPDATE SET metadata = EXCLUDED.metadata, updated_at = NOW()",
                he_id, "decision_record", f"Decision: {topic[:80]}",
                _j.dumps({"topic": topic, "chosen": chosen, "owner": owner_name or owner_id, "alternatives_count": len(alternatives)}),
                1.0,
                _j.dumps({"decision_id": decision_id, "ontology": "ontology-guided-hypergraph-memory.v2"}),
            )

            # 3. Add hyperedge members
            await self._svc._upsert_hyperedge_member(conn, he_id, decision_id, "decision", "decision", 1.0, {"role": "decision"})
            await self._svc._upsert_hyperedge_member(conn, he_id, owner_node_id, "owner", "owner", 1.0, {"owner_name": owner_name or owner_id})
            await self._svc._upsert_hyperedge_member(conn, he_id, topic_id, "topic", "topic", 0.95, {"topic": topic})
            await self._svc._upsert_hyperedge_member(conn, he_id, chosen_id, "chosen", "alternative", 1.0, {"option": chosen, "is_chosen": True})

            # 4. Add alternatives as members and ontology nodes
            for alt in alternatives:
                if alt == chosen:
                    continue
                alt_id = _onto_id("alternative", f"{topic}:{alt}")
                await self._svc._upsert_ontology_node(conn, alt_id, "alternative",
                    alt, {"topic": topic, "is_chosen": False},
                )
                await self._svc._upsert_hyperedge_member(conn, he_id, alt_id, "alternative", "alternative", 0.5, {"option": alt, "is_chosen": False})

            # 5. Create pairwise links for fast traversal
            sql_link = ("INSERT INTO memory_links (source_id, target_id, relation, confidence, metadata, created_at) VALUES ("
                        + d + "1, " + d + "2, " + d + "3, " + d + "4, " + d + "5::jsonb, NOW())"
                        + " ON CONFLICT (source_id, target_id, relation) DO NOTHING")
            await conn.execute(sql_link, owner_node_id, decision_id, "decides", 1.0,
                              _j.dumps({"topic": topic, "chosen": chosen}))
            await conn.execute(sql_link, decision_id, chosen_id, "considers", 1.0,
                              _j.dumps({"is_chosen": True}))
            for alt in alternatives:
                if alt != chosen:
                    alt_id = _onto_id("alternative", f"{topic}:{alt}")
                    await conn.execute(sql_link, decision_id, alt_id, "considers", 0.5,
                                      _j.dumps({"is_chosen": False}))

            # 6. Record rationale as episodic memory for vector search
            if rationale:
                try:
                    await conn.execute(
                        "INSERT INTO episodic_memory (id, text, owner_id, scope, session_id, metadata, created_at, embedding) VALUES ("
                        + d + "1, " + d + "2, " + d + "3, " + d + "4, " + d + "5, " + d + "6::jsonb, NOW(), NULL::vector)",
                        f"mem:{decision_id}", f"[Decision] {topic}: {chosen}\n{rationale}",
                        owner_id, scope, session_id,
                        _j.dumps({"decision_id": decision_id, "topic": topic, "chosen": chosen}),
                    )
                except Exception:
                    pass  # episodic_memory without embedding is fine

        logger.info(
            "Decision recorded in hypergraph: [%s] %s chose '%s' for '%s' (he=%s)",
            decision_id[:16], owner_name or owner_id, chosen, topic, he_id[:24],
        )
        return decision_id

    # ── Detect conflicts via hypergraph traversal ──────

    async def detect_conflict(
        self,
        topic_hint: str = "",
        tags: list[str] | None = None,
        current_owner_id: str = "",
        scope: str = "project",
    ) -> ConflictResult:
        """Check whether a new request conflicts with prior decisions on the same topic.

        Traverses ontology_nodes → memory_links (decides/considers) to find
        previous decisions on related topics, then checks for cross-owner
        disagreements.
        """
        if not self.pg_pool:
            return ConflictResult(has_conflict=False, prior_decisions=[], conflict_description="", suggestion="")

        # Extract search tokens from topic hint
        search_terms: list[str] = []
        if topic_hint:
            words = topic_hint.lower().replace(",", " ").replace("，", " ").split()
            stop = {"the", "a", "an", "for", "to", "of", "in", "is", "it", "on", "and",
                     "这个", "那个", "一下", "帮我", "使用", "用", "的", "了", "是"}
            search_terms = [w for w in words if len(w) > 1 and w not in stop]
        if tags:
            search_terms.extend(tags)

        if not search_terms:
            return ConflictResult(has_conflict=False, prior_decisions=[], conflict_description="", suggestion="")

        async with self.pg_pool.acquire() as conn:
            # 1. Find matching topic nodes via ontology_nodes
            d = chr(36)
            conditions = []
            params: list[Any] = []
            idx = 1
            for term in search_terms:
                conditions.append(
                    "(o.kind = 'topic' AND o.label ILIKE " + d + str(idx) + ")"
                    + " OR (o.kind = 'decision' AND (o.label ILIKE " + d + str(idx)
                    + " OR (o.properties->>'topic') ILIKE " + d + str(idx) + "))"
                )
                params.append(f"%{term}%")
                idx += 1

            if not conditions:
                return ConflictResult(has_conflict=False, prior_decisions=[], conflict_description="", suggestion="")

            # 2. Find decisions linked to matching topics
            rows = await conn.fetch(
                "SELECT DISTINCT o.id, o.kind, o.label, o.properties, o.created_at FROM ontology_nodes o"
                " LEFT JOIN memory_links ml ON ml.source_id = o.id OR ml.target_id = o.id"
                " WHERE (" + " OR ".join(conditions) + ")"
                " AND o.kind IN ('decision', 'topic')"
                " ORDER BY o.created_at DESC NULLS LAST LIMIT 20",
                *params,
            )

            prior: list[DecisionRecord] = []
            seen_ids: set[str] = set()

            for row in rows:
                props = row["properties"] or {}
                if isinstance(props, str):
                    try:
                        props = json.loads(props)
                    except Exception:
                        props = {}

                if row["kind"] == "decision":
                    did = row["id"]
                    if did in seen_ids:
                        continue
                    seen_ids.add(did)
                    created = row.get("created_at")
                    if isinstance(created, datetime):
                        created = created.isoformat()
                    prior.append(DecisionRecord(
                        id=did,
                        session_id=props.get("session_id", ""),
                        owner_id=props.get("owner_id", ""),
                        owner_name=props.get("owner_name", ""),
                        topic=props.get("topic", ""),
                        question=props.get("question", ""),
                        chosen=props.get("chosen", ""),
                        alternatives=props.get("alternatives", []) if isinstance(props.get("alternatives"), list) else [],
                        rationale=props.get("rationale", ""),
                        tags=props.get("tags", []) if isinstance(props.get("tags"), list) else [],
                        created_at=created or "",
                        scope=props.get("scope", "project"),
                    ))

            if len(prior) < 2:
                return ConflictResult(has_conflict=False, prior_decisions=prior, conflict_description="", suggestion="")

            # 3. Check for conflicts: same topic, different owners, different choices
            conflict_pairs: list[dict] = []
            for i, d1 in enumerate(prior):
                for d2 in prior[i + 1:]:
                    # Two decisions on similar topics
                    topic1 = (d1.topic or "").lower()
                    topic2 = (d2.topic or "").lower()
                    same_topic = (
                        topic1 == topic2
                        or (topic1 and topic1 in topic2)
                        or (topic2 and topic2 in topic1)
                    )
                    different_owner = d1.owner_id != d2.owner_id and d2.owner_id != "shared" and d1.owner_id != "shared"
                    different_choice = d1.chosen != d2.chosen
                    if same_topic and different_owner and different_choice:
                        conflict_pairs.append({"decision_a": d1, "decision_b": d2})

            if not conflict_pairs:
                return ConflictResult(has_conflict=False, prior_decisions=prior, conflict_description="", suggestion="")

            # 4. Build human-readable conflict description
            descriptions: list[str] = []
            suggestions: list[str] = []
            seen_owners: set[str] = set()
            seen_choices: set[str] = set()

            for pair in conflict_pairs[:3]:
                a, b = pair["decision_a"], pair["decision_b"]
                a_who = a.owner_name or a.owner_id
                b_who = b.owner_name or b.owner_id
                descriptions.append(f"{a_who} 选择了「{a.chosen}」，而 {b_who} 选择了「{b.chosen}」（话题: {a.topic}）")
                seen_owners.add(a_who)
                seen_owners.add(b_who)
                seen_choices.add(a.chosen)
                seen_choices.add(b.chosen)

            who_list = "、".join(sorted(seen_owners)[:4])
            what_list = "、".join(sorted(seen_choices)[:4])
            suggestions.append(f"请决定采用哪个方案（{what_list}），并说明理由。")
            if current_owner_id:
                suggestions.append(f"当前由 {current_owner_id} 操作，如需沿用某人的方案请明确指定。")

            return ConflictResult(
                has_conflict=True,
                prior_decisions=prior,
                conflict_description="；".join(descriptions),
                suggestion=" ".join(suggestions),
                conflict_pairs=[
                    {"a": {"owner": p["decision_a"].owner_name or p["decision_a"].owner_id, "chosen": p["decision_a"].chosen},
                     "b": {"owner": p["decision_b"].owner_name or p["decision_b"].owner_id, "chosen": p["decision_b"].chosen}}
                    for p in conflict_pairs[:5]
                ],
            )

    # ── List decisions ─────────────────────────────────

    async def list(
        self, topic: str = "", owner_id: str = "", scope: str = "",
        limit: int = 50, offset: int = 0,
    ) -> list[DecisionRecord]:
        if not self.pg_pool:
            return []
        conditions = ["o.kind = 'decision'"]
        params: list[Any] = []
        idx = 1

        if topic:
            conditions.append("(o.properties->>'topic') ILIKE " + chr(36) + str(idx))
            params.append(f"%{topic}%")
            idx += 1
        if owner_id:
            conditions.append("(o.properties->>'owner_id') = " + chr(36) + str(idx))
            params.append(owner_id)
            idx += 1
        if scope:
            conditions.append("(o.properties->>'scope') = " + chr(36) + str(idx))
            params.append(scope)
            idx += 1

        params.extend([limit, offset])

        async with self.pg_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT o.id, o.label, o.properties, o.created_at FROM ontology_nodes o WHERE "
                + " AND ".join(conditions) + " ORDER BY o.created_at DESC NULLS LAST LIMIT "
                + chr(36) + str(idx) + " OFFSET " + chr(36) + str(idx + 1),
                *params,
            )

        records: list[DecisionRecord] = []
        for row in rows:
            props = row["properties"] or {}
            if isinstance(props, str):
                try:
                    props = json.loads(props)
                except Exception:
                    props = {}
            created = row.get("created_at")
            if isinstance(created, datetime):
                created = created.isoformat()
            records.append(DecisionRecord(
                id=row["id"],
                session_id=props.get("session_id", ""),
                owner_id=props.get("owner_id", ""),
                owner_name=props.get("owner_name", ""),
                topic=props.get("topic", ""),
                question=props.get("question", ""),
                chosen=props.get("chosen", ""),
                alternatives=props.get("alternatives", []) if isinstance(props.get("alternatives"), list) else [],
                rationale=props.get("rationale", ""),
                tags=props.get("tags", []) if isinstance(props.get("tags"), list) else [],
                created_at=created or "",
                scope=props.get("scope", "project"),
            ))
        return records

    # ── Get single decision ────────────────────────────

    async def get_by_id(self, decision_id: str) -> DecisionRecord | None:
        if not self.pg_pool:
            return None
        async with self.pg_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, label, properties, created_at FROM ontology_nodes WHERE id = "
                + chr(36) + "1 AND kind = 'decision'",
                decision_id,
            )
        if not row:
            return None
        props = row["properties"] or {}
        if isinstance(props, str):
            try:
                props = json.loads(props)
            except Exception:
                props = {}
        created = row.get("created_at")
        if isinstance(created, datetime):
            created = created.isoformat()
        return DecisionRecord(
            id=row["id"],
            session_id=props.get("session_id", ""),
            owner_id=props.get("owner_id", ""),
            owner_name=props.get("owner_name", ""),
            topic=props.get("topic", ""),
            question=props.get("question", ""),
            chosen=props.get("chosen", ""),
            alternatives=props.get("alternatives", []) if isinstance(props.get("alternatives"), list) else [],
            rationale=props.get("rationale", ""),
            tags=props.get("tags", []) if isinstance(props.get("tags"), list) else [],
            created_at=created or "",
            scope=props.get("scope", "project"),
        )

    # ── Delete a decision ──────────────────────────────

    async def delete(self, decision_id: str) -> bool:
        if not self.pg_pool:
            return False
        async with self.pg_pool.acquire() as conn:
            # Delete link relations
            await conn.execute("DELETE FROM memory_links WHERE source_id = " + chr(36) + "1 OR target_id = " + chr(36) + "2", decision_id, decision_id)
            # Delete hyperedge members
            await conn.execute("DELETE FROM memory_hyperedge_members WHERE node_id = " + chr(36) + "1", decision_id)
            # Delete the ontology node
            await conn.execute("DELETE FROM ontology_nodes WHERE id = " + chr(36) + "1", decision_id)
            # Also clean up episodic_memory
            await conn.execute("DELETE FROM episodic_memory WHERE id = " + chr(36) + "1", f"mem:{decision_id}")
        logger.info("Decision deleted: %s", decision_id[:24])
        return True

    # ── Stats ──────────────────────────────────────────

    async def get_stats(self) -> dict:
        if not self.pg_pool:
            return {"total_decisions": 0, "by_owner": [], "by_topic": []}
        async with self.pg_pool.acquire() as conn:
            total = await conn.fetchval("SELECT COUNT(*) FROM ontology_nodes WHERE kind = 'decision'")
            by_owner_rows = await conn.fetch(
                "SELECT properties->>'owner_id' AS oid, properties->>'owner_name' AS oname, COUNT(*) AS cnt"
                " FROM ontology_nodes WHERE kind = 'decision'"
                " GROUP BY properties->>'owner_id', properties->>'owner_name' ORDER BY cnt DESC"
            )
            by_topic_rows = await conn.fetch(
                "SELECT properties->>'topic' AS topic, COUNT(*) AS cnt"
                " FROM ontology_nodes WHERE kind = 'decision'"
                " GROUP BY properties->>'topic' ORDER BY cnt DESC LIMIT 10"
            )
        return {
            "total_decisions": total,
            "by_owner": [{"owner_id": r["oid"], "owner_name": r["oname"], "count": r["cnt"]} for r in by_owner_rows],
            "by_topic": [{"topic": r["topic"], "count": r["cnt"]} for r in by_topic_rows],
        }
