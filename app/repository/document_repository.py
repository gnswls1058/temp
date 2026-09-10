"""SQLite DocumentRepository 구현."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

from app.models.document import Document
from app.repository.base import DocumentRepository
from app.repository.database import Database

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SqliteDocumentRepository(DocumentRepository):
    def __init__(self, db: Database):
        self.db = db

    def upsert(self, document: Document) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO documents
                    (page_id, space_id, title, body, version, created_at,
                     updated_at, labels, url, clean_text, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(page_id) DO UPDATE SET
                    space_id   = excluded.space_id,
                    title      = excluded.title,
                    body       = excluded.body,
                    version    = excluded.version,
                    created_at = excluded.created_at,
                    updated_at = excluded.updated_at,
                    labels     = excluded.labels,
                    url        = excluded.url,
                    clean_text = excluded.clean_text,
                    fetched_at = excluded.fetched_at
                """,
                (
                    document.page_id,
                    document.space_id,
                    document.title,
                    document.body,
                    document.version,
                    document.created_at,
                    document.updated_at,
                    ",".join(document.labels),
                    document.url,
                    document.clean_text,
                    _now(),
                ),
            )

    def get(self, page_id: str) -> Optional[Document]:
        row = self.db.execute(
            "SELECT * FROM documents WHERE page_id = ?", (page_id,)
        ).fetchone()
        return self._to_document(row) if row else None

    def get_version(self, page_id: str) -> Optional[int]:
        row = self.db.execute(
            "SELECT version FROM documents WHERE page_id = ?", (page_id,)
        ).fetchone()
        return int(row["version"]) if row else None

    def list_all(self) -> List[Document]:
        rows = self.db.execute("SELECT * FROM documents ORDER BY page_id").fetchall()
        return [self._to_document(r) for r in rows]

    def update_clean_text(self, page_id: str, clean_text: str) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE documents SET clean_text = ? WHERE page_id = ?",
                (clean_text, page_id),
            )

    def count(self) -> int:
        return int(self.db.execute("SELECT COUNT(*) c FROM documents").fetchone()["c"])

    def log_error(self, run_id: Optional[str], page_id: Optional[str],
                  stage: str, message: str) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                """INSERT INTO document_errors (run_id, page_id, stage, message, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (run_id, page_id, stage, message[:2000], _now()),
            )
        logger.warning("[%s] page=%s 처리 실패: %s", stage, page_id, message)

    def count_errors(self, run_id: Optional[str] = None) -> int:
        if run_id:
            row = self.db.execute(
                "SELECT COUNT(*) c FROM document_errors WHERE run_id = ?", (run_id,)
            ).fetchone()
        else:
            row = self.db.execute("SELECT COUNT(*) c FROM document_errors").fetchone()
        return int(row["c"])

    @staticmethod
    def _to_document(row) -> Document:
        labels = row["labels"] or ""
        return Document(
            page_id=row["page_id"],
            space_id=row["space_id"],
            title=row["title"],
            body=row["body"],
            version=int(row["version"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            labels=[x for x in labels.split(",") if x],
            url=row["url"],
            clean_text=row["clean_text"],
        )
