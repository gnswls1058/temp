"""SQLite TermRepository 구현 (§41)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterable, List, Optional

from app.models.enums import EntityType, TermType, safe_enum
from app.models.term import Term
from app.repository.base import TermRepository
from app.repository.database import Database

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SqliteTermRepository(TermRepository):
    def __init__(self, db: Database):
        self.db = db

    def upsert_many(self, terms: Iterable[Term]) -> None:
        now = _now()
        rows = [
            (
                t.term_key,
                t.display_term,
                t.term_type.value if isinstance(t.term_type, TermType) else str(t.term_type),
                t.entity_type.value if isinstance(t.entity_type, EntityType) else t.entity_type,
                t.frequency,
                t.document_frequency,
                round(float(t.sentence_ratio), 4),
                1 if t.is_stopword else 0,
                str(t.excluded_reason or ""),
                t.first_seen_at or now,
                t.last_seen_at or now,
                1 if t.active else 0,
                now,
                now,
            )
            for t in terms
        ]
        if not rows:
            return
        with self.db.transaction() as conn:
            conn.executemany(
                """
                INSERT INTO terms
                    (term_key, display_term, term_type, entity_type, frequency,
                     document_frequency, sentence_ratio, is_stopword, excluded_reason,
                     first_seen_at, last_seen_at, active, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(term_key) DO UPDATE SET
                    display_term       = excluded.display_term,
                    frequency          = excluded.frequency,
                    document_frequency = excluded.document_frequency,
                    sentence_ratio     = excluded.sentence_ratio,
                    is_stopword        = excluded.is_stopword,
                    excluded_reason    = excluded.excluded_reason,
                    last_seen_at       = excluded.last_seen_at,
                    active             = excluded.active,
                    updated_at         = excluded.updated_at
                """,
                rows,
            )

    def get(self, term_key: str) -> Optional[Term]:
        row = self.db.execute(
            "SELECT * FROM terms WHERE term_key = ?", (term_key,)
        ).fetchone()
        return self._to_term(row) if row else None

    def get_id(self, term_key: str) -> Optional[int]:
        row = self.db.execute(
            "SELECT id FROM terms WHERE term_key = ?", (term_key,)
        ).fetchone()
        return int(row["id"]) if row else None

    def update_classification(self, term_key: str, term_type: str,
                              entity_type: Optional[str]) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                """UPDATE terms
                   SET term_type = ?, entity_type = ?, updated_at = ?
                   WHERE term_key = ?""",
                (term_type, entity_type, _now(), term_key),
            )

    def deactivate(self, term_key: str) -> None:
        """검수에서 제거된 용어를 비활성화한다. 이력은 남긴다."""
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE terms SET active = 0, updated_at = ? WHERE term_key = ?",
                (_now(), term_key),
            )

    def retain_only(self, term_keys: Iterable[str]) -> int:
        """이번 실행의 corpus 에 없는 term 을 지운다.

        terms 는 upsert 라서 그냥 두면 과거 실행(예: user dictionary 적용 전)의
        토큰이 남아 후보 생성에 계속 끼어든다. 살아남은 term 의 LLM 분류는
        보존해야 하므로 테이블 전체를 비우지 않고 사라진 것만 제거한다.
        term_relations 는 ON DELETE CASCADE 로 함께 정리된다.
        """
        keys = list(dict.fromkeys(term_keys))
        if not keys:
            return 0
        with self.db.transaction() as conn:
            conn.execute("CREATE TEMP TABLE IF NOT EXISTS _keep(term_key TEXT PRIMARY KEY)")
            conn.execute("DELETE FROM _keep")
            conn.executemany("INSERT OR IGNORE INTO _keep(term_key) VALUES (?)",
                             [(k,) for k in keys])
            cursor = conn.execute(
                "DELETE FROM terms WHERE term_key NOT IN (SELECT term_key FROM _keep)"
            )
            removed = cursor.rowcount or 0
            conn.execute("DROP TABLE IF EXISTS _keep")
        if removed:
            logger.info("이전 실행에만 있던 term %s개를 정리했습니다.", removed)
        return removed

    def list_all(self) -> List[Term]:
        rows = self.db.execute(
            "SELECT * FROM terms ORDER BY frequency DESC, term_key"
        ).fetchall()
        return [self._to_term(r) for r in rows]

    def count(self) -> int:
        return int(self.db.execute("SELECT COUNT(*) c FROM terms").fetchone()["c"])

    def clear(self) -> None:
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM terms")

    @staticmethod
    def _to_term(row) -> Term:
        return Term(
            term_id=int(row["id"]),
            term_key=row["term_key"],
            display_term=row["display_term"],
            term_type=safe_enum(TermType, row["term_type"], TermType.UNKNOWN),
            entity_type=safe_enum(EntityType, row["entity_type"], None)
            if row["entity_type"] else None,
            frequency=int(row["frequency"]),
            document_frequency=int(row["document_frequency"]),
            sentence_ratio=float(row["sentence_ratio"] or 0.0),
            is_stopword=bool(row["is_stopword"]),
            excluded_reason=str(row["excluded_reason"] or ""),
            first_seen_at=row["first_seen_at"],
            last_seen_at=row["last_seen_at"],
            active=bool(row["active"]),
        )
