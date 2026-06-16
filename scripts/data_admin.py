import argparse
import asyncio
import json
import os
import urllib.parse
from pathlib import Path
from typing import Iterable

import asyncpg
import redis.asyncio as redis
from neo4j import AsyncGraphDatabase

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib


CONVERSATION_PATTERNS = [
    "agent_history:*",
    "core_memory:*",
    "notifications:*",
    "active_chat_turn:*",
    "confirm_req:*",
    "failure:*",
    "chat:shared:*",
    "chat:private:*",
]

MEMORY_REDIS_PATTERNS = [
    "core_memory:*",
    "rag:cache:*",
    "vec:cache:*",
    "graph:*",
]

MEMORY_CLEAR_ONTOLOGY_KINDS = [
    "memory",
    "memory_card",
    "rag_entity",
    "document",
    "community",
    "paper",
    "author",
    "venue",
    "literature_review",
    "claim",
    "evidence",
    "artifact",
    "entity",
]


def load_config(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open("rb") as file:
        return tomllib.load(file)


def redis_url(redis_cfg: dict) -> str:
    host = redis_cfg.get("host", "localhost")
    port = int(redis_cfg.get("port", 6379))
    db = int(redis_cfg.get("db", 0))
    password = redis_cfg.get("password")
    if password:
        encoded = urllib.parse.quote_plus(str(password))
        return f"redis://:{encoded}@{host}:{port}/{db}"
    return f"redis://{host}:{port}/{db}"


def postgres_dsn(config: dict) -> str:
    pg_cfg = config.get("postgres") or config.get("postgresql") or config.get("pgvector") or {}
    if pg_cfg.get("dsn"):
        return str(pg_cfg["dsn"])
    user = pg_cfg.get("user", "root")
    password = pg_cfg.get("password", "root")
    host = pg_cfg.get("host", "localhost")
    port = int(pg_cfg.get("port", 54320))
    database = pg_cfg.get("database", "root")
    return f"postgresql://{user}:{password}@{host}:{port}/{database}"


def command_count(command: str) -> int:
    try:
        return int(str(command).split()[-1])
    except Exception:
        return 0


async def delete_redis_patterns(config: dict, patterns: Iterable[str]) -> int:
    redis_cfg = config.get("redis", {}) or {}
    client = redis.from_url(redis_url(redis_cfg), decode_responses=True)
    deleted = 0
    try:
        for pattern in patterns:
            batch = []
            async for key in client.scan_iter(match=pattern, count=200):
                batch.append(key)
                if len(batch) >= 200:
                    deleted += int(await client.delete(*batch))
                    batch = []
            if batch:
                deleted += int(await client.delete(*batch))
    finally:
        await client.aclose()
    return deleted


async def clear_postgres_memory(config: dict) -> dict:
    deleted = {
        "memory_links": 0,
        "memory_evolution_log": 0,
        "memory_hyperedge_members": 0,
        "memory_hyperedges": 0,
        "topic_index": 0,
        "rag_chunks": 0,
        "rag_documents": 0,
        "rag_communities": 0,
        "episodic_memory": 0,
        "ontology_nodes": 0,
    }

    async def execute_delete(conn, key: str, sql: str, *args) -> None:
        try:
            deleted[key] = command_count(await conn.execute(sql, *args))
        except Exception as exc:
            deleted[key] = 0
            print(f"[WARN] Skipped {key}: {exc}")

    conn = await asyncpg.connect(postgres_dsn(config))
    try:
        await execute_delete(
            conn,
            "memory_links",
            """
            WITH doomed AS (
                SELECT id FROM episodic_memory
                UNION SELECT id FROM rag_documents
                UNION SELECT id FROM rag_chunks
                UNION SELECT id FROM ontology_nodes WHERE kind = ANY($1::text[])
            )
            DELETE FROM memory_links
            WHERE source_id IN (SELECT id FROM doomed)
               OR target_id IN (SELECT id FROM doomed)
            """,
            MEMORY_CLEAR_ONTOLOGY_KINDS,
        )
        await execute_delete(
            conn,
            "memory_evolution_log",
            """
            WITH doomed AS (
                SELECT id FROM episodic_memory
                UNION SELECT id FROM rag_documents
                UNION SELECT id FROM rag_chunks
                UNION SELECT id FROM ontology_nodes WHERE kind = ANY($1::text[])
            )
            DELETE FROM memory_evolution_log
            WHERE source_id IN (SELECT id FROM doomed)
               OR target_id IN (SELECT id FROM doomed)
            """,
            MEMORY_CLEAR_ONTOLOGY_KINDS,
        )
        await execute_delete(
            conn,
            "memory_hyperedge_members",
            """
            DELETE FROM memory_hyperedge_members
            WHERE node_id IN (
                SELECT id FROM ontology_nodes WHERE kind = ANY($1::text[])
            )
            """,
            MEMORY_CLEAR_ONTOLOGY_KINDS,
        )
        await execute_delete(
            conn,
            "memory_hyperedges",
            """
            DELETE FROM memory_hyperedges h
            WHERE NOT EXISTS (
                SELECT 1 FROM memory_hyperedge_members m WHERE m.hyperedge_id = h.id
            )
            """,
        )
        for table in ("topic_index", "rag_chunks", "rag_documents", "rag_communities", "episodic_memory"):
            await execute_delete(conn, table, f"DELETE FROM {table}")
        await execute_delete(
            conn,
            "ontology_nodes",
            "DELETE FROM ontology_nodes WHERE kind = ANY($1::text[])",
            MEMORY_CLEAR_ONTOLOGY_KINDS,
        )
    finally:
        await conn.close()
    return deleted


async def clear_neo4j_memory(config: dict) -> int:
    neo_cfg = config.get("neo4j", {}) or {}
    uri = neo_cfg.get("uri", "bolt://localhost:7687")
    user = neo_cfg.get("user", "neo4j")
    password = neo_cfg.get("password", "root")
    driver = AsyncGraphDatabase.driver(uri, auth=(user, password))
    try:
        async with driver.session() as session:
            result = await session.run(
                """
                MATCH (n)
                WHERE n.kind IN $kinds
                   OR any(label IN labels(n) WHERE label IN [
                       'Memory', 'MemoryRecord', 'MemoryCard',
                       'RAGEntity', 'Document', 'Community',
                       'Paper', 'Author', 'Venue', 'LiteratureReview',
                       'Claim', 'Evidence', 'Artifact', 'Entity'
                   ])
                DETACH DELETE n
                """,
                kinds=MEMORY_CLEAR_ONTOLOGY_KINDS,
            )
            summary = await result.consume()
            return int(getattr(summary.counters, "nodes_deleted", 0) or 0)
    finally:
        await driver.close()


async def trajectory_stats(args) -> int:
    """Print trajectory store statistics."""
    from pysrc.trajectory_store import TrajectoryStore
    store = TrajectoryStore(db_path=args.db)
    stats = store.stats()
    print(json.dumps(stats, indent=2, ensure_ascii=False))
    store.close()
    return 0


async def trajectory_export(args) -> int:
    """Export trajectories as JSONL."""
    from pysrc.trajectory_store import TrajectoryStore
    store = TrajectoryStore(db_path=args.db)
    count = store.export_jsonl(args.output)
    print(f"Exported {count} trajectories to {args.output}")
    store.close()
    return 0


async def trajectory_list(args) -> int:
    """List recent trajectories."""
    from pysrc.trajectory_store import TrajectoryStore
    store = TrajectoryStore(db_path=args.db)
    trajectories = store.query(
        source=args.source or None,
        success=args.success if hasattr(args, 'success') and args.success is not None else None,
        limit=args.limit,
    )
    for t in trajectories:
        print(json.dumps({
            "id": t["id"],
            "session_id": t["session_id"],
            "user_input": t["user_input"][:100],
            "success": t["success"],
            "reward": t["reward"],
            "tool_count": t["tool_count"],
            "source": t["source"],
            "created_at": t["created_at"],
        }, ensure_ascii=False))
    print(f"\n{len(trajectories)} trajectories shown (total: {store.count()})")
    store.close()
    return 0


async def trajectory_import_external_agent(args) -> int:
    """Parse and import External Agent JSONL transcripts."""
    from pysrc.external_agent_parser import ExternalAgentParser
    from pysrc.trajectory_store import TrajectoryStore
    parser = ExternalAgentParser()
    input_path = Path(args.input)
    if input_path.is_file():
        turns = parser.parse_file(str(input_path))
    elif input_path.is_dir():
        turns = parser.parse_directory(str(input_path))
    else:
        print(f"Error: input path not found: {args.input}", file=__import__('sys').stderr)
        return 1
    trajectories = parser.to_trajectories(turns)
    store = TrajectoryStore(db_path=args.db)
    imported = 0
    for traj in trajectories:
        try:
            store.store(traj)
            imported += 1
        except Exception as exc:
            print(f"[WARN] Failed to import: {exc}")
    print(f"Imported {imported} trajectories into {args.db}")
    stats = store.stats()
    print(f"Store now has {stats['total']} total trajectories")
    store.close()
    return 0


async def trajectory_import(args) -> int:
    """Import trajectories from External Text Agent, OpenMegatron, or custom JSON/JSONL."""
    from pysrc.trajectory_importer import TrajectoryImporter
    from pysrc.trajectory_store import TrajectoryStore

    importer = TrajectoryImporter()
    try:
        trajectories = importer.parse_path(
            args.input,
            format=args.format,
            source=args.source,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=__import__('sys').stderr)
        return 1

    store = TrajectoryStore(db_path=args.db)
    imported = 0
    for traj in trajectories:
        try:
            store.store(traj)
            imported += 1
        except Exception as exc:
            print(f"[WARN] Failed to import: {exc}")
    print(f"Imported {imported} trajectories into {args.db}")
    stats = store.stats()
    print(f"Store now has {stats['total']} total trajectories")
    store.close()
    return 0


async def _trajectory_train(args) -> int:
    """Train a reward model from trajectory data."""
    from pysrc.reward_model import create_scorer
    from pysrc.reward_trainer import RewardTrainer
    from pysrc.trajectory_store import TrajectoryStore
    store = TrajectoryStore(db_path=args.db)
    total = store.count()
    print(f"Training data: {total} trajectories in {args.db}")
    if total < 10:
        print(f"Error: need at least 10 trajectories, have {total}")
        store.close()
        return 1
    scorer = create_scorer(args.backend)
    trainer = RewardTrainer(store, scorer)
    result = trainer.train()
    if "error" in result:
        print(f"Error: {result['error']}")
        store.close()
        return 1
    scorer.save(args.output)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"Model saved to {args.output}")
    store.close()
    return 0


async def _trajectory_cv(args) -> int:
    """Cross-validate reward model on trajectory data."""
    from pysrc.reward_model import create_scorer
    from pysrc.reward_trainer import RewardTrainer
    from pysrc.trajectory_store import TrajectoryStore
    store = TrajectoryStore(db_path=args.db)
    scorer = create_scorer("sklearn")
    trainer = RewardTrainer(store, scorer)
    result = trainer.cross_validate(folds=args.folds)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    store.close()
    return 0


async def main() -> int:
    parser = argparse.ArgumentParser(description="Megatron local data manager")
    parser.add_argument("--config", default="pysrc/model.toml")
    parser.add_argument("--clear-conversations", action="store_true")
    parser.add_argument("--clear-memory", action="store_true")
    parser.add_argument("--confirm", action="store_true")

    sub = parser.add_subparsers(dest="subcommand")

    # trajectory stats
    traj_stats = sub.add_parser("trajectory-stats", help="Show trajectory store statistics")
    traj_stats.add_argument("--db", default=".trajectory/trajectories.db")

    # trajectory export
    traj_export = sub.add_parser("trajectory-export", help="Export trajectories as JSONL")
    traj_export.add_argument("--db", default=".trajectory/trajectories.db")
    traj_export.add_argument("--output", "-o", default="trajectories_export.jsonl")

    # trajectory list
    traj_list = sub.add_parser("trajectory-list", help="List recent trajectories")
    traj_list.add_argument("--db", default=".trajectory/trajectories.db")
    traj_list.add_argument("--limit", type=int, default=20)
    traj_list.add_argument("--source")
    traj_list.add_argument("--success", type=int, choices=[0, 1], default=None)

    # trajectory import-external_agent
    traj_import = sub.add_parser("trajectory-import-external_agent", help="Import External Agent JSONL transcripts")
    traj_import.add_argument("input")
    traj_import.add_argument("--db", default=".trajectory/trajectories.db")

    # trajectory import
    traj_import_any = sub.add_parser(
        "trajectory-import",
        help="Import external text-agent/OpenMegatron/custom trajectory JSON, JSONL, or logs",
    )
    traj_import_any.add_argument("input")
    traj_import_any.add_argument("--db", default=".trajectory/trajectories.db")
    traj_import_any.add_argument(
        "--format",
        default="auto",
        choices=["auto", "agent_text", "openmegatron", "generic"],
        help="Input format hint (default: auto)",
    )
    traj_import_any.add_argument(
        "--source",
        help="Override stored source label, for example agent_text or my_framework",
    )

    # trajectory train
    traj_train = sub.add_parser("trajectory-train", help="Train a reward model from trajectory data")
    traj_train.add_argument("--db", default=".trajectory/trajectories.db")
    traj_train.add_argument("--backend", default="sklearn", choices=["sklearn", "torch"])
    traj_train.add_argument("--output", "-o", default="model.pkl")

    # trajectory cv
    traj_cv = sub.add_parser("trajectory-cv", help="Cross-validate reward model on trajectory data")
    traj_cv.add_argument("--db", default=".trajectory/trajectories.db")
    traj_cv.add_argument("--folds", type=int, default=5)

    args = parser.parse_args()

    # Route to trajectory subcommands
    if args.subcommand == "trajectory-stats":
        return await trajectory_stats(args)
    elif args.subcommand == "trajectory-export":
        return await trajectory_export(args)
    elif args.subcommand == "trajectory-list":
        return await trajectory_list(args)
    elif args.subcommand == "trajectory-import-external_agent":
        return await trajectory_import_external_agent(args)
    elif args.subcommand == "trajectory-import":
        return await trajectory_import(args)
    elif args.subcommand == "trajectory-train":
        return await _trajectory_train(args)
    elif args.subcommand == "trajectory-cv":
        return await _trajectory_cv(args)

    # Original behavior
    if not args.confirm:
        print("[ERROR] Refusing to modify data without --confirm.")
        return 1
    if not args.clear_conversations and not args.clear_memory:
        print("[ERROR] No operation selected.")
        return 1

    config = load_config(Path(args.config))
    result = {}

    if args.clear_conversations:
        deleted = await delete_redis_patterns(config, CONVERSATION_PATTERNS)
        result["redis_conversation_keys"] = deleted
        print(f"[OK] Cleared Redis conversation keys: {deleted}")

    if args.clear_memory:
        pg_deleted = await clear_postgres_memory(config)
        result["postgres_memory_rows"] = pg_deleted
        print(f"[OK] Cleared PostgreSQL memory rows: {json.dumps(pg_deleted, ensure_ascii=False)}")
        try:
            neo4j_deleted = await clear_neo4j_memory(config)
            result["neo4j_nodes"] = neo4j_deleted
            print(f"[OK] Cleared Neo4j memory nodes: {neo4j_deleted}")
        except Exception as exc:
            result["neo4j_error"] = str(exc)
            print(f"[WARN] Neo4j clear skipped: {exc}")
        redis_memory_deleted = await delete_redis_patterns(config, MEMORY_REDIS_PATTERNS)
        result["redis_memory_keys"] = redis_memory_deleted
        print(f"[OK] Cleared Redis memory/cache keys: {redis_memory_deleted}")

    print(json.dumps({"status": "success", "result": result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    raise SystemExit(asyncio.run(main()))
