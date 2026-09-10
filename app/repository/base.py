"""Repository Interface (§6).

파이프라인 상위 계층은 이 인터페이스에만 의존한다.
SQLite 구현을 PostgreSQL 구현으로 교체해도 상위 코드는 변경되지 않는다.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable, List, Optional, Sequence

from app.models.context import TermContext
from app.models.document import Document
from app.models.relation import (
    CandidateRelation,
    IndexingRun,
    TermRelation,
)
from app.models.term import Term


class DocumentRepository(ABC):
    @abstractmethod
    def upsert(self, document: Document) -> None: ...

    @abstractmethod
    def get(self, page_id: str) -> Optional[Document]: ...

    @abstractmethod
    def get_version(self, page_id: str) -> Optional[int]: ...

    @abstractmethod
    def list_all(self) -> List[Document]: ...

    @abstractmethod
    def update_clean_text(self, page_id: str, clean_text: str) -> None: ...

    @abstractmethod
    def count(self) -> int: ...

    @abstractmethod
    def log_error(self, run_id: Optional[str], page_id: Optional[str],
                  stage: str, message: str) -> None: ...

    @abstractmethod
    def count_errors(self, run_id: Optional[str] = None) -> int: ...


class ContextRepository(ABC):
    @abstractmethod
    def save_many(self, contexts: Iterable[TermContext]) -> int: ...

    @abstractmethod
    def find_by_term(self, term_key: str, limit: int = 5,
                     diversify_by_page: bool = True,
                     max_per_page: int = 2) -> List[TermContext]: ...

    @abstractmethod
    def find_for_pair(self, term_a: str, term_b: str, limit_per_term: int = 5,
                      diversify_by_page: bool = True,
                      max_per_page: int = 2) -> tuple[List[TermContext], List[TermContext]]: ...

    @abstractmethod
    def clear(self) -> None: ...

    @abstractmethod
    def count(self) -> int: ...


class TermRepository(ABC):
    @abstractmethod
    def upsert_many(self, terms: Iterable[Term]) -> None: ...

    @abstractmethod
    def get(self, term_key: str) -> Optional[Term]: ...

    @abstractmethod
    def get_id(self, term_key: str) -> Optional[int]: ...

    @abstractmethod
    def update_classification(self, term_key: str, term_type: str,
                              entity_type: Optional[str]) -> None: ...

    @abstractmethod
    def deactivate(self, term_key: str) -> None: ...

    @abstractmethod
    def retain_only(self, term_keys: Iterable[str]) -> int: ...

    @abstractmethod
    def list_all(self) -> List[Term]: ...

    @abstractmethod
    def count(self) -> int: ...


class RelationRepository(ABC):
    # --- candidate ---
    @abstractmethod
    def save_candidates(self, candidates: Sequence[CandidateRelation]) -> int: ...

    @abstractmethod
    def list_candidates(self, status: Optional[str] = None) -> List[CandidateRelation]: ...

    @abstractmethod
    def update_candidate_status(self, pair_key: str, status: str) -> None: ...

    @abstractmethod
    def clear_candidates(self) -> None: ...

    # --- relation ---
    @abstractmethod
    def save_relation(self, relation: TermRelation) -> Optional[int]: ...

    @abstractmethod
    def list_relations(self, status: Optional[str] = None) -> List[TermRelation]: ...

    @abstractmethod
    def relations_for_term(self, term_key: str,
                           statuses: Optional[Sequence[str]] = None) -> List[TermRelation]: ...

    @abstractmethod
    def count_by_status(self, status: str) -> int: ...

    @abstractmethod
    def delete_relation(self, relation_id: int) -> None: ...

    @abstractmethod
    def clear_relations(self) -> None: ...


class RunRepository(ABC):
    @abstractmethod
    def start(self, run: IndexingRun) -> None: ...

    @abstractmethod
    def update(self, run: IndexingRun) -> None: ...

    @abstractmethod
    def get(self, run_id: str) -> Optional[IndexingRun]: ...

    @abstractmethod
    def list_recent(self, limit: int = 10) -> List[IndexingRun]: ...
