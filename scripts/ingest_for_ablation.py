"""Ingest project documents into the RAG pipeline for ablation testing.

Usage:
    python scripts/ingest_for_ablation.py
    python scripts/ingest_for_ablation.py --dir docs/
    python scripts/ingest_for_ablation.py --file README.md
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

# Ensure pysrc/ is on sys.path
_SELF_DIR = Path(__file__).resolve().parent
_PROJECT_DIR = _SELF_DIR.parent
_PYSRC_DIR = _PROJECT_DIR / "pysrc"
sys.path.insert(0, str(_PYSRC_DIR))

import asyncpg
import numpy as np
from rag_ingest import (
    EmbeddingProvider, chunk_text, extract_entities_deterministic,
    SUPPORTED_EXTENSIONS, _get_parser,
)

# ── Config ────────────────────────────────────────────────────────────────────

def load_config():
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib
    config_path = _PYSRC_DIR / "model.toml"
    if config_path.exists():
        with open(config_path, "rb") as f:
            return tomllib.load(f)
    return {}


async def get_pg_conn(config: dict):
    pg_cfg = config.get("postgres") or config.get("postgresql") or {}
    return await asyncpg.connect(
        host=pg_cfg.get("host", "localhost"),
        port=pg_cfg.get("port", 54320),
        user=pg_cfg.get("user", "root"),
        password=pg_cfg.get("password", "root"),
        database=pg_cfg.get("database", "root"),
    )


async def ensure_tables(conn, embed_dim: int = 512):
    """Ensure rag_documents and rag_chunks tables exist with correct vector dimension."""
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS rag_documents (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            source TEXT,
            file_type TEXT,
            owner_id TEXT DEFAULT 'default',
            scope TEXT DEFAULT 'shared',
            metadata JSONB DEFAULT '{}',
            chunk_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    # Drop old chunks table if dimension doesn't match, then recreate
    await conn.execute("DROP TABLE IF EXISTS rag_chunks CASCADE")
    await conn.execute(f"""
        CREATE TABLE rag_chunks (
            id TEXT PRIMARY KEY,
            doc_id TEXT REFERENCES rag_documents(id) ON DELETE CASCADE,
            chunk_index INTEGER NOT NULL,
            text TEXT NOT NULL,
            embedding vector({embed_dim}),
            metadata JSONB DEFAULT '{{}}',
            owner_id TEXT DEFAULT 'default',
            scope TEXT DEFAULT 'shared',
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    # Ensure pgvector extension
    await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")


async def ingest_file(filepath: str, conn, embedder: EmbeddingProvider, config: dict):
    """Ingest a single file: parse → chunk → embed → store in PG."""
    path = Path(filepath)
    ext = path.suffix.lower()

    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported file type: {ext}")

    # Parse
    parser = _get_parser(ext)
    raw_text = parser(str(path))

    # Chunk
    chunks = chunk_text(raw_text)
    if not chunks:
        raise ValueError(f"No extractable text in {filepath}")

    # Generate doc_id
    import hashlib
    doc_id = hashlib.sha256(
        f"ablation:{path.name}:{time.time()}".encode()
    ).hexdigest()[:16]

    # Embed all chunks
    chunk_texts = [c["text"] for c in chunks]
    all_embeddings = []
    BATCH_SIZE = 32
    for i in range(0, len(chunk_texts), BATCH_SIZE):
        batch = chunk_texts[i:i + BATCH_SIZE]
        emb = await embedder.embed(batch, config)
        all_embeddings.append(emb)
    embeddings = np.concatenate(all_embeddings, axis=0) if all_embeddings else np.array([])

    # Store document record
    await conn.execute(
        """INSERT INTO rag_documents (id, title, source, file_type, owner_id, scope, metadata, chunk_count)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
           ON CONFLICT (id) DO UPDATE SET chunk_count = $8, metadata = $7""",
        doc_id, path.name, str(path), ext, "default", "shared",
        json.dumps({}), len(chunks),
    )

    # Store chunks with embeddings
    for i, ch in enumerate(chunks):
        chunk_id = f"{doc_id}:{i}"
        emb_list = embeddings[i].tolist() if i < len(embeddings) else [0.0] * 1024
        await conn.execute(
            """INSERT INTO rag_chunks (id, doc_id, chunk_index, text, embedding, metadata, owner_id, scope)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
               ON CONFLICT (id) DO UPDATE SET text = $4, embedding = $5""",
            chunk_id, doc_id, i, ch["text"],
            json.dumps(emb_list),
            json.dumps({"start_char": ch.get("start_char", 0), "end_char": ch.get("end_char", 0)}),
            "default", "shared",
        )

    # Extract entities
    entities = extract_entities_deterministic(raw_text)
    entity_count = len({(e["name"].lower(), e["type"]) for e in entities})

    return {
        "doc_id": doc_id,
        "title": path.name,
        "chunk_count": len(chunks),
        "entity_count": entity_count,
    }


async def main():
    config = load_config()
    embedder = EmbeddingProvider(config)
    conn = await get_pg_conn(config)
    await ensure_tables(conn)

    try:
        # Files to ingest
        project_dir = _PROJECT_DIR
        files_to_ingest = [
            str(project_dir / "README.md"),
            str(project_dir / "README.md"),
        ]

        # Also ingest docs/
        docs_dir = project_dir / "docs"
        if docs_dir.exists():
            for f in docs_dir.glob("**/*.md"):
                files_to_ingest.append(str(f))

        total_chunks = 0
        total_entities = 0
        count = 0

        for fp in files_to_ingest:
            if not os.path.isfile(fp):
                continue
            try:
                result = await ingest_file(fp, conn, embedder, config)
                total_chunks += result["chunk_count"]
                total_entities += result["entity_count"]
                count += 1
                rel_path = Path(fp).relative_to(project_dir)
                print(f"  OK  [{rel_path}] → {result['chunk_count']} chunks, {result['entity_count']} entities")
            except Exception as e:
                rel_path = Path(fp).relative_to(project_dir)
                print(f"  FAIL [{rel_path}] → {e}")

        print(f"\n{'='*50}")
        print(f"Ingested {count} documents: {total_chunks} chunks, {total_entities} entities")

        # Verify
        doc_count = await conn.fetchval("SELECT COUNT(*) FROM rag_documents")
        chunk_count = await conn.fetchval("SELECT COUNT(*) FROM rag_chunks")
        print(f"Database state: {doc_count} docs, {chunk_count} chunks in rag_* tables")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
