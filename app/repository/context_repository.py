"""SQLite ContextRepository 구현 (§20 ~ §22, §32).

대표 Context 선택 시 서로 다른 page / 서로 다른 문장 패턴에서 뽑는다.
같은 문장을 반복하는 문서 하나만으로 근거를 채우지 않는다.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Iterable, List, Optional

from app.models.context import TermContext
from app.repository.base import ContextRepository
from app.repository.database import Database

logger = logging.getLogger(__name__)

_PLACEHOLDER_RE = re.compile(r"<[A-Z]+>")
_NON_WORD_RE = re.compile(r"[^0-9A-Za-z가-힣<>_ ]+")
_DIGIT_RE = re.compile(r"\d+")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _pattern_signature(context: TermContext, term_key: str) -> str:
    """문장 패턴 서명.

    normalized 문장에서 대상 term 을 지우고 남는 뼈대를 비교해
    사실상 동일한 문장 패턴을 중복 선택하지 않도록 한다 (§32).
    """
    base = context.processed_sentence or context.normalized_sentence or context.original_sentence
    base = base.replace(term_key, " ").replace(term_key.replace("_", " "), " ")
    base = _PLACEHOLDER_RE.sub(" ", base)
    base = _NON_WORD_RE.sub(" ", base)
    # 값만 다른 문장(날짜/수량)은 같은 패턴으로 본다.
    base = _DIGIT_RE.sub(" ", base)
    tokens = [t for t in base.split() if t]
    return " ".join(tokens[:8]).lower()


class SqliteContextRepository(ContextRepository):
    def __init__(self, db: Database):
        self.db = db

    def save_many(self, contexts: Iterable[TermContext]) -> int:
        rows = [
            (
                c.term_key,
                c.display_term,
                c.page_id,
                c.page_title,
                c.sentence_index,
                c.original_sentence,
                c.normalized_sentence,
                c.processed_sentence,
                _now(),
            )
            for c in contexts
        ]
        if not rows:
            return 0
        with self.db.transaction() as conn:
            conn.executemany(
                """
                INSERT OR IGNORE INTO term_contexts
                    (term_key, display_term, page_id, page_title, sentence_index,
                     original_sentence, normalized_sentence, processed_sentence, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return len(rows)

    def find_by_term(self, term_key: str, limit: int = 5,
                     diversify_by_page: bool = True,
                     max_per_page: int = 2) -> List[TermContext]:
        rows = self.db.execute(
            """
            SELECT * FROM term_contexts
            WHERE term_key = ?
            ORDER BY page_id, sentence_index
            LIMIT ?
            """,
            (term_key, max(limit * 20, 100)),
        ).fetchall()

        candidates = [self._to_context(r) for r in rows]
        if not candidates:
            return []
        if not diversify_by_page:
            return candidates[:limit]
        return self._diversify(candidates, term_key, limit, max_per_page)

    @staticmethod
    def _diversify(candidates: List[TermContext], term_key: str,
                   limit: int, max_per_page: int) -> List[TermContext]:
        selected: List[TermContext] = []
        seen_patterns: set[str] = set()
        page_counts: dict[str, int] = {}

        # 1차: 서로 다른 page + 서로 다른 문장 패턴 우선
        for ctx in candidates:
            if len(selected) >= limit:
                break
            signature = _pattern_signature(ctx, term_key)
            if signature and signature in seen_patterns:
                continue
            if page_counts.get(ctx.page_id, 0) >= max_per_page:
                continue
            selected.append(ctx)
            seen_patterns.add(signature)
            page_counts[ctx.page_id] = page_counts.get(ctx.page_id, 0) + 1

        # 2차: 그래도 모자라면 패턴 제약만 유지하며 채운다
        if len(selected) < limit:
            chosen = {id(c) for c in selected}
            for ctx in candidates:
                if len(selected) >= limit:
                    break
                if id(ctx) in chosen:
                    continue
                signature = _pattern_signature(ctx, term_key)
                if signature and signature in seen_patterns:
                    continue
                selected.append(ctx)
                seen_patterns.add(signature)

        # 3차: 패턴이 하나뿐인 term 은 최소 1건이라도 제공
        if not selected and candidates:
            selected = candidates[:limit]
        return selected

    def find_for_pair(self, term_a: str, term_b: str, limit_per_term: int = 5,
                      diversify_by_page: bool = True,
                      max_per_page: int = 2) -> tuple[List[TermContext], List[TermContext]]:
        return (
            self.find_by_term(term_a, limit_per_term, diversify_by_page, max_per_page),
            self.find_by_term(term_b, limit_per_term, diversify_by_page, max_per_page),
        )

    def distinct_pattern_count(self, term_key: str, sample: int = 50) -> int:
        """해당 term 이 등장하는 서로 다른 문장 패턴 수 (§38 근거 강도 판단용)."""
        rows = self.db.execute(
            "SELECT * FROM term_contexts WHERE term_key = ? LIMIT ?",
            (term_key, sample),
        ).fetchall()
        signatures = {
            _pattern_signature(self._to_context(r), term_key) for r in rows
        }
        signatures.discard("")
        return len(signatures)

    def clear(self) -> None:
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM term_contexts")

    def count(self) -> int:
        return int(self.db.execute("SELECT COUNT(*) c FROM term_contexts").fetchone()["c"])

    @staticmethod
    def _to_context(row) -> TermContext:
        return TermContext(
            context_id=int(row["id"]),
            term_key=row["term_key"],
            display_term=row["display_term"],
            page_id=row["page_id"],
            page_title=row["page_title"],
            sentence_index=int(row["sentence_index"]),
            original_sentence=row["original_sentence"],
            normalized_sentence=row["normalized_sentence"] or "",
            processed_sentence=row["processed_sentence"] or "",
        )
