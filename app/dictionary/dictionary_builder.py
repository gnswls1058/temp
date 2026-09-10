"""Term Dictionary 생성 (§39 ~ §47).

- LLM 판정 결과에 승인 정책을 적용해 ACTIVE / REVIEW / REJECTED 로 저장한다.
- ENTITY 와 CONCEPT 를 구분해서 확장 가능 여부(expandable)를 표시한다.
- Alias 의 자동 전이(A=B, B=C -> A=C)는 V1 에서 생성하지 않는다 (§47).
- 실제 검색 확장 정책은 별도 Search Agent 의 몫이고, 여기서는 관계만 정확히 저장한다.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from app.models.enums import (
    Confidence,
    RelationStatus,
    RelationType,
    TermType,
)
from app.models.relation import RelationValidation, TermRelation
from app.repository.base import RelationRepository, TermRepository

logger = logging.getLogger(__name__)


@dataclass
class RelationPolicy:
    """관계 승인 정책 (§39, §44, §45)."""

    active_relation_types: Sequence[str] = (
        "SAME_ENTITY", "EXACT_ALIAS", "ABBREVIATION",
    )
    # 자동 ACTIVE 를 좁히기 위한 조건.
    # 잘못된 유의어 하나가 검색을 오염시키는 비용이, 하나를 놓치는 비용보다 크다.
    # 양방향 치환이 안전하다고 판정된 관계만 자동 승인한다.
    require_bidirectional_for_active: bool = True
    high_confidence_status: str = "ACTIVE"
    medium_confidence_status: str = "REVIEW"
    low_confidence_status: str = "REJECTED"
    related_status: str = "REVIEW"
    never_expand_types: Sequence[str] = (
        "RELATED", "ANTONYM", "UNRELATED", "UNKNOWN",
    )
    entity_expandable_types: Sequence[str] = (
        "SAME_ENTITY", "EXACT_ALIAS", "ALIAS", "ABBREVIATION",
    )
    concept_expandable_types: Sequence[str] = (
        "EXACT_ALIAS", "SYNONYM", "NEAR_SYNONYM", "ABBREVIATION",
    )
    enable_alias_transitivity: bool = False

    @classmethod
    def from_settings(cls, section) -> "RelationPolicy":
        if section is None:
            return cls()
        return cls(
            active_relation_types=tuple(section.get("active_relation_types", cls.active_relation_types)),
            require_bidirectional_for_active=bool(
                section.get("require_bidirectional_for_active", True)
            ),
            high_confidence_status=str(section.get("high_confidence_status", "ACTIVE")),
            medium_confidence_status=str(section.get("medium_confidence_status", "REVIEW")),
            low_confidence_status=str(section.get("low_confidence_status", "REJECTED")),
            related_status=str(section.get("related_status", "REVIEW")),
            never_expand_types=tuple(section.get("never_expand_types", cls.never_expand_types)),
            entity_expandable_types=tuple(section.get("entity_expandable_types", cls.entity_expandable_types)),
            concept_expandable_types=tuple(section.get("concept_expandable_types", cls.concept_expandable_types)),
            enable_alias_transitivity=bool(section.get("enable_alias_transitivity", False)),
        )

    # ------------------------------------------------------------------
    def decide_status(self, relation_type: RelationType, confidence: Confidence,
                      *, bidirectional: bool = True) -> RelationStatus:
        if relation_type is RelationType.UNRELATED or relation_type is RelationType.UNKNOWN:
            return RelationStatus.REJECTED
        if relation_type is RelationType.ANTONYM:
            # 의미 있는 정보지만 검색 확장에는 절대 쓰지 않는다.
            return RelationStatus.REVIEW
        if relation_type is RelationType.RELATED:
            return RelationStatus(self.related_status)
        if relation_type in (RelationType.BROADER, RelationType.NARROWER):
            # 방향이 있는 포함 관계는 자동 승인하지 않는다. 확장 방향은 저장하되
            # 사전 등재 여부는 사람이 정한다.
            return RelationStatus.REVIEW

        if relation_type.value not in self.active_relation_types:
            return RelationStatus.REVIEW
        if self.require_bidirectional_for_active and not bidirectional:
            return RelationStatus.REVIEW

        if confidence is Confidence.HIGH:
            return RelationStatus(self.high_confidence_status)
        if confidence is Confidence.MEDIUM:
            return RelationStatus(self.medium_confidence_status)
        return RelationStatus(self.low_confidence_status)

    def is_expandable(self, source_term_type: TermType, relation_type: RelationType,
                      status: RelationStatus) -> bool:
        if status is not RelationStatus.ACTIVE:
            return False
        if relation_type.value in self.never_expand_types:
            return False
        if source_term_type is TermType.ENTITY:
            return relation_type.value in self.entity_expandable_types
        if source_term_type is TermType.CONCEPT:
            return relation_type.value in self.concept_expandable_types
        return False


@dataclass
class BuildStats:
    active: int = 0
    review: int = 0
    rejected: int = 0
    saved: int = 0
    skipped: int = 0


class DictionaryBuilder:
    def __init__(
        self,
        term_repository: TermRepository,
        relation_repository: RelationRepository,
        *,
        policy: Optional[RelationPolicy] = None,
        output_dir: str | Path = "./data/output",
    ):
        self.terms = term_repository
        self.relations = relation_repository
        self.policy = policy or RelationPolicy()
        self.output_dir = Path(output_dir)

    # ------------------------------------------------------------------
    def apply_validations(self, validations: Sequence[RelationValidation]) -> BuildStats:
        """LLM 판정 결과를 정책에 따라 term_relations 에 저장한다."""
        stats = BuildStats()

        for validation in validations:
            bidirectional = validation.safe_a_to_b and validation.safe_b_to_a
            status = self.policy.decide_status(
                validation.relation_type, validation.confidence,
                bidirectional=bidirectional,
            )
            if status is RelationStatus.ACTIVE:
                stats.active += 1
            elif status is RelationStatus.REVIEW:
                stats.review += 1
            else:
                stats.rejected += 1

            # 관계는 대칭으로 저장한다(전이는 만들지 않는다, §47).
            # 관계 자체는 대칭으로 저장하되, 검색 확장 가능 여부는 방향마다 다르다.
            for source, target, safe in (
                (validation.term_a, validation.term_b, validation.safe_a_to_b),
                (validation.term_b, validation.term_a, validation.safe_b_to_a),
            ):
                relation = TermRelation(
                    source_term_key=source,
                    target_term_key=target,
                    relation_type=validation.relation_type,
                    status=status,
                    fasttext_similarity=validation.fasttext_similarity,
                    llm_confidence=validation.confidence,
                    reason=validation.reason,
                    evidence_context_ids=validation.evidence_context_ids,
                    safe_expansion=bool(safe),
                )
                if self.relations.save_relation(relation) is None:
                    stats.skipped += 1
                else:
                    stats.saved += 1

        logger.info(
            "관계 저장 - ACTIVE %s, REVIEW %s, REJECTED %s (레코드 %s건, 스킵 %s건)",
            stats.active, stats.review, stats.rejected, stats.saved, stats.skipped,
        )
        return stats

    # ------------------------------------------------------------------
    def rebuild(self, *, include_statuses: Sequence[str] = ("ACTIVE", "REVIEW"),
                filename: str = "term_dictionary.json") -> Path:
        """DB 상태로부터 Term Dictionary JSON 을 다시 만든다 (§40)."""
        dictionary = self.build_dictionary(include_statuses)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        path = self.output_dir / filename
        with path.open("w", encoding="utf-8") as fp:
            json.dump(dictionary, fp, ensure_ascii=False, indent=2)
        logger.info("Term Dictionary 생성: %s (term %s개)", path, len(dictionary))
        return path

    def build_dictionary(
        self, include_statuses: Sequence[str] = ("ACTIVE", "REVIEW")
    ) -> Dict[str, dict]:
        result: Dict[str, dict] = {}
        for term in self.terms.list_all():
            relations = self.relations.relations_for_term(
                term.term_key, list(include_statuses)
            )
            if not relations and term.term_type is TermType.UNKNOWN:
                # 관계도 없고 분류도 안 된 term 은 사전에 넣지 않는다.
                continue

            entries = []
            for relation in relations:
                target = self.terms.get(relation.target_term_key)
                entries.append(
                    {
                        "term": target.display_term if target else relation.target_term_key,
                        "termKey": relation.target_term_key,
                        "relationType": relation.relation_type.value,
                        "status": relation.status.value,
                        "fastTextSimilarity": relation.fasttext_similarity,
                        "llmConfidence": relation.llm_confidence.value,
                        # source -> target 방향으로만 판단한다.
                        # 관계가 같아도 반대 방향은 안전하지 않을 수 있다.
                        "expandable": (
                            relation.safe_expansion
                            and self.policy.is_expandable(
                                term.term_type, relation.relation_type, relation.status
                            )
                        ),
                        "safeExpansion": relation.safe_expansion,
                        "evidenceContextIds": relation.evidence_context_ids,
                        "reason": relation.reason,
                    }
                )

            result[term.display_term] = {
                "termKey": term.term_key,
                "termType": term.term_type.value,
                "entityType": term.entity_type.value if term.entity_type else None,
                "frequency": term.frequency,
                "documentFrequency": term.document_frequency,
                "relations": sorted(
                    entries,
                    key=lambda e: (not e["expandable"], -e["fastTextSimilarity"]),
                ),
            }
        return result

    # ------------------------------------------------------------------
    def export_review_queue(self, filename: str = "review_queue.json") -> Path:
        """사람이 확인해야 하는 REVIEW 관계 목록 (§39)."""
        rows = [
            {
                "source": r.source_term_key,
                "target": r.target_term_key,
                "relationType": r.relation_type.value,
                "llmConfidence": r.llm_confidence.value,
                "fastTextSimilarity": r.fasttext_similarity,
                "reason": r.reason,
                "evidenceContextIds": r.evidence_context_ids,
            }
            for r in self.relations.list_relations(RelationStatus.REVIEW.value)
            if r.source_term_key <= r.target_term_key  # 대칭 중복 제거
        ]
        self.output_dir.mkdir(parents=True, exist_ok=True)
        path = self.output_dir / filename
        with path.open("w", encoding="utf-8") as fp:
            json.dump(rows, fp, ensure_ascii=False, indent=2)
        logger.info("REVIEW 큐 생성: %s (%s건)", path, len(rows))
        return path
