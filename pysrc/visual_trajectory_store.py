"""Visual trajectory store — SQLite persistence for GUI automation traces.

Stores visual trajectories with screenshot path references, action metadata,
and reward signals. Complements the text TrajectoryStore with vision-specific
fields needed for training vision reward models and DPO preference pairs.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

SQL_CREATE_VISUAL_TRAJECTORIES = """
CREATE TABLE IF NOT EXISTS visual_trajectories (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    user_goal TEXT,
    steps_json TEXT,            -- JSON array of step objects
    step_count INTEGER DEFAULT 0,
    success INTEGER DEFAULT 0,
    final_answer TEXT,
    total_elapsed_ms REAL DEFAULT 0,
    reward REAL DEFAULT 0.5,
    reward_confidence REAL DEFAULT 0.0,
    reward_source TEXT DEFAULT 'rule',  -- 'rule', 'model', 'human'
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
"""

SQL_CREATE_VISUAL_STEPS = """
CREATE TABLE IF NOT EXISTS visual_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trajectory_id TEXT NOT NULL REFERENCES visual_trajectories(id) ON DELETE CASCADE,
    step_index INTEGER NOT NULL,
    screenshot_before TEXT,     -- file path
    screenshot_after TEXT,      -- file path
    action TEXT NOT NULL,       -- 'click', 'type', 'scroll', etc.
    action_params TEXT DEFAULT '{}',
    result_json TEXT DEFAULT '{}',
    elapsed_ms REAL DEFAULT 0,
    visual_diff_score REAL,     -- pixel difference score (computed offline)
    click_accuracy REAL,        -- distance to nearest UI element (computed offline)
    misclick INTEGER DEFAULT 0, -- 1 if click landed on empty area
    created_at TEXT DEFAULT (datetime('now'))
);
"""

SQL_CREATE_PREFERENCE_PAIRS = """
CREATE TABLE IF NOT EXISTS visual_preference_pairs (
    id TEXT PRIMARY KEY,
    trajectory_id TEXT NOT NULL,
    step_index INTEGER NOT NULL,
    chosen_screenshot TEXT,     -- path to screenshot from preferred action
    rejected_screenshot TEXT,   -- path to screenshot from rejected action
    chosen_action TEXT,
    rejected_action TEXT,
    reward_chosen REAL,
    reward_rejected REAL,
    reward_delta REAL,          -- chosen - rejected
    used_for_training INTEGER DEFAULT 0,
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now'))
);
"""

SQL_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_vt_session ON visual_trajectories(session_id);",
    "CREATE INDEX IF NOT EXISTS idx_vt_success ON visual_trajectories(success);",
    "CREATE INDEX IF NOT EXISTS idx_vt_created ON visual_trajectories(created_at);",
    "CREATE INDEX IF NOT EXISTS idx_vs_traj ON visual_steps(trajectory_id);",
    "CREATE INDEX IF NOT EXISTS idx_vpp_used ON visual_preference_pairs(used_for_training);",
    "CREATE INDEX IF NOT EXISTS idx_vpp_delta ON visual_preference_pairs(reward_delta);",
]


class VisualTrajectoryStore:
    """SQLite-backed storage for visual GUI trajectories."""

    def __init__(self, db_path: str = ".trajectory/visual_trajectories.db"):
        self._db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA foreign_keys=ON;")
            conn.execute(SQL_CREATE_VISUAL_TRAJECTORIES)
            conn.execute(SQL_CREATE_VISUAL_STEPS)
            conn.execute(SQL_CREATE_PREFERENCE_PAIRS)
            for idx_sql in SQL_INDEXES:
                try:
                    conn.execute(idx_sql)
                except Exception:
                    pass
            conn.commit()

    # ── CRUD ─────────────────────────────────────────────────────────────────

    def store(self, trajectory: dict) -> str:
        """Store a complete visual trajectory.

        Args:
            trajectory: dict from VisualTrajectoryCollector.to_dict().

        Returns:
            trajectory_id string.
        """
        traj_id = trajectory["trajectory_id"]
        steps = trajectory.get("steps", [])

        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO visual_trajectories
                   (id, session_id, user_goal, steps_json, step_count,
                    success, final_answer, total_elapsed_ms,
                    reward, reward_confidence, reward_source, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    traj_id,
                    trajectory["session_id"],
                    trajectory["user_goal"],
                    json.dumps(steps, ensure_ascii=False),
                    len(steps),
                    1 if trajectory.get("success") else 0,
                    trajectory.get("final_answer", ""),
                    trajectory.get("total_elapsed_ms", 0),
                    trajectory.get("metadata", {}).get("reward", 0.5),
                    trajectory.get("metadata", {}).get("reward_confidence", 0.0),
                    trajectory.get("metadata", {}).get("reward_source", "rule"),
                    json.dumps(trajectory.get("metadata", {}), ensure_ascii=False),
                ),
            )

            # Store individual steps
            for step in steps:
                conn.execute(
                    """INSERT INTO visual_steps
                       (trajectory_id, step_index, screenshot_before,
                        screenshot_after, action, action_params,
                        result_json, elapsed_ms)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        traj_id,
                        step["step_index"],
                        step.get("screenshot_before", ""),
                        step.get("screenshot_after", ""),
                        step["action"],
                        json.dumps(step.get("action_params", {}), ensure_ascii=False),
                        json.dumps(step.get("result", {}), ensure_ascii=False),
                        step.get("elapsed_ms", 0),
                    ),
                )

            conn.commit()
        return traj_id

    def get(self, trajectory_id: str) -> Optional[dict]:
        """Retrieve a single trajectory by ID."""
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM visual_trajectories WHERE id = ?", (trajectory_id,)
            ).fetchone()
            if row is None:
                return None
            return dict(row)

    def query(self, session_id: str = None, success: bool = None,
              min_reward: float = None, limit: int = 100,
              offset: int = 0) -> List[dict]:
        """Query trajectories with optional filters."""
        conditions = []
        params = []

        if session_id:
            conditions.append("session_id = ?")
            params.append(session_id)
        if success is not None:
            conditions.append("success = ?")
            params.append(1 if success else 0)
        if min_reward is not None:
            conditions.append("reward >= ?")
            params.append(min_reward)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"SELECT * FROM visual_trajectories {where} ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    def count(self) -> int:
        with sqlite3.connect(self._db_path) as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM visual_trajectories"
            ).fetchone()[0]

    def stats(self) -> dict:
        """Return aggregate statistics."""
        with sqlite3.connect(self._db_path) as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM visual_trajectories"
            ).fetchone()[0]
            if total == 0:
                return {"total": 0, "success_rate": 0, "avg_reward": 0,
                        "avg_steps": 0, "avg_elapsed_ms": 0}

            success_rate = conn.execute(
                "SELECT CAST(SUM(success) AS REAL) / COUNT(*) FROM visual_trajectories"
            ).fetchone()[0]
            avg_reward = conn.execute(
                "SELECT AVG(reward) FROM visual_trajectories"
            ).fetchone()[0]
            avg_steps = conn.execute(
                "SELECT AVG(step_count) FROM visual_trajectories"
            ).fetchone()[0]
            avg_elapsed = conn.execute(
                "SELECT AVG(total_elapsed_ms) FROM visual_trajectories"
            ).fetchone()[0]

        return {
            "total": total,
            "success_rate": round(success_rate, 4) if success_rate else 0,
            "avg_reward": round(avg_reward, 4) if avg_reward else 0,
            "avg_steps": round(avg_steps, 1) if avg_steps else 0,
            "avg_elapsed_ms": round(avg_elapsed, 1) if avg_elapsed else 0,
        }

    # ── Preference Pairs (for DPO training) ──────────────────────────────────

    def build_preference_pairs(self, min_reward_delta: float = 0.2) -> int:
        """Scan trajectories for preference pairs (chosen vs rejected actions).

        For each step, if two different actions were tried and one got a
        significantly higher reward, create a preference pair for DPO training.

        Returns:
            Number of new preference pairs created.
        """
        count = 0
        with sqlite3.connect(self._db_path) as conn:
            # Find trajectory steps that have both success and failure variants
            rows = conn.execute(
                """SELECT vs1.trajectory_id, vs1.step_index,
                          vs1.screenshot_before AS chosen_ss,
                          vs2.screenshot_before AS rejected_ss,
                          vs1.action AS chosen_action,
                          vs2.action AS rejected_action,
                          vt1.reward AS reward_chosen,
                          vt2.reward AS reward_rejected
                   FROM visual_steps vs1
                   JOIN visual_steps vs2
                     ON vs1.step_index = vs2.step_index
                    AND vs1.trajectory_id != vs2.trajectory_id
                    AND vs1.action = vs2.action
                   JOIN visual_trajectories vt1 ON vs1.trajectory_id = vt1.id
                   JOIN visual_trajectories vt2 ON vs2.trajectory_id = vt2.id
                   WHERE vt1.reward - vt2.reward >= ?
                   LIMIT 1000""",
                (min_reward_delta,),
            ).fetchall()

            for row in rows:
                pair_id = f"vpp_{hashlib.sha256(f'{row[0]}:{row[1]}:{time.time()}'.encode()).hexdigest()[:16]}"
                try:
                    conn.execute(
                        """INSERT OR IGNORE INTO visual_preference_pairs
                           (id, trajectory_id, step_index, chosen_screenshot,
                            rejected_screenshot, chosen_action, rejected_action,
                            reward_chosen, reward_rejected, reward_delta)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            pair_id, row[0], row[1],
                            row[2], row[3], row[4], row[5],
                            row[6], row[7], row[6] - row[7],
                        ),
                    )
                    count += 1
                except Exception:
                    pass

            conn.commit()
        logger.info(f"Built {count} visual preference pairs (min_delta={min_reward_delta})")
        return count

    def get_preference_pairs(self, used_for_training: bool = None,
                             limit: int = 500) -> List[dict]:
        """Retrieve preference pairs for DPO training."""
        conditions = []
        params = []
        if used_for_training is not None:
            conditions.append("used_for_training = ?")
            params.append(1 if used_for_training else 0)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"SELECT * FROM visual_preference_pairs {where} ORDER BY reward_delta DESC LIMIT ?"
        params.append(limit)

        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    def mark_pairs_trained(self, pair_ids: List[str]):
        """Mark preference pairs as used for training."""
        with sqlite3.connect(self._db_path) as conn:
            for pid in pair_ids:
                conn.execute(
                    "UPDATE visual_preference_pairs SET used_for_training = 1 WHERE id = ?",
                    (pid,),
                )
            conn.commit()

    # ── Export ───────────────────────────────────────────────────────────────

    def export_dpo_format(self, output_path: str, limit: int = 500) -> int:
        """Export preference pairs in DPO training format (JSONL).

        Each line: {"chosen": [...], "rejected": [...], "metadata": {...}}

        Returns:
            Number of pairs exported.
        """
        pairs = self.get_preference_pairs(used_for_training=False, limit=limit)
        count = 0
        with open(output_path, "w", encoding="utf-8") as f:
            for pair in pairs:
                # Build DPO format: image + action as the "response"
                chosen = {
                    "images": [pair["chosen_screenshot"]],
                    "action": pair["chosen_action"],
                }
                rejected = {
                    "images": [pair["rejected_screenshot"]],
                    "action": pair["rejected_action"],
                }
                record = {
                    "chosen": json.dumps(chosen, ensure_ascii=False),
                    "rejected": json.dumps(rejected, ensure_ascii=False),
                    "metadata": {
                        "reward_chosen": pair["reward_chosen"],
                        "reward_rejected": pair["reward_rejected"],
                        "reward_delta": pair["reward_delta"],
                    },
                }
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
                count += 1
        logger.info(f"Exported {count} DPO pairs to {output_path}")
        return count

    def close(self):
        pass  # SQLite connections are short-lived


# Need hashlib at module level for preference pair ID generation
import hashlib
