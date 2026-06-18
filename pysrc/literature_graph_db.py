"""SQLite-backed persistence for the literature knowledge graph."""

import json
import sqlite3
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ── SQL constants ────────────────────────────────────────────────────────────

SQL_CREATE_PAPERS = """
CREATE TABLE IF NOT EXISTS papers (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    authors TEXT NOT NULL,
    year INTEGER,
    venue TEXT,
    abstract TEXT,
    keywords TEXT,
    metadata TEXT
)
"""

SQL_CREATE_AUTHORS = """
CREATE TABLE IF NOT EXISTS authors (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    affiliation TEXT,
    metadata TEXT
)
"""

SQL_CREATE_CITATIONS = """
CREATE TABLE IF NOT EXISTS citations (
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    context TEXT,
    PRIMARY KEY (source_id, target_id),
    FOREIGN KEY (source_id) REFERENCES papers(id),
    FOREIGN KEY (target_id) REFERENCES papers(id)
)
"""

SQL_INSERT_PAPER = """
INSERT OR REPLACE INTO papers (id, title, authors, year, venue, abstract, keywords, metadata)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
"""

SQL_INSERT_AUTHOR = """
INSERT OR REPLACE INTO authors (id, name, affiliation, metadata)
VALUES (?, ?, ?, ?)
"""

SQL_INSERT_CITATION = """
INSERT OR REPLACE INTO citations (source_id, target_id, context)
VALUES (?, ?, ?)
"""

SQL_SELECT_PAPER = "SELECT * FROM papers WHERE id = ?"
SQL_SELECT_AUTHOR = "SELECT * FROM authors WHERE id = ?"
SQL_SELECT_CITATIONS_BY_SOURCE = "SELECT * FROM citations WHERE source_id = ?"
SQL_SELECT_CITATIONS_BY_TARGET = "SELECT * FROM citations WHERE target_id = ?"
SQL_SEARCH_PAPERS = "SELECT * FROM papers WHERE title LIKE ? OR abstract LIKE ?"
SQL_SEARCH_AUTHORS = "SELECT * FROM authors WHERE name LIKE ?"
SQL_DELETE_PAPER = "DELETE FROM papers WHERE id = ?"
SQL_DELETE_AUTHOR = "DELETE FROM authors WHERE id = ?"
SQL_DELETE_CITATION = "DELETE FROM citations WHERE source_id = ? AND target_id = ?"
SQL_COUNT_PAPERS = "SELECT COUNT(*) FROM papers"
SQL_COUNT_AUTHORS = "SELECT COUNT(*) FROM authors"
SQL_COUNT_CITATIONS = "SELECT COUNT(*) FROM citations"


class LiteratureGraphDB:
    """SQLite-backed persistence layer for the literature knowledge graph.

    When an ontology_service is provided, papers, authors, venues and
    citations are also synced to the ontology graph (PostgreSQL + Neo4j)
    for cross-domain queries.  SQLite remains the authoritative store.
    """

    def __init__(self, db_path: str = ":memory:", ontology_service=None):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._ontology = ontology_service  # MemoryService instance (optional)
        self._init_schema()

    def _init_schema(self) -> None:
        """Create tables if they don't exist."""
        self._conn.executescript(
            SQL_CREATE_PAPERS + SQL_CREATE_AUTHORS + SQL_CREATE_CITATIONS
        )
        self._conn.commit()

    # ── Paper CRUD ────────────────────────────────────────────────────────

    def add_paper(self, paper: dict) -> None:
        """Insert or replace a paper."""
        self._conn.execute(
            SQL_INSERT_PAPER,
            (
                paper["id"],
                paper["title"],
                json.dumps(paper.get("authors", [])),
                paper.get("year"),
                paper.get("venue"),
                paper.get("abstract"),
                json.dumps(paper.get("keywords", [])),
                json.dumps(paper.get("metadata", {})),
            ),
        )
        self._conn.commit()
        self._sync_paper_to_ontology(paper)

    def _sync_paper_to_ontology(self, paper: dict) -> None:
        """Mirror a paper to the ontology graph (fire-and-forget)."""
        svc = self._ontology
        if svc is None:
            return
        try:
            import asyncio
            paper_id = paper["id"]
            title = paper.get("title", "")[:500]
            authors = paper.get("authors", [])
            year = paper.get("year")
            venue_name = paper.get("venue", "").strip()
            abstract = (paper.get("abstract") or "")[:2000]
            keywords = paper.get("keywords", [])

            async def _sync():
                pg = getattr(svc, 'pg_pool', None)
                if pg is None:
                    return
                async with pg.acquire() as conn:
                    # Paper node
                    p_id = svc.ontology_node_id("paper", paper_id)
                    await svc._upsert_ontology_node(conn, p_id, "paper", title, {
                        "paper_id": paper_id,
                        "title": title,
                        "year": year,
                        "venue": venue_name,
                        "abstract": abstract,
                        "keywords": keywords if isinstance(keywords, list) else [],
                    })
                    # Venue node
                    if venue_name:
                        v_id = svc.ontology_node_id("venue", venue_name)
                        await svc._upsert_ontology_node(conn, v_id, "venue", venue_name, {
                            "paper_id": paper_id,
                        })
                        await svc._upsert_link(conn, p_id, v_id, "published_in", 1.0,
                            {"paper_id": paper_id, "venue": venue_name})
                    # Author nodes + authored_by relations
                    if isinstance(authors, list):
                        for author in authors:
                            if isinstance(author, dict):
                                author_name = author.get("name", "") or str(author)
                                author_id = author.get("id") or author_name
                            elif isinstance(author, str):
                                author_name = author
                                author_id = author_name
                            else:
                                continue
                            a_id = svc.ontology_node_id("author", author_id)
                            await svc._upsert_ontology_node(conn, a_id, "author", author_name[:200], {
                                "author_id": author_id,
                            })
                            await svc._upsert_link(conn, p_id, a_id, "authored_by", 1.0,
                                {"paper_id": paper_id, "author": author_name[:200]})
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.ensure_future(_sync())
                else:
                    loop.run_until_complete(_sync())
            except RuntimeError:
                asyncio.run(_sync())
        except Exception:
            logger.debug("Ontology sync for paper %s skipped", paper.get("id"), exc_info=True)

    def get_paper(self, paper_id: str) -> Optional[dict]:
        """Retrieve a paper by ID."""
        row = self._conn.execute(SQL_SELECT_PAPER, (paper_id,)).fetchone()
        return self._row_to_paper(row) if row else None

    def search_papers(self, query: str) -> list[dict]:
        """Full-text-ish search over title and abstract."""
        pattern = f"%{query}%"
        rows = self._conn.execute(SQL_SEARCH_PAPERS, (pattern, pattern)).fetchall()
        return [self._row_to_paper(r) for r in rows]

    def delete_paper(self, paper_id: str) -> bool:
        """Delete a paper. Returns True if it existed."""
        cur = self._conn.execute(SQL_DELETE_PAPER, (paper_id,))
        self._conn.commit()
        return cur.rowcount > 0

    # ── Author CRUD ───────────────────────────────────────────────────────

    def add_author(self, author: dict) -> None:
        """Insert or replace an author."""
        self._conn.execute(
            SQL_INSERT_AUTHOR,
            (
                author["id"],
                author["name"],
                author.get("affiliation"),
                json.dumps(author.get("metadata", {})),
            ),
        )
        self._conn.commit()
        self._sync_author_to_ontology(author)

    def _sync_author_to_ontology(self, author: dict) -> None:
        svc = self._ontology
        if svc is None:
            return
        try:
            import asyncio
            async def _sync():
                pg = getattr(svc, 'pg_pool', None)
                if pg is None:
                    return
                async with pg.acquire() as conn:
                    a_id = svc.ontology_node_id("author", author["id"])
                    await svc._upsert_ontology_node(conn, a_id, "author", author["name"][:200], {
                        "author_id": author["id"],
                        "affiliation": author.get("affiliation", ""),
                    })
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.ensure_future(_sync())
                else:
                    loop.run_until_complete(_sync())
            except RuntimeError:
                asyncio.run(_sync())
        except Exception:
            logger.debug("Ontology sync for author %s skipped", author.get("id"), exc_info=True)

    def get_author(self, author_id: str) -> Optional[dict]:
        """Retrieve an author by ID."""
        row = self._conn.execute(SQL_SELECT_AUTHOR, (author_id,)).fetchone()
        return self._row_to_author(row) if row else None

    def search_authors(self, query: str) -> list[dict]:
        """Search authors by name."""
        pattern = f"%{query}%"
        rows = self._conn.execute(SQL_SEARCH_AUTHORS, (pattern,)).fetchall()
        return [self._row_to_author(r) for r in rows]

    def delete_author(self, author_id: str) -> bool:
        """Delete an author. Returns True if it existed."""
        cur = self._conn.execute(SQL_DELETE_AUTHOR, (author_id,))
        self._conn.commit()
        return cur.rowcount > 0

    # ── Citation CRUD ─────────────────────────────────────────────────────

    def add_citation(self, source_id: str, target_id: str, context: Optional[str] = None) -> None:
        """Add a citation edge."""
        self._conn.execute(SQL_INSERT_CITATION, (source_id, target_id, context))
        self._conn.commit()
        self._sync_citation_to_ontology(source_id, target_id, context)

    def _sync_citation_to_ontology(self, source_id: str, target_id: str, context: Optional[str]) -> None:
        svc = self._ontology
        if svc is None:
            return
        try:
            import asyncio
            async def _sync():
                pg = getattr(svc, 'pg_pool', None)
                if pg is None:
                    return
                async with pg.acquire() as conn:
                    src_id = svc.ontology_node_id("paper", source_id)
                    tgt_id = svc.ontology_node_id("paper", target_id)
                    await svc._upsert_link(conn, src_id, tgt_id, "cites", 1.0,
                        {"source_id": source_id, "target_id": target_id, "context": context or ""})
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.ensure_future(_sync())
                else:
                    loop.run_until_complete(_sync())
            except RuntimeError:
                asyncio.run(_sync())
        except Exception:
            logger.debug("Ontology sync for citation %s->%s skipped", source_id, target_id, exc_info=True)

    def get_citations_from(self, paper_id: str) -> list[dict]:
        """Get papers cited by this paper."""
        rows = self._conn.execute(SQL_SELECT_CITATIONS_BY_SOURCE, (paper_id,)).fetchall()
        return [self._row_to_citation(r) for r in rows]

    def get_citations_to(self, paper_id: str) -> list[dict]:
        """Get papers that cite this paper."""
        rows = self._conn.execute(SQL_SELECT_CITATIONS_BY_TARGET, (paper_id,)).fetchall()
        return [self._row_to_citation(r) for r in rows]

    def delete_citation(self, source_id: str, target_id: str) -> bool:
        """Delete a citation edge. Returns True if it existed."""
        cur = self._conn.execute(SQL_DELETE_CITATION, (source_id, target_id))
        self._conn.commit()
        return cur.rowcount > 0

    # ── Stats ─────────────────────────────────────────────────────────────

    def stats(self) -> dict:
        """Return row counts."""
        return {
            "papers": self._conn.execute(SQL_COUNT_PAPERS).fetchone()[0],
            "authors": self._conn.execute(SQL_COUNT_AUTHORS).fetchone()[0],
            "citations": self._conn.execute(SQL_COUNT_CITATIONS).fetchone()[0],
        }

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _row_to_paper(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "title": row["title"],
            "authors": json.loads(row["authors"]),
            "year": row["year"],
            "venue": row["venue"],
            "abstract": row["abstract"],
            "keywords": json.loads(row["keywords"]),
            "metadata": json.loads(row["metadata"]),
        }

    @staticmethod
    def _row_to_author(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "name": row["name"],
            "affiliation": row["affiliation"],
            "metadata": json.loads(row["metadata"]),
        }

    @staticmethod
    def _row_to_citation(row: sqlite3.Row) -> dict:
        return {
            "source_id": row["source_id"],
            "target_id": row["target_id"],
            "context": row["context"],
        }
