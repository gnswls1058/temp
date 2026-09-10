"""Document Collector (§7).

Confluence 에서 페이지를 수집하고 version 비교로 변경분만 갱신한다.
원본 body 는 그대로 로컬에 보존한다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.confluence.client import ConfluenceClient, ConfluenceError
from app.models.document import Document
from app.repository.base import DocumentRepository

logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    total_seen: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    failed: int = 0

    @property
    def changed(self) -> int:
        return self.created + self.updated


class DocumentCollector:
    def __init__(
        self,
        client: ConfluenceClient,
        repository: DocumentRepository,
        *,
        space_ids: Optional[List[str]] = None,
        space_keys: Optional[List[str]] = None,
        page_limit: int = 50,
        body_format: str = "storage",
        fetch_labels: bool = False,
    ):
        self.client = client
        self.repository = repository
        self.space_ids = [str(s) for s in (space_ids or []) if str(s).strip()]
        self.space_keys = [str(s) for s in (space_keys or []) if str(s).strip()]
        self.page_limit = page_limit
        self.body_format = body_format
        self.fetch_labels = fetch_labels

    def sync(self, run_id: Optional[str] = None) -> SyncResult:
        """대상 space 의 모든 페이지를 동기화한다."""
        result = SyncResult()

        space_ids = list(self.space_ids)
        if not space_ids and self.space_keys:
            space_ids = self.client.resolve_space_ids(self.space_keys)

        if not space_ids:
            logger.warning(
                "space_ids / space_keys 가 비어 있습니다. 접근 가능한 전체 페이지를 수집합니다."
            )

        for raw in self.client.iter_pages(
            space_ids or None, limit=self.page_limit, body_format=self.body_format
        ):
            result.total_seen += 1
            page_id = str(raw.get("id", ""))
            try:
                self._sync_one(raw, result, run_id)
            except ConfluenceError as exc:
                result.failed += 1
                self.repository.log_error(run_id, page_id, "collect", str(exc))
            except Exception as exc:  # 문서 1건 실패로 전체 중단하지 않는다 (§50)
                result.failed += 1
                self.repository.log_error(run_id, page_id, "collect", repr(exc))

        logger.info(
            "문서 동기화 완료 - 조회 %s, 신규 %s, 갱신 %s, 변경없음 %s, 실패 %s",
            result.total_seen, result.created, result.updated,
            result.unchanged, result.failed,
        )
        return result

    def _sync_one(self, raw: Dict[str, Any], result: SyncResult,
                  run_id: Optional[str]) -> None:
        page_id = str(raw["id"])
        version = int((raw.get("version") or {}).get("number", 0))

        cached_version = self.repository.get_version(page_id)
        if cached_version is not None and cached_version == version:
            result.unchanged += 1
            logger.debug("변경 없음 page=%s version=%s", page_id, version)
            return

        document = self._to_document(raw)
        if not document.body:
            # 목록 응답에 body 가 없으면 개별 조회로 보강한다.
            detail = self.client.get_page(page_id, self.body_format)
            document = self._to_document(detail)

        if self.fetch_labels:
            document.labels = self.client.get_labels(page_id)

        self.repository.upsert(document)
        if cached_version is None:
            result.created += 1
        else:
            result.updated += 1
            logger.info("문서 갱신 page=%s %s -> %s", page_id, cached_version, version)

    def _to_document(self, raw: Dict[str, Any]) -> Document:
        body = ""
        body_node = raw.get("body") or {}
        for fmt in (self.body_format, "storage", "atlas_doc_format", "view"):
            node = body_node.get(fmt)
            if isinstance(node, dict) and node.get("value"):
                body = node["value"]
                break

        version_node = raw.get("version") or {}
        links = raw.get("_links") or {}
        webui = links.get("webui")

        return Document(
            page_id=str(raw.get("id", "")),
            space_id=str(raw["spaceId"]) if raw.get("spaceId") else None,
            title=raw.get("title") or "",
            body=body,
            version=int(version_node.get("number", 0)),
            created_at=raw.get("createdAt"),
            updated_at=version_node.get("createdAt"),
            labels=[],
            url=f"{self.client.base_url}{webui}" if webui else None,
        )
