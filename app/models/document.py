"""Confluence 원본 문서 모델."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Document:
    """Confluence 페이지 1건. body 는 원본 그대로 보존한다 (§5.1)."""

    page_id: str
    space_id: Optional[str]
    title: str
    body: str
    version: int
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    labels: List[str] = field(default_factory=list)
    url: Optional[str] = None

    # 전처리 결과 (원본과 분리 보관)
    clean_text: Optional[str] = None

    def to_row(self) -> dict:
        return {
            "page_id": self.page_id,
            "space_id": self.space_id,
            "title": self.title,
            "body": self.body,
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "labels": ",".join(self.labels),
            "url": self.url,
            "clean_text": self.clean_text,
        }


@dataclass
class CleanedDocument:
    page_id: str
    title: str
    clean_text: str
