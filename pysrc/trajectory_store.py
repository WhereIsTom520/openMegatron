"""SQLite-backed persistence for agent trajectories.

Each trajectory is a (state, action, reward) triple captured from the agent
loop, suitable for future RL training of a companion model.

Follows the same SQLite patterns as literature_graph_db.py:
  - Module-level SQL_* constants
  - sqlite3.Row row factory
  - INSERT OR REPLACE upsert
  - Static _row_to_*() deserialization methods
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Optional

# ── SQL constants ────────────────────────────────────────────────────────────

SQL_CREATE_TRAJECTORIES = """
CREATE TABLE IF NOT EXISTS trajectories (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    user_input TEXT NOT NULL,
    selected_skills TEXT NOT NULL,
    tool_calls_json TEXT NOT NULL,
    reward REAL NOT NULL DEFAULT 0.0,
    confidence REAL NOT NULL DEFAULT 0.0,
    success INTEGER NOT NULL DEFAULT 0,
    tool_count INTEGER NOT NULL DEFAULT 0,
    duration_ms REAL NOT NULL DEFAULT 0.0,
    final_answer TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'openmegatron',
    created_at TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}'
)
"""

SQL_CREATE_INDEX_SESSION = """
CREATE INDEX IF NOT EXISTS idx_trajectories_session
ON trajectories (session_id)
"""

SQL_CREATE_INDEX_CREATED = """
CREATE INDEX IF NOT EXISTS idx_trajectories_created
ON trajectories (created_at)
"""

SQL_CREATE_INDEX_SUCCESS = """
CREATE INDEX IF NOT EXISTS idx_trajectories_success
ON trajectories (success)
"""

SQL_CREATE_INDEX_SOURCE = """
CREATE INDEX IF NOT EXISTS idx_trajectories_source
ON trajectories (source)
"""

SQL_INSERT_TRAJECTORY = """
INSERT OR REPLACE INTO trajectories
    (id, session_id, user_input, selected_skills, tool_calls_json,
     reward, confidence, success, tool_count, duration_ms,
     final_answer, source, created_at, metadata)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

SQL_SELECT_BY_ID = "SELECT * FROM trajectories WHERE id = ?"

SQL_SELECT_ALL = "SELECT * FROM trajectories ORDER BY created_at DESC"

SQL_COUNT_ALL = "SELECT COUNT(*) FROM trajectories"

SQL_SELECT_RECENT = """
SELECT * FROM trajectories
ORDER BY created_at DESC
LIMIT ? OFFSET ?
"""

SQL_DELETE_BY_ID = "DELETE FROM trajectories WHERE id = ?"

# ── Helpers ──────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _make_id() -> str:
    """Generate a unique trajectory ID."""
    return f"traj_{uuid.uuid4().hex[:16]}"


# ── TrajectoryStore ──────────────────────────────────────────────────────────

class TrajectoryStore:
    """SQLite-backed persistence layer for agent trajectories."""

    def __init__(self, db_path: str = ".trajectory/trajectories.db"):
        db_dir = Path(db_path).parent
        db_dir.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        """Create tables and indexes if they don't exist."""
        self._conn.execute(SQL_CREATE_TRAJECTORIES)
        self._conn.execute(SQL_CREATE_INDEX_SESSION)
        self._conn.execute(SQL_CREATE_INDEX_CREATED)
        self._conn.execute(SQL_CREATE_INDEX_SUCCESS)
        self._conn.execute(SQL_CREATE_INDEX_SOURCE)
        self._conn.commit()

    # ── CRUD ─────────────────────────────────────────────────────────────

    def store(self, trajectory: dict) -> str:
        """Insert or replace a trajectory. Returns the trajectory ID."""
        tid = trajectory.get("id") or _make_id()
        self._conn.execute(
            SQL_INSERT_TRAJECTORY,
            (
                tid,
                str(trajectory.get("session_id", "")),
                str(trajectory.get("user_input", "")),
                json.dumps(trajectory.get("selected_skills", []), ensure_ascii=False),
                json.dumps(trajectory.get("tool_calls", []), ensure_ascii=False),
                float(trajectory.get("reward", 0.0)),
                float(trajectory.get("confidence", 0.0)),
                1 if trajectory.get("success") else 0,
                int(trajectory.get("tool_count", 0)),
                float(trajectory.get("duration_ms", 0.0)),
                str(trajectory.get("final_answer", ""))[:2000],
                str(trajectory.get("source", "openmegatron")),
                str(trajectory.get("created_at") or _now_iso()),
                json.dumps(trajectory.get("metadata", {}), ensure_ascii=False),
            ),
        )
        self._conn.commit()
        return tid

    def get(self, tid: str) -> Optional[dict]:
        """Retrieve a single trajectory by ID."""
        row = self._conn.execute(SQL_SELECT_BY_ID, (tid,)).fetchone()
        return self._row_to_dict(row) if row else None

    def query(
        self,
        *,
        session_id: str = None,
        date_from: str = None,
        date_to: str = None,
        success: bool = None,
        source: str = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        """Query trajectories with optional filters."""
        clauses = []
        params: list[Any] = []

        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(session_id)
        if date_from is not None:
            clauses.append("created_at >= ?")
            params.append(date_from)
        if date_to is not None:
            clauses.append("created_at <= ?")
            params.append(date_to)
        if success is not None:
            clauses.append("success = ?")
            params.append(1 if success else 0)
        if source is not None:
            clauses.append("source = ?")
            params.append(source)

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT * FROM trajectories {where} ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def count(
        self,
        *,
        success: bool = None,
        source: str = None,
    ) -> int:
        """Count trajectories, optionally filtered."""
        clauses = []
        params: list[Any] = []

        if success is not None:
            clauses.append("success = ?")
            params.append(1 if success else 0)
        if source is not None:
            clauses.append("source = ?")
            params.append(source)

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT COUNT(*) FROM trajectories {where}"
        row = self._conn.execute(sql, params).fetchone()
        return row[0] if row else 0

    def stats(self) -> dict:
        """Return aggregate statistics."""
        total = self._conn.execute(SQL_COUNT_ALL).fetchone()[0]
        if total == 0:
            return {
                "total": 0,
                "success_rate": 0.0,
                "avg_reward": 0.0,
                "avg_confidence": 0.0,
                "avg_duration_ms": 0.0,
                "by_source": {},
                "by_date": {},
            }

        row = self._conn.execute(
            """SELECT
                COUNT(*) as total,
                AVG(CASE WHEN success = 1 THEN 1.0 ELSE 0.0 END) as success_rate,
                AVG(reward) as avg_reward,
                AVG(confidence) as avg_confidence,
                AVG(duration_ms) as avg_duration_ms
            FROM trajectories"""
        ).fetchone()

        by_source_rows = self._conn.execute(
            "SELECT source, COUNT(*) as cnt FROM trajectories GROUP BY source"
        ).fetchall()

        by_date_rows = self._conn.execute(
            """SELECT substr(created_at, 1, 10) as day, COUNT(*) as cnt
            FROM trajectories GROUP BY day ORDER BY day DESC LIMIT 30"""
        ).fetchall()

        return {
            "total": total,
            "success_rate": round(row["success_rate"] or 0.0, 4),
            "avg_reward": round(row["avg_reward"] or 0.0, 4),
            "avg_confidence": round(row["avg_confidence"] or 0.0, 4),
            "avg_duration_ms": round(row["avg_duration_ms"] or 0.0, 2),
            "by_source": {r["source"]: r["cnt"] for r in by_source_rows},
            "by_date": {r["day"]: r["cnt"] for r in by_date_rows},
        }

    def export_jsonl(self, filepath: str) -> int:
        """Export all trajectories as JSONL. Returns the count written."""
        rows = self._conn.execute(SQL_SELECT_ALL).fetchall()
        count = 0
        with open(filepath, "w", encoding="utf-8") as f:
            for row in rows:
                d = self._row_to_dict(row)
                f.write(json.dumps(d, ensure_ascii=False) + "\n")
                count += 1
        return count

    def delete(self, tid: str) -> bool:
        """Delete a trajectory by ID. Returns True if deleted."""
        cur = self._conn.execute(SQL_DELETE_BY_ID, (tid,))
        self._conn.commit()
        return cur.rowcount > 0

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    # ── Internal ──────────────────────────────────────────────────────────

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        """Convert a database row to a dict with deserialized JSON fields."""
        return {
            "id": row["id"],
            "session_id": row["session_id"],
            "user_input": row["user_input"],
            "selected_skills": json.loads(row["selected_skills"]),
            "tool_calls": json.loads(row["tool_calls_json"]),
            "reward": row["reward"],
            "confidence": row["confidence"],
            "success": bool(row["success"]),
            "tool_count": row["tool_count"],
            "duration_ms": row["duration_ms"],
            "final_answer": row["final_answer"],
            "source": row["source"],
            "created_at": row["created_at"],
            "metadata": json.loads(row["metadata"]),
        }
