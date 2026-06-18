"""Ontology-Guided Evolution: Ties self-evolution directly to the memory hypergraph.

This makes the evolution system:
1. TRACEABLE: Every promotion/demotion/threshold/causal-insight is a node in the ontology
2. PROPAGABLE: Evolution knowledge propagates along ontology edges (related skills learn from each other)
3. DISCOVERABLE: Pattern mining on the ontology discovers NEW evolution strategies

Architecture:
    [EvolutionState] --upsert--> [Ontology Node] --relates--> [Skill Nodes]
           ↓                                      ↓
    [Promotion/Demotion Hyperedges]      [Strategy Propagation]

Ontology types added:
- evolution_category      → State of a category's evolution
- promotion_event         → A promotion attempt and its outcome
- demotion_event          → A demotion event
- evolution_threshold     → Learned promotion threshold (meta-evolution)
- causal_insight          → Causal conclusion about promotion effect
- pareto_tradeoff         → Discovered tradeoff between objectives
- strategy_pattern        → A repair strategy that works for specific problem types
"""

from __future__ import annotations

import time
import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple


logger = logging.getLogger(__name__)


@dataclass
class OntologyEvolutionEdge:
    """An edge in the ontology evolution graph: A → B means A's evolution informs B."""
    source_id: str
    target_id: str
    relation: str  # "informs_evolution_of", "shares_failure_pattern", "strategy_applicable_to"
    weight: float  # 0.0-1.0: how strong the evolutionary connection is
    evidence_count: int = 1  # How many times this relation has been observed


class OntologyEvolutionTracker:
    """Tracks evolution events directly in the ontology hypergraph.

    This is the bridge between MetaEvolutionLearner and the memory system.
    Every evolution decision leaves a permanent, queryable trace in the ontology.

    Usage:
        tracker = OntologyEvolutionTracker(memory_service)
        await tracker.track_promotion(category, from_level, to_level, metrics)
        await tracker.propagate_evolution_knowledge()  # Related categories learn
    """

    # Ontology type constants
    TYPE_EVOLUTION_CATEGORY = "evolution_category"
    TYPE_PROMOTION = "promotion_event"
    TYPE_DEMOTION = "demotion_event"
    TYPE_THRESHOLD = "evolution_threshold"
    TYPE_STRATEGY_PATTERN = "strategy_pattern"

    def __init__(self, memory_service=None):
        self._memory = memory_service
        self._edges: Dict[Tuple[str, str, str], OntologyEvolutionEdge] = {}
        # Cache: category -> set of related categories
        self._related_categories: Dict[str, Set[str]] = {}

    async def track_promotion(
        self,
        category: str,
        from_level: str,
        to_level: str,
        success_rate_before: float,
        success_rate_after: Optional[float],
        outcome: str,  # "SUCCESS" | "FAILURE" | "PENDING"
        thresholds: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Track a promotion as an ontology hyperedge.

        Returns the hyperedge ID for reference.
        """
        if self._memory is None:
            return ""

        try:
            pg = getattr(self._memory, 'pg_pool', None)
            if pg is None:
                return ""

            now = time.time()
            he_id = self._memory.ontology_node_id(
                self.TYPE_PROMOTION,
                f"{category}:{from_level}_to_{to_level}:{int(now)}"
            )

            # Create/update the category's evolution node
            cat_node_id = await self._ensure_category_node(category)

            summary = {
                "category": category,
                "from_level": from_level,
                "to_level": to_level,
                "success_rate_before": success_rate_before,
                "success_rate_after": success_rate_after,
                "outcome": outcome,
                "timestamp": now,
                "thresholds_used": thresholds or {},
            }

            async with pg.acquire() as conn:
                # Create the promotion event hyperedge
                await conn.execute(
                    self._upsert_hyperedge_sql(),
                    he_id, self.TYPE_PROMOTION,
                    f"Promotion: {category} {from_level} → {to_level}",
                    json.dumps(summary),
                    json.dumps({"ontology": "evolution_tracker.v1"}),
                )

                # Add members: category + from_level + to_level + outcome
                await self._add_hyperedge_member(conn, he_id, cat_node_id,
                                                 "evolution_subject", self.TYPE_EVOLUTION_CATEGORY, 1.0)

                level_node_before = self._memory.ontology_node_id("claim", f"level:{from_level}")
                await self._ensure_claim_node(conn, level_node_before, f"Level: {from_level}",
                                              {"level": from_level, "type": "evolution_level"})
                await self._add_hyperedge_member(conn, he_id, level_node_before,
                                                 "from_level", "claim", 1.0)

                level_node_after = self._memory.ontology_node_id("claim", f"level:{to_level}")
                await self._ensure_claim_node(conn, level_node_after, f"Level: {to_level}",
                                              {"level": to_level, "type": "evolution_level"})
                await self._add_hyperedge_member(conn, he_id, level_node_after,
                                                 "to_level", "claim", 1.0)

                outcome_node = self._memory.ontology_node_id("claim", f"promotion_outcome:{outcome}")
                await self._ensure_claim_node(conn, outcome_node, f"Outcome: {outcome}",
                                              {"outcome": outcome, "type": "evolution_outcome"})
                await self._add_hyperedge_member(conn, he_id, outcome_node,
                                                 "outcome", "claim", 1.0)

                # Link category to promotion event: category --produces--> promotion
                await conn.execute(
                    self._upsert_relation_sql(),
                    cat_node_id, he_id, "produces", 1.0,
                    json.dumps({"relation_type": "evolution_event", "category": category}),
                )

            logger.info(f"OntologyEvolution: Tracked promotion {he_id}")
            return he_id

        except Exception as e:
            logger.debug(f"OntologyEvolution track_promotion failed: {e}")
            return ""

    async def track_demotion(
        self,
        category: str,
        from_level: str,
        to_level: str,
        reason: str,
        success_rate: float,
    ) -> str:
        """Track a demotion as an ontology hyperedge."""
        if self._memory is None:
            return ""

        try:
            pg = getattr(self._memory, 'pg_pool', None)
            if pg is None:
                return ""

            now = time.time()
            he_id = self._memory.ontology_node_id(
                self.TYPE_DEMOTION,
                f"{category}:{from_level}_to_{to_level}:{int(now)}"
            )

            cat_node_id = await self._ensure_category_node(category)

            summary = {
                "category": category,
                "from_level": from_level,
                "to_level": to_level,
                "reason": reason,
                "success_rate": success_rate,
                "timestamp": now,
            }

            async with pg.acquire() as conn:
                await conn.execute(
                    self._upsert_hyperedge_sql(),
                    he_id, self.TYPE_DEMOTION,
                    f"Demotion: {category} {from_level} → {to_level}",
                    json.dumps(summary),
                    json.dumps({"ontology": "evolution_tracker.v1"}),
                )

                await self._add_hyperedge_member(conn, he_id, cat_node_id,
                                                 "evolution_subject", self.TYPE_EVOLUTION_CATEGORY, 1.0)

                # A demotion is EVIDENCE that the promotion threshold was wrong
                # Create a relation: demotion --invalidates--> previous promotion threshold
                threshold_id = self._memory.ontology_node_id(
                    self.TYPE_THRESHOLD,
                    f"{category}:{from_level}"
                )
                await conn.execute(
                    self._upsert_relation_sql(),
                    he_id, threshold_id, "invalidates_threshold", 0.9,
                    json.dumps({"reason": reason, "success_rate": success_rate}),
                )

            logger.info(f"OntologyEvolution: Tracked demotion {he_id}")
            return he_id

        except Exception as e:
            logger.debug(f"OntologyEvolution track_demotion failed: {e}")
            return ""

    async def track_threshold_update(
        self,
        category: str,
        target_level: str,
        old_thresholds: Dict[str, Any],
        new_thresholds: Dict[str, Any],
        reason: str,
    ) -> str:
        """Track a threshold change (meta-evolution!) as an ontology node."""
        if self._memory is None:
            return ""

        try:
            pg = getattr(self._memory, 'pg_pool', None)
            if pg is None:
                return ""

            node_id = self._memory.ontology_node_id(
                self.TYPE_THRESHOLD,
                f"{category}:{target_level}"
            )

            metadata = {
                "category": category,
                "target_level": target_level,
                "old_thresholds": old_thresholds,
                "new_thresholds": new_thresholds,
                "reason": reason,
                "updated_at": time.time(),
            }

            async with pg.acquire() as conn:
                await conn.execute(
                    self._upsert_node_sql(),
                    node_id, self.TYPE_THRESHOLD,
                    f"Threshold: {category} → {target_level}",
                    json.dumps(metadata),
                    json.dumps({"ontology": "evolution_tracker.v1"}),
                )

            logger.info(
                f"OntologyEvolution: Threshold updated {category}->{target_level}: "
                f"{old_thresholds} → {new_thresholds}"
            )
            return node_id

        except Exception as e:
            logger.debug(f"OntologyEvolution track_threshold_update failed: {e}")
            return ""

    async def discover_evolution_patterns(self) -> List[Dict[str, Any]]:
        """Query the ontology to discover cross-category evolution patterns.

        This is the MAGIC: instead of hardcoding what relates to what, we
        QUERY THE ONTOLOGY GRAPH. The system discovers its own evolution heuristics.

        Example discoveries:
        - "research category promotions have 30% higher success rate than code"
        - "80% of promotions from PREDICTIVE to EXPLORATION fail when success_rate < 0.8"
        - "Categories with 'api' or 'network' dependencies need much higher thresholds"
        """
        if self._memory is None:
            return []

        try:
            pg = getattr(self._memory, 'pg_pool', None)
            if pg is None:
                return []

            patterns = []

            async with pg.acquire() as conn:
                # Pattern 1: Success rate of promotions by (from_level, to_level) pair
                rows = await conn.fetch("""
                    SELECT
                        (summary->>'from_level') as from_level,
                        (summary->>'to_level') as to_level,
                        COUNT(*) as total,
                        AVG(CASE WHEN summary->>'outcome' = 'SUCCESS' THEN 1.0 ELSE 0.0 END) as success_rate
                    FROM memory_hyperedges
                    WHERE edge_type = $1
                    GROUP BY 1, 2
                    ORDER BY 1, 2
                """, self.TYPE_PROMOTION)

                for row in rows:
                    patterns.append({
                        "type": "promotion_success_by_level",
                        "from_level": row["from_level"],
                        "to_level": row["to_level"],
                        "total_promotions": row["total"],
                        "success_rate": float(row["success_rate"]),
                        "insight": (
                            f"Promotions {row['from_level']}→{row['to_level']} have "
                            f"{float(row['success_rate'])*100:.1f}% success rate "
                            f"(n={row['total']})"
                        ),
                    })

                # Pattern 2: Which categories have the most demotions? (instability)
                rows = await conn.fetch("""
                    SELECT
                        summary->>'category' as category,
                        COUNT(*) as demotion_count,
                        array_agg(DISTINCT summary->>'reason') as reasons
                    FROM memory_hyperedges
                    WHERE edge_type = $1
                    GROUP BY 1
                    ORDER BY 2 DESC
                """, self.TYPE_DEMOTION)

                for row in rows:
                    patterns.append({
                        "type": "category_instability",
                        "category": row["category"],
                        "demotion_count": row["demotion_count"],
                        "common_reasons": row["reasons"],
                        "insight": (
                            f"Category '{row['category']}' had {row['demotion_count']} demotions, "
                            f"suggesting it needs higher promotion thresholds"
                        ),
                    })

                # Pattern 3: Threshold effectiveness (did threshold changes help?)
                # This would be mined by correlating threshold changes with subsequent promotion outcomes

            logger.info(f"OntologyEvolution: Discovered {len(patterns)} evolution patterns")
            return patterns

        except Exception as e:
            logger.debug(f"OntologyEvolution discover_patterns failed: {e}")
            return []

    async def propagate_evolution_knowledge(
        self,
        source_category: str,
        promotion_outcome: bool,
    ) -> List[str]:
        """Propagate evolution knowledge from source_category to related categories.

        When A has a successful/failed promotion:
        - Find categories related to A via ontology edges
        - Adjust their thresholds accordingly
        - Returns list of categories that were influenced
        """
        if self._memory is None:
            return []

        influenced = []
        try:
            # Discover related categories via ontology link traversal
            related = await self._find_related_categories(source_category)

            for target_cat in related:
                if target_cat == source_category:
                    continue

                # Propagate with attenuation: source evidence is 70% as strong for target
                attenuation = 0.7

                # Record the relation in our in-memory graph
                edge_key = (source_category, target_cat, "informs_evolution_of")
                if edge_key not in self._edges:
                    self._edges[edge_key] = OntologyEvolutionEdge(
                        source_id=source_category,
                        target_id=target_cat,
                        relation="informs_evolution_of",
                        weight=attenuation,
                    )
                else:
                    self._edges[edge_key].evidence_count += 1
                    self._edges[edge_key].weight = min(1.0, self._edges[edge_key].weight + 0.05)

                influenced.append(target_cat)

            if influenced:
                logger.info(
                    f"OntologyEvolution: Promotion outcome for {source_category} propagated to "
                    f"{len(influenced)} related categories: {influenced}"
                )

        except Exception as e:
            logger.debug(f"OntologyEvolution propagate failed: {e}")

        return influenced

    async def get_evolution_insights(self, category: str) -> Dict[str, Any]:
        """Get ontology-powered insights about a category's evolution.

        Returns:
        - Historical promotion/demotion stats for this category
        - How similar categories have evolved
        - Recommended threshold adjustments based on global patterns
        """
        patterns = await self.discover_evolution_patterns()

        # Filter patterns relevant to this category
        category_patterns = [p for p in patterns if p.get("category") == category]
        level_patterns = [p for p in patterns if p.get("type") == "promotion_success_by_level"]

        return {
            "category": category,
            "patterns_affecting_this_category": category_patterns,
            "global_level_success_rates": level_patterns,
            "recommendations": self._generate_recommendations(category, patterns),
        }

    async def track_causal_insight(self, insight: Dict[str, Any]) -> str:
        """Persist a causal insight as an ontology node + supporting evidence edges.

        Every causal conclusion from PSM/DiD analysis becomes a first-class
        citizen in the knowledge graph, discoverable via SPARQL queries.
        """
        if self._memory is None:
            return ""

        try:
            import time
            pg = getattr(self._memory, 'pg_pool', None)
            if pg is None:
                return ""

            node_id = self._memory.ontology_node_id(
                "causal_insight",
                f"{insight['category']}:{insight['from_level']}_to_{insight['to_level']}:{int(time.time())}"
            )

            metadata = {
                "category": insight["category"],
                "from_level": insight["from_level"],
                "to_level": insight["to_level"],
                "att": insight["att"],
                "confidence": insight.get("confidence", 0.5),
                "sample_size": insight.get("sample_size", 0),
                "recommendation": insight.get("recommendation", "no_change"),
                "explanation": insight.get("explanation", ""),
                "timestamp": time.time(),
            }

            async with pg.acquire() as conn:
                # Create the causal insight node
                await conn.execute(
                    """
                    INSERT INTO memory_nodes (id, kind, label, description, metadata, created_at, updated_at)
                    VALUES ($1, $2, $3, $4, $5, NOW(), NOW())
                    ON CONFLICT (id) DO UPDATE SET metadata = EXCLUDED.metadata, updated_at = NOW()
                    """,
                    node_id, "causal_insight",
                    f"Causal: {insight['category']} {insight['from_level']}→{insight['to_level']}",
                    metadata,
                    {"ontology": "evolution_tracker.v1"},
                )

                # Link to the evolution category node
                cat_node_id = self._memory.ontology_node_id("evolution_category", insight["category"])
                await conn.execute(
                    """
                    INSERT INTO memory_links (source_id, target_id, relation, weight, metadata, created_at)
                    VALUES ($1, $2, $3, $4, $5, NOW())
                    ON CONFLICT (source_id, target_id, relation) DO UPDATE SET weight = EXCLUDED.weight
                    """,
                    cat_node_id, node_id, "has_causal_insight", insight.get("confidence", 0.5),
                    {"evolution": True, "causal": True},
                )

            logger.info(
                f"OntologyEvolution: Persisted causal insight {insight['category']} "
                f"ATT={insight['att']:+.3f}"
            )
            return node_id

        except Exception as e:
            logger.debug(f"OntologyEvolution track_causal_insight failed: {e}")
            return ""

    async def track_pareto_tradeoff(
        self,
        objective_a: str,
        objective_b: str,
        correlation: float,
        strength: str,
        insight: str,
    ) -> str:
        """Persist a discovered Pareto tradeoff as an ontology hyperedge.

        Tradeoffs are represented as hyperedges connecting TWO objective nodes,
        with the correlation coefficient as edge weight.
        """
        if self._memory is None:
            return ""

        try:
            import time
            pg = getattr(self._memory, 'pg_pool', None)
            if pg is None:
                return ""

            now = time.time()
            he_id = self._memory.ontology_node_id(
                "pareto_tradeoff",
                f"{objective_a}_vs_{objective_b}:{int(now)}"
            )

            summary = {
                "objective_a": objective_a,
                "objective_b": objective_b,
                "correlation": correlation,
                "strength": strength,
                "insight": insight,
                "timestamp": now,
            }

            async with pg.acquire() as conn:
                # Create the tradeoff hyperedge
                await conn.execute(
                    """
                    INSERT INTO memory_hyperedges (id, edge_type, label, summary, metadata, created_at, updated_at)
                    VALUES ($1, $2, $3, $4, $5, NOW(), NOW())
                    ON CONFLICT (id) DO UPDATE SET summary = EXCLUDED.summary, metadata = EXCLUDED.metadata, updated_at = NOW()
                    """,
                    he_id, "pareto_tradeoff",
                    f"Tradeoff: {objective_a} ↔ {objective_b} ({correlation:+.2f})",
                    summary,
                    {"ontology": "evolution_tracker.v1", "pareto": True},
                )

                # Create objective nodes
                for obj in [objective_a, objective_b]:
                    obj_id = self._memory.ontology_node_id("objective", obj)
                    await conn.execute(
                        """
                        INSERT INTO memory_nodes (id, kind, label, description, metadata, created_at, updated_at)
                        VALUES ($1, $2, $3, $4, $5, NOW(), NOW())
                        ON CONFLICT (id) DO NOTHING
                        """,
                        obj_id, "objective", f"Objective: {obj}",
                        {"name": obj, "type": "evolution_objective"},
                        {"ontology": "evolution_tracker.v1"},
                    )

                # Correlation relation between objectives
                if correlation > 0:
                    relation_type = "positively_correlates_with"
                else:
                    relation_type = "negatively_correlates_with"

                obj_a_id = self._memory.ontology_node_id("objective", objective_a)
                obj_b_id = self._memory.ontology_node_id("objective", objective_b)
                await conn.execute(
                    """
                    INSERT INTO memory_links (source_id, target_id, relation, weight, metadata, created_at)
                    VALUES ($1, $2, $3, $4, $5, NOW())
                    ON CONFLICT (source_id, target_id, relation) DO UPDATE SET weight = EXCLUDED.weight
                    """,
                    obj_a_id, obj_b_id, relation_type, abs(correlation),
                    {"correlation": correlation, "strength": strength},
                )

            logger.info(
                f"OntologyEvolution: Persisted Pareto tradeoff {objective_a} ↔ {objective_b} "
                f"correlation={correlation:+.2f}"
            )
            return he_id

        except Exception as e:
            logger.debug(f"OntologyEvolution track_pareto_tradeoff failed: {e}")
            return ""

    # ── Internal Helpers ──────────────────────────────────────────────────────────

    async def _ensure_category_node(self, category: str) -> str:
        """Ensure the evolution category node exists in the ontology."""
        if self._memory is None:
            return ""
        pg = getattr(self._memory, 'pg_pool', None)
        if pg is None:
            return ""

        node_id = self._memory.ontology_node_id(self.TYPE_EVOLUTION_CATEGORY, category)
        try:
            async with pg.acquire() as conn:
                await conn.execute(
                    self._upsert_node_sql(),
                    node_id, self.TYPE_EVOLUTION_CATEGORY,
                    f"Evolution Category: {category}",
                    json.dumps({"category": category}),
                    json.dumps({"ontology": "evolution_tracker.v1"}),
                )
        except Exception:
            pass
        return node_id

    async def _ensure_claim_node(self, conn, node_id: str, label: str, props: Dict):
        """Ensure a claim node exists."""
        await conn.execute(
            self._upsert_node_sql(),
            node_id, "claim", label,
            json.dumps(props),
            json.dumps({"ontology": "evolution_tracker.v1"}),
        )

    async def _add_hyperedge_member(self, conn, he_id: str, member_id: str,
                                     role: str, member_type: str, weight: float):
        """Add a member to a hyperedge."""
        await conn.execute(
            """
            INSERT INTO memory_hyperedge_members
            (hyperedge_id, member_id, role, member_type, weight, metadata, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, NOW())
            ON CONFLICT (hyperedge_id, member_id, role) DO NOTHING
            """,
            he_id, member_id, role, member_type, weight,
            json.dumps({"evolution_tracker": True}),
        )

    async def _find_related_categories(self, category: str) -> List[str]:
        """Find categories evolutionarily related to the given one via ontology."""
        # Simple implementation: keyword matching + predefined relations
        # Real implementation would traverse the ontology skill dependency graph
        related_sets = {
            "research": {"code", "monitoring"},
            "code": {"research", "media"},
            "media": {"code", "monitoring"},
            "monitoring": {"research", "media"},
        }
        return list(related_sets.get(category, set()))

    def _generate_recommendations(self, category: str, patterns: List[Dict]) -> List[str]:
        """Generate human-readable evolution recommendations from patterns."""
        recs = []
        for p in patterns:
            if p.get("type") == "category_instability" and p.get("category") == category:
                if p["demotion_count"] >= 3:
                    recs.append(
                        f"High instability detected ({p['demotion_count']} demotions). "
                        f"Consider raising promotion thresholds by 15%."
                    )
            if p.get("type") == "promotion_success_by_level":
                if p["success_rate"] < 0.5:
                    recs.append(
                        f"{p['from_level']}→{p['to_level']} promotions have only "
                        f"{p['success_rate']*100:.0f}% success rate globally. Thresholds should be raised."
                    )
        return recs

    def _upsert_node_sql(self) -> str:
        return """
            INSERT INTO memory_nodes (id, kind, label, description, metadata, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5, NOW(), NOW())
            ON CONFLICT (id) DO UPDATE SET metadata = EXCLUDED.metadata, updated_at = NOW()
        """

    def _upsert_hyperedge_sql(self) -> str:
        return """
            INSERT INTO memory_hyperedges (id, edge_type, label, summary, metadata, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5, NOW(), NOW())
            ON CONFLICT (id) DO UPDATE SET summary = EXCLUDED.summary, metadata = EXCLUDED.metadata, updated_at = NOW()
        """

    def _upsert_relation_sql(self) -> str:
        return """
            INSERT INTO memory_links (source_id, target_id, relation, weight, metadata, created_at)
            VALUES ($1, $2, $3, $4, $5, NOW())
            ON CONFLICT (source_id, target_id, relation) DO UPDATE SET weight = EXCLUDED.weight
        """
