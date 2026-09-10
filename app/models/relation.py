"""Candidate / Relation 모델 (§26, §42)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from app.models.enums import (
    CandidateSource,
    CandidateStatus,
    Confidence,
    EntityType,
    RelationStatus,
    RelationType,
    TermType,
)


@dataclass
class CandidateRelation:
    """FastText 가 만든 후보. 이 자체는 의미 관계가 아니다 (§5.2)."""

    term_a: str            # term_key, canonical 정렬 기준 min
    term_b: str            # term_key, canonical 정렬 기준 max
    fasttext_similarity: float
    status: CandidateStatus = CandidateStatus.PENDING
    candidate_id: Optional[int] = None
    run_id: Optional[str] = None

    # --- 후보 근거 ---
    # 어느 경로가 이 쌍을 만들었는지. 여러 경로가 겹치면 신호가 강하다.
    sources: List[str] = field(default_factory=list)
    # FastText 이웃 목록에서의 순위(1부터). 없으면 None.
    # 절대 cosine 은 이 corpus 에서 변별력이 없으므로 순위를 함께 보존한다.
    rank_a_to_b: Optional[int] = None
    rank_b_to_a: Optional[int] = None
    # 문자열 유사도(어순/띄어쓰기/포함). 0~1.
    lexical_score: float = 0.0
    # 후보 정렬과 상한 적용에 쓰는 복합 점수
    priority_score: float = 0.0

    @property
    def source_set(self) -> set:
        return set(self.sources)

    def has_source(self, source: CandidateSource) -> bool:
        return source.value in self.sources

    @staticmethod
    def canonical_pair(a: str, b: str) -> tuple[str, str]:
        """A→B 와 B→A 를 동일 후보로 취급한다 (§27)."""
        return (a, b) if a <= b else (b, a)

    @property
    def pair_key(self) -> str:
        a, b = self.canonical_pair(self.term_a, self.term_b)
        return f"{a}||{b}"


@dataclass
class RelationValidation:
    """LLM 판정 결과 (§37)."""

    term_a: str
    term_b: str
    relation_type: RelationType
    confidence: Confidence
    term_a_type: TermType = TermType.UNKNOWN
    term_b_type: TermType = TermType.UNKNOWN
    term_a_entity_type: Optional[EntityType] = None
    term_b_entity_type: Optional[EntityType] = None
    reason: str = ""
    evidence_context_ids: List[int] = field(default_factory=list)
    fasttext_similarity: float = 0.0
    # 검색 확장 안전성은 방향마다 다르다.
    # '전문 인덱스'를 찾는 사람에게 '인덱스'를 주는 것과 그 반대는 같지 않다.
    safe_a_to_b: bool = False
    safe_b_to_a: bool = False
    # 응답을 후보에 되짚기 위한 식별자. 문자열 재정규화로 맞추지 않는다.
    candidate_id: Optional[int] = None


@dataclass
class TermRelation:
    """저장되는 최종 관계."""

    source_term_key: str
    target_term_key: str
    relation_type: RelationType
    status: RelationStatus
    fasttext_similarity: float = 0.0
    llm_confidence: Confidence = Confidence.LOW
    reason: str = ""
    evidence_context_ids: List[int] = field(default_factory=list)
    relation_id: Optional[int] = None
    # source -> target 방향으로 검색어를 확장해도 되는가.
    # 관계는 대칭으로 저장하지만 이 값은 방향마다 다르다.
    safe_expansion: bool = False


@dataclass
class IndexingRun:
    """하나의 Dictionary Build 실행 단위 (§48)."""

    run_id: str
    started_at: str
    status: str = "RUNNING"
    completed_at: Optional[str] = None
    document_count: int = 0
    changed_document_count: int = 0
    sentence_count: int = 0
    phrase_count: int = 0
    vocabulary_size: int = 0
    term_count: int = 0
    candidate_count: int = 0
    validated_count: int = 0
    active_relation_count: int = 0
    review_relation_count: int = 0
    rejected_relation_count: int = 0
    error_count: int = 0
    duration_seconds: float = 0.0
    message: str = ""
