"""SQLite RunRepository 구현 (§48)."""
from __future__ import annotations

from typing import List, Optional

from app.models.relation import IndexingRun
from app.repository.base import RunRepository
from app.repository.database import Database

_FIELDS = (
    "run_id", "started_at", "completed_at", "status", "document_count",
    "changed_document_count", "sentence_count", "phrase_count", "vocabulary_size",
    "term_count", "candidate_count", "validated_count", "active_relation_count",
    "review_relation_count", "rejected_relation_count", "error_count",
    "duration_seconds", "message",
)


class SqliteRunRepository(RunRepository):
    def __init__(self, db: Database):
        self.db = db

    def start(self, run: IndexingRun) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO indexing_runs (run_id, started_at, status)
                   VALUES (?, ?, ?)""",
                (run.run_id, run.started_at, run.status),
            )

    def update(self, run: IndexingRun) -> None:
        assignments = ", ".join(f"{f} = ?" for f in _FIELDS if f != "run_id")
        values = [getattr(run, f) for f in _FIELDS if f != "run_id"]
        values.append(run.run_id)
        with self.db.transaction() as conn:
            conn.execute(
                f"UPDATE indexing_runs SET {assignments} WHERE run_id = ?", tuple(values)
            )

    def get(self, run_id: str) -> Optional[IndexingRun]:
        row = self.db.execute(
            "SELECT * FROM indexing_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        return self._to_run(row) if row else None

    def list_recent(self, limit: int = 10) -> List[IndexingRun]:
        rows = self.db.execute(
            "SELECT * FROM indexing_runs ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [self._to_run(r) for r in rows]

    @staticmethod
    def _to_run(row) -> IndexingRun:
        return IndexingRun(**{f: row[f] for f in _FIELDS if row[f] is not None})
