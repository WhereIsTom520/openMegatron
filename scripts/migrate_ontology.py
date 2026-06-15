"""Ontology data migration: align existing Neo4j data with ontology types.

Usage:
    python scripts/migrate_ontology.py --dry-run    # Preview changes
    python scripts/migrate_ontology.py               # Apply migration

Operations:
  1. Rename edge types: MENTIONS → mentions, RAG_MENTIONS → rag_mentions
  2. Relabel nodes: MemoryCard → Memory:OntologyNode (dual label)
  3. Add kind property to bare nodes based on Neo4j label
  4. Report orphan nodes/edges with no ontology mapping
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pysrc"))

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


# ── Migration maps ───────────────────────────────────────────────────────────

EDGE_RENAMES = {
    "MENTIONS": "mentions",
    "RAG_MENTIONS": "rag_mentions",
    "RELATES_TO": "related",
    "DEPENDS_ON": "part_of",
    "CONTRASTS": "contradicts",
}

NODE_LABEL_ALIASES = {
    "MemoryCard": "Memory",
    "ENTITY": "Entity",
    "RELATION": "OntologyNode",
}

KIND_FROM_LABEL = {
    "Memory": "memory", "MemoryRecord": "memory",
    "Entity": "entity", "RAGEntity": "rag_entity",
    "Topic": "topic", "Skill": "skill", "Tool": "tool",
    "Claim": "claim", "Evidence": "evidence",
    "Artifact": "artifact", "Owner": "owner",
    "Scope": "scope", "HyperEdge": "hyperedge",
    "Paper": "paper", "Author": "author", "Venue": "venue",
    "LiteratureReview": "literature_review",
    "Decision": "decision", "Alternative": "alternative",
    "Document": "document", "Community": "community",
    "MemoryCard": "memory_card",
    "Project": "project", "Session": "session",
    "ConversationSession": "session",
}


async def migrate(config_path: str = "model.toml", dry_run: bool = True):
    """Run the ontology alignment migration."""
    # Load config
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib

    with open(config_path, "rb") as f:
        config = tomllib.load(f)

    neo4j_cfg = config.get("neo4j", {})
    uri = neo4j_cfg.get("uri", "bolt://localhost:7687")
    user = neo4j_cfg.get("user", "neo4j")
    password = neo4j_cfg.get("password", "root")

    from neo4j import AsyncGraphDatabase
    driver = AsyncGraphDatabase.driver(uri, auth=(user, password))

    try:
        async with driver.session(database="neo4j") as session:
            stats = {"edge_renames": 0, "label_aliases": 0, "kind_added": 0, "orphans": 0}

            # 1. Rename edges
            for old_type, new_type in EDGE_RENAMES.items():
                try:
                    result = await session.run(
                        f"MATCH ()-[r:{old_type}]->() RETURN count(r) AS cnt"
                    )
                    record = await result.single()
                    count = record["cnt"] if record else 0
                    if count > 0:
                        if not dry_run:
                            await session.run(
                                f"MATCH (a)-[r:{old_type}]->(b) "
                                f"CREATE (a)-[r2:{new_type}]->(b) "
                                f"SET r2 = properties(r) "
                                f"DELETE r"
                            )
                        stats["edge_renames"] += count
                        logger.info(f"{'[DRY RUN] ' if dry_run else ''}"
                                    f"Rename {old_type}→{new_type}: {count} edges")
                except Exception as e:
                    logger.debug(f"Edge rename {old_type} skipped: {e}")

            # 2. Relabel nodes
            for old_label, new_label in NODE_LABEL_ALIASES.items():
                try:
                    result = await session.run(
                        f"MATCH (n:{old_label}) WHERE NOT n:{new_label} "
                        f"RETURN count(n) AS cnt"
                    )
                    record = await result.single()
                    count = record["cnt"] if record else 0
                    if count > 0:
                        if not dry_run:
                            await session.run(
                                f"MATCH (n:{old_label}) "
                                f"SET n:{new_label}"
                            )
                        stats["label_aliases"] += count
                        logger.info(f"{'[DRY RUN] ' if dry_run else ''}"
                                    f"Relabel {old_label}→{new_label}: {count} nodes")
                except Exception as e:
                    logger.debug(f"Label alias {old_label} skipped: {e}")

            # 3. Add kind property to ontology nodes without one
            for label, kind in KIND_FROM_LABEL.items():
                try:
                    result = await session.run(
                        f"MATCH (n:{label}:OntologyNode) WHERE n.kind IS NULL "
                        f"RETURN count(n) AS cnt"
                    )
                    record = await result.single()
                    count = record["cnt"] if record else 0
                    if count > 0:
                        if not dry_run:
                            await session.run(
                                f"MATCH (n:{label}:OntologyNode) WHERE n.kind IS NULL "
                                f"SET n.kind = $kind",
                                kind=kind,
                            )
                        stats["kind_added"] += count
                        logger.info(f"{'[DRY RUN] ' if dry_run else ''}"
                                    f"Add kind='{kind}' to {label}: {count} nodes")
                except Exception as e:
                    logger.debug(f"Kind add {label} skipped: {e}")

            # 4. Report orphans (nodes with no ontology label)
            try:
                result = await session.run(
                    "MATCH (n) WHERE NOT n:OntologyNode "
                    "AND NOT n:Paper AND NOT n:Author AND NOT n:Venue "
                    "AND NOT n:Topic AND NOT n:Skill AND NOT n:Tool "
                    "AND NOT n:Decision AND NOT n:Owner AND NOT n:Memory "
                    "AND NOT n:Evidence AND NOT n:Claim AND NOT n:Artifact "
                    "AND NOT n:Entity AND NOT n:Alternative "
                    "AND NOT n:LiteratureReview AND NOT n:Project AND NOT n:Session "
                    "AND NOT n:Scope AND NOT n:HyperEdge "
                    "AND NOT n:Document AND NOT n:Community AND NOT n:MemoryCard "
                    "AND NOT n:RAGEntity AND NOT n:ConversationSession "
                    "AND NOT n:MemoryRecord "
                    "RETURN labels(n) AS lbls, count(*) AS cnt "
                    "ORDER BY cnt DESC LIMIT 20"
                )
                orphans = [row async for row in result]
                stats["orphans"] = sum(r["cnt"] for r in orphans)
                if orphans:
                    logger.warning(f"Orphan nodes (no ontology label): {stats['orphans']} total")
                    for row in orphans[:10]:
                        logger.warning(f"  {row['lbls']}: {row['cnt']}")
            except Exception as e:
                logger.debug(f"Orphan scan skipped: {e}")

            # Summary
            logger.info(f"\n=== Migration {'Preview' if dry_run else 'Complete'} ===")
            logger.info(f"  Edges renamed: {stats['edge_renames']}")
            logger.info(f"  Nodes relabeled: {stats['label_aliases']}")
            logger.info(f"  Kind properties added: {stats['kind_added']}")
            logger.info(f"  Orphan nodes: {stats['orphans']}")

            if dry_run:
                logger.info("  Run without --dry-run to apply changes.")

    finally:
        await driver.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate Neo4j data to ontology alignment")
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="Preview changes without applying")
    parser.add_argument("--apply", dest="dry_run", action="store_false",
                        help="Apply the migration")
    parser.add_argument("--config", default="model.toml", help="Config file path")
    args = parser.parse_args()

    import asyncio
    asyncio.run(migrate(args.config, args.dry_run))
