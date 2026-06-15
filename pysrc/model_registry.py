"""Model registry — version tracking for trained reward models.

Stores metadata about each trained model version in SQLite, enabling:
  - Version history with accuracy/n_samples/timestamp
  - Automatic best-model selection
  - Model swap tracking for the auto-retrain loop

Follows the same SQLite patterns as trajectory_store.py and literature_graph_db.py.
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

SQL_CREATE_MODELS = """
CREATE TABLE IF NOT EXISTS model_versions (
    id TEXT PRIMARY KEY,
    file_path TEXT NOT NULL,
    backend TEXT NOT NULL,
    accuracy REAL NOT NULL DEFAULT 0.0,
    f1 REAL NOT NULL DEFAULT 0.0,
    n_samples INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'trained',
    created_at TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 0,
    metadata TEXT NOT NULL DEFAULT '{}'
)
"""

SQL_CREATE_INDEX_ACTIVE = """
CREATE INDEX IF NOT EXISTS idx_model_active
ON model_versions (is_active)
"""

SQL_CREATE_INDEX_CREATED = """
CREATE INDEX IF NOT EXISTS idx_model_created
ON model_versions (created_at)
"""

SQL_INSERT_MODEL = """
INSERT OR REPLACE INTO model_versions
    (id, file_path, backend, accuracy, f1, n_samples,
     status, created_at, is_active, metadata)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

SQL_SELECT_ACTIVE = """
SELECT * FROM model_versions WHERE is_active = 1
ORDER BY created_at DESC LIMIT 1
"""

SQL_SELECT_BEST = """
SELECT * FROM model_versions WHERE status = 'trained'
ORDER BY f1 DESC, accuracy DESC LIMIT 1
"""

SQL_SELECT_ALL = """
SELECT * FROM model_versions ORDER BY created_at DESC
"""

SQL_DEACTIVATE_ALL = """
UPDATE model_versions SET is_active = 0
"""

SQL_ACTIVATE = """
UPDATE model_versions SET is_active = 1 WHERE id = ?
"""

SQL_COUNT_MODELS = "SELECT COUNT(*) FROM model_versions"

SQL_SELECT_BY_ID = "SELECT * FROM model_versions WHERE id = ?"


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _make_id() -> str:
    return f"model_{uuid.uuid4().hex[:12]}"


class ModelRegistry:
    """Tracks trained reward model versions in SQLite."""

    def __init__(self, db_path: str = ".trajectory/model_registry.db"):
        db_dir = Path(db_path).parent
        db_dir.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute(SQL_CREATE_MODELS)
        self._conn.execute(SQL_CREATE_INDEX_ACTIVE)
        self._conn.execute(SQL_CREATE_INDEX_CREATED)
        self._conn.commit()

    def register(
        self,
        file_path: str,
        backend: str,
        accuracy: float,
        f1: float,
        n_samples: int,
        metadata: dict = None,
        *,
        activate: bool = True,
        status: str = "trained",
    ) -> str:
        """Register a newly trained model. Returns model ID."""
        if activate:
            self._conn.execute(SQL_DEACTIVATE_ALL)
        # Insert and activate new one
        mid = _make_id()
        self._conn.execute(
            SQL_INSERT_MODEL,
            (
                mid, file_path, backend, accuracy, f1, n_samples,
                status, _now_iso(), 1 if activate else 0,
                json.dumps(metadata or {}, ensure_ascii=False),
            ),
        )
        self._conn.commit()
        return mid

    def get_active(self) -> Optional[dict]:
        """Get the currently active (deployed) model."""
        row = self._conn.execute(SQL_SELECT_ACTIVE).fetchone()
        return self._row_to_dict(row) if row else None

    def get_best(self) -> Optional[dict]:
        """Get the best model by F1 score."""
        row = self._conn.execute(SQL_SELECT_BEST).fetchone()
        return self._row_to_dict(row) if row else None

    def get(self, mid: str) -> Optional[dict]:
        """Get a model by ID."""
        row = self._conn.execute(SQL_SELECT_BY_ID, (mid,)).fetchone()
        return self._row_to_dict(row) if row else None

    def activate(self, mid: str) -> bool:
        """Set a model as the active one. Deactivates all others."""
        existing = self.get(mid)
        if not existing:
            return False
        self._conn.execute(SQL_DEACTIVATE_ALL)
        self._conn.execute(SQL_ACTIVATE, (mid,))
        self._conn.commit()
        return True

    def mark_retired(self, mid: str) -> bool:
        """Mark a model as retired (no longer active)."""
        existing = self.get(mid)
        if not existing:
            return False
        self._conn.execute(
            "UPDATE model_versions SET status = 'retired', is_active = 0 WHERE id = ?",
            (mid,),
        )
        self._conn.commit()
        return True

    def list_all(self) -> list[dict]:
        """List all registered models, newest first."""
        rows = self._conn.execute(SQL_SELECT_ALL).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def count(self) -> int:
        row = self._conn.execute(SQL_COUNT_MODELS).fetchone()
        return row[0] if row else 0

    def close(self) -> None:
        self._conn.close()

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "file_path": row["file_path"],
            "backend": row["backend"],
            "accuracy": row["accuracy"],
            "f1": row["f1"],
            "n_samples": row["n_samples"],
            "status": row["status"],
            "created_at": row["created_at"],
            "is_active": bool(row["is_active"]),
            "metadata": json.loads(row["metadata"]),
        }
