"""SQLite 연결 및 스키마 (§6).

V1 저장소는 SQLite 이지만 Repository Interface 를 통해서만 접근하므로
추후 PostgreSQL 등으로 교체할 수 있다.
"""
from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS documents (
    page_id        TEXT PRIMARY KEY,
    space_id       TEXT,
    title          TEXT NOT NULL,
    body           TEXT NOT NULL,
    version        INTEGER NOT NULL,
    created_at     TEXT,
    updated_at     TEXT,
    labels         TEXT,
    url            TEXT,
    clean_text     TEXT,
    fetched_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_documents_space ON documents(space_id);

CREATE TABLE IF NOT EXISTS document_errors (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT,
    page_id     TEXT,
    stage       TEXT NOT NULL,
    message     TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS term_contexts (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    term_key            TEXT NOT NULL,
    display_term        TEXT NOT NULL,
    page_id             TEXT NOT NULL,
    page_title          TEXT,
    sentence_index      INTEGER NOT NULL,
    original_sentence   TEXT NOT NULL,
    normalized_sentence TEXT,
    processed_sentence  TEXT,
    created_at          TEXT NOT NULL,
    UNIQUE(term_key, page_id, sentence_index)
);
CREATE INDEX IF NOT EXISTS idx_contexts_term ON term_contexts(term_key);
CREATE INDEX IF NOT EXISTS idx_contexts_page ON term_contexts(page_id);

CREATE TABLE IF NOT EXISTS terms (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    term_key           TEXT NOT NULL UNIQUE,
    display_term       TEXT NOT NULL,
    term_type          TEXT NOT NULL DEFAULT 'UNKNOWN',
    entity_type        TEXT,
    frequency          INTEGER NOT NULL DEFAULT 0,
    document_frequency INTEGER NOT NULL DEFAULT 0,
    sentence_ratio     REAL NOT NULL DEFAULT 0,
    is_stopword        INTEGER NOT NULL DEFAULT 0,
    excluded_reason    TEXT NOT NULL DEFAULT '',
    first_seen_at      TEXT,
    last_seen_at       TEXT,
    active             INTEGER NOT NULL DEFAULT 1,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_terms_type ON terms(term_type);

CREATE TABLE IF NOT EXISTS candidate_relations (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id              TEXT,
    term_a              TEXT NOT NULL,
    term_b              TEXT NOT NULL,
    pair_key            TEXT NOT NULL UNIQUE,
    fasttext_similarity REAL NOT NULL,
    sources             TEXT NOT NULL DEFAULT '',
    rank_a_to_b         INTEGER,
    rank_b_to_a         INTEGER,
    lexical_score       REAL NOT NULL DEFAULT 0,
    priority_score      REAL NOT NULL DEFAULT 0,
    status              TEXT NOT NULL DEFAULT 'PENDING',
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_candidates_status ON candidate_relations(status);

CREATE TABLE IF NOT EXISTS term_relations (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    source_term_id      INTEGER NOT NULL REFERENCES terms(id) ON DELETE CASCADE,
    target_term_id      INTEGER NOT NULL REFERENCES terms(id) ON DELETE CASCADE,
    relation_type       TEXT NOT NULL,
    fasttext_similarity REAL,
    llm_confidence      TEXT,
    status              TEXT NOT NULL,
    safe_expansion      INTEGER NOT NULL DEFAULT 0,
    candidate_id        INTEGER,
    reason              TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    UNIQUE(source_term_id, target_term_id, relation_type)
);
CREATE INDEX IF NOT EXISTS idx_relations_source ON term_relations(source_term_id);
CREATE INDEX IF NOT EXISTS idx_relations_status ON term_relations(status);

CREATE TABLE IF NOT EXISTS relation_evidence (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    relation_id INTEGER NOT NULL REFERENCES term_relations(id) ON DELETE CASCADE,
    context_id  INTEGER NOT NULL REFERENCES term_contexts(id) ON DELETE CASCADE,
    UNIQUE(relation_id, context_id)
);

CREATE TABLE IF NOT EXISTS indexing_runs (
    run_id                  TEXT PRIMARY KEY,
    started_at              TEXT NOT NULL,
    completed_at            TEXT,
    status                  TEXT NOT NULL,
    document_count          INTEGER DEFAULT 0,
    changed_document_count  INTEGER DEFAULT 0,
    sentence_count          INTEGER DEFAULT 0,
    phrase_count            INTEGER DEFAULT 0,
    vocabulary_size         INTEGER DEFAULT 0,
    term_count              INTEGER DEFAULT 0,
    candidate_count         INTEGER DEFAULT 0,
    validated_count         INTEGER DEFAULT 0,
    active_relation_count   INTEGER DEFAULT 0,
    review_relation_count   INTEGER DEFAULT 0,
    rejected_relation_count INTEGER DEFAULT 0,
    error_count             INTEGER DEFAULT 0,
    duration_seconds        REAL DEFAULT 0,
    message                 TEXT
);
"""


class Database:
    """SQLite 커넥션 래퍼."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")

    @property
    def connection(self) -> sqlite3.Connection:
        return self._conn

    # 이후 버전에서 추가된 컬럼. 기존 DB 를 지우지 않고 따라잡기 위한 목록이다.
    _ADDED_COLUMNS = (
        ("terms", "sentence_ratio", "REAL NOT NULL DEFAULT 0"),
        ("terms", "is_stopword", "INTEGER NOT NULL DEFAULT 0"),
        ("terms", "excluded_reason", "TEXT NOT NULL DEFAULT ''"),
        ("candidate_relations", "sources", "TEXT NOT NULL DEFAULT ''"),
        ("candidate_relations", "rank_a_to_b", "INTEGER"),
        ("candidate_relations", "rank_b_to_a", "INTEGER"),
        ("candidate_relations", "lexical_score", "REAL NOT NULL DEFAULT 0"),
        ("candidate_relations", "priority_score", "REAL NOT NULL DEFAULT 0"),
        ("term_relations", "safe_expansion", "INTEGER NOT NULL DEFAULT 0"),
        ("term_relations", "candidate_id", "INTEGER"),
    )

    def initialize(self) -> None:
        self._conn.executescript(SCHEMA)
        self._migrate()
        self._conn.commit()
        logger.debug("스키마 초기화 완료: %s", self.db_path)

    def _migrate(self) -> None:
        """기존 DB 에 없는 컬럼을 채워 넣는다."""
        for table, column, definition in self._ADDED_COLUMNS:
            existing = {
                row["name"]
                for row in self._conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            if column not in existing:
                self._conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
                )
                logger.info("컬럼 추가: %s.%s", table, column)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        return self._conn.execute(sql, params)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
