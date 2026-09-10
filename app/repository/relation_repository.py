"""SQLite RelationRepository 구현 (§26, §42, §43)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional, Sequence

from app.models.enums import (
    CandidateStatus,
    Confidence,
    RelationStatus,
    RelationType,
    safe_enum,
)
from app.models.relation import CandidateRelation, TermRelation
from app.repository.base import RelationRepository
from app.repository.database import Database

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SqliteRelationRepository(RelationRepository):
    def __init__(self, db: Database):
        self.db = db

    # ------------------------------------------------------------------
    # Candidate
    # ------------------------------------------------------------------
    def save_candidates(self, candidates: Sequence[CandidateRelation]) -> int:
        now = _now()
        rows = []
        for c in candidates:
            a, b = CandidateRelation.canonical_pair(c.term_a, c.term_b)
            rows.append((c.run_id, a, b, f"{a}||{b}", float(c.fasttext_similarity),
                         ",".join(c.sources), c.rank_a_to_b, c.rank_b_to_a,
                         float(c.lexical_score), float(c.priority_score),
                         c.status.value, now, now))
        if not rows:
            return 0
        with self.db.transaction() as conn:
            conn.executemany(
                """
                INSERT INTO candidate_relations
                    (run_id, term_a, term_b, pair_key, fasttext_similarity,
                     sources, rank_a_to_b, rank_b_to_a, lexical_score, priority_score,
                     status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(pair_key) DO UPDATE SET
                    run_id              = excluded.run_id,
                    fasttext_similarity = MAX(candidate_relations.fasttext_similarity,
                                              excluded.fasttext_similarity),
                    sources             = excluded.sources,
                    rank_a_to_b         = excluded.rank_a_to_b,
                    rank_b_to_a         = excluded.rank_b_to_a,
                    lexical_score       = excluded.lexical_score,
                    priority_score      = excluded.priority_score,
                    status              = excluded.status,
                    updated_at          = excluded.updated_at
                """,
                rows,
            )
        return len(rows)

    def list_candidates(self, status: Optional[str] = None) -> List[CandidateRelation]:
        if status:
            rows = self.db.execute(
                """SELECT * FROM candidate_relations WHERE status = ?
                   ORDER BY priority_score DESC, id""",
                (status,),
            ).fetchall()
        else:
            rows = self.db.execute(
                "SELECT * FROM candidate_relations ORDER BY priority_score DESC, id"
            ).fetchall()
        return [
            CandidateRelation(
                candidate_id=int(r["id"]),
                run_id=r["run_id"],
                term_a=r["term_a"],
                term_b=r["term_b"],
                fasttext_similarity=float(r["fasttext_similarity"]),
                status=safe_enum(CandidateStatus, r["status"], CandidateStatus.PENDING),
                sources=[x for x in str(r["sources"] or "").split(",") if x],
                rank_a_to_b=r["rank_a_to_b"],
                rank_b_to_a=r["rank_b_to_a"],
                lexical_score=float(r["lexical_score"] or 0.0),
                priority_score=float(r["priority_score"] or 0.0),
            )
            for r in rows
        ]

    def update_candidate_status(self, pair_key: str, status: str) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE candidate_relations SET status = ?, updated_at = ? WHERE pair_key = ?",
                (status, _now(), pair_key),
            )

    def clear_candidates(self) -> None:
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM candidate_relations")

    def count_candidates(self) -> int:
        return int(
            self.db.execute("SELECT COUNT(*) c FROM candidate_relations").fetchone()["c"]
        )

    # ------------------------------------------------------------------
    # Relation
    # ------------------------------------------------------------------
    def save_relation(self, relation: TermRelation) -> Optional[int]:
        source_id = self._term_id(relation.source_term_key)
        target_id = self._term_id(relation.target_term_key)
        if source_id is None or target_id is None:
            logger.warning(
                "관계 저장 실패 - term 미등록: %s / %s",
                relation.source_term_key, relation.target_term_key,
            )
            return None

        now = _now()
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO term_relations
                    (source_term_id, target_term_id, relation_type, fasttext_similarity,
                     llm_confidence, status, safe_expansion, reason, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_term_id, target_term_id, relation_type) DO UPDATE SET
                    fasttext_similarity = excluded.fasttext_similarity,
                    llm_confidence      = excluded.llm_confidence,
                    status              = excluded.status,
                    safe_expansion      = excluded.safe_expansion,
                    reason              = excluded.reason,
                    updated_at          = excluded.updated_at
                """,
                (
                    source_id, target_id,
                    relation.relation_type.value,
                    relation.fasttext_similarity,
                    relation.llm_confidence.value,
                    relation.status.value,
                    1 if relation.safe_expansion else 0,
                    relation.reason,
                    now, now,
                ),
            )
            row = conn.execute(
                """SELECT id FROM term_relations
                   WHERE source_term_id = ? AND target_term_id = ? AND relation_type = ?""",
                (source_id, target_id, relation.relation_type.value),
            ).fetchone()
            relation_id = int(row["id"]) if row else None

            # 근거 Context 추적 (§43)
            if relation_id and relation.evidence_context_ids:
                conn.executemany(
                    """INSERT OR IGNORE INTO relation_evidence (relation_id, context_id)
                       VALUES (?, ?)""",
                    [(relation_id, cid) for cid in relation.evidence_context_ids],
                )
        return relation_id

    def list_relations(self, status: Optional[str] = None) -> List[TermRelation]:
        sql = """
            SELECT r.*, s.term_key AS source_key, t.term_key AS target_key
            FROM term_relations r
            JOIN terms s ON s.id = r.source_term_id
            JOIN terms t ON t.id = r.target_term_id
        """
        params: tuple = ()
        if status:
            sql += " WHERE r.status = ?"
            params = (status,)
        sql += " ORDER BY r.fasttext_similarity DESC"
        rows = self.db.execute(sql, params).fetchall()
        return [self._to_relation(r) for r in rows]

    def relations_for_term(self, term_key: str,
                           statuses: Optional[Sequence[str]] = None) -> List[TermRelation]:
        sql = """
            SELECT r.*, s.term_key AS source_key, t.term_key AS target_key
            FROM term_relations r
            JOIN terms s ON s.id = r.source_term_id
            JOIN terms t ON t.id = r.target_term_id
            WHERE s.term_key = ?
        """
        params: list = [term_key]
        if statuses:
            placeholders = ",".join("?" * len(statuses))
            sql += f" AND r.status IN ({placeholders})"
            params.extend(statuses)
        rows = self.db.execute(sql, tuple(params)).fetchall()
        return [self._to_relation(r) for r in rows]

    def evidence_context_ids(self, relation_id: int) -> List[int]:
        rows = self.db.execute(
            "SELECT context_id FROM relation_evidence WHERE relation_id = ?",
            (relation_id,),
        ).fetchall()
        return [int(r["context_id"]) for r in rows]

    def count_by_status(self, status: str) -> int:
        row = self.db.execute(
            "SELECT COUNT(*) c FROM term_relations WHERE status = ?", (status,)
        ).fetchone()
        return int(row["c"])

    def delete_relation(self, relation_id: int) -> None:
        """관계 한 건과 그 근거 연결을 삭제한다 (검수 제거용)."""
        with self.db.transaction() as conn:
            conn.execute(
                "DELETE FROM relation_evidence WHERE relation_id = ?", (relation_id,)
            )
            conn.execute("DELETE FROM term_relations WHERE id = ?", (relation_id,))

    def clear_relations(self) -> None:
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM relation_evidence")
            conn.execute("DELETE FROM term_relations")

    # ------------------------------------------------------------------
    def _term_id(self, term_key: str) -> Optional[int]:
        row = self.db.execute(
            "SELECT id FROM terms WHERE term_key = ?", (term_key,)
        ).fetchone()
        return int(row["id"]) if row else None

    def _to_relation(self, row) -> TermRelation:
        return TermRelation(
            relation_id=int(row["id"]),
            source_term_key=row["source_key"],
            target_term_key=row["target_key"],
            relation_type=safe_enum(RelationType, row["relation_type"], RelationType.UNKNOWN),
            status=safe_enum(RelationStatus, row["status"], RelationStatus.REVIEW),
            fasttext_similarity=float(row["fasttext_similarity"] or 0.0),
            llm_confidence=safe_enum(Confidence, row["llm_confidence"], Confidence.LOW),
            reason=row["reason"] or "",
            safe_expansion=bool(row["safe_expansion"]),
            evidence_context_ids=self.evidence_context_ids(int(row["id"])),
        )
