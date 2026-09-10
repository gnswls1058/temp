"""LLM Relation Validation (§30 ~ §39, §51, §52).

Candidate Pair 와 Context Store 에서 자동 조회한 대표 문맥을 LLM 에 전달하고
지정된 Enum 으로만 관계를 판정받는다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from app.models.context import TermContext
from app.models.enums import (
    CandidateStatus,
    Confidence,
    EntityType,
    RelationType,
    TermType,
    safe_enum,
)
from app.models.relation import CandidateRelation, RelationValidation
from app.preprocessing.phrase_processor import display_term
from app.repository.base import ContextRepository
from app.validation import prompts
from app.validation.llm_client import LLMClient, LLMError

logger = logging.getLogger(__name__)

# 어떤 방향으로도 검색 확장에 쓰지 않는 관계
_NEVER_EXPAND_TYPES = {
    RelationType.RELATED,
    RelationType.ANTONYM,
    RelationType.UNRELATED,
    RelationType.UNKNOWN,
}

# 근거가 약할 때 HIGH 로 확정하면 안 되는 관계 (§38)
_STRONG_IDENTITY_TYPES = {
    RelationType.SAME_ENTITY,
    RelationType.EXACT_ALIAS,
    RelationType.ALIAS,
    RelationType.ABBREVIATION,
}


@dataclass
class ValidationOutcome:
    validations: List[RelationValidation] = field(default_factory=list)
    failed_pairs: List[str] = field(default_factory=list)
    skipped_pairs: List[str] = field(default_factory=list)


class RelationValidator:
    def __init__(
        self,
        llm_client: LLMClient,
        context_repository: ContextRepository,
        *,
        context_per_term: int = 5,
        batch_size: int = 5,
        diversify_by_page: bool = True,
        max_contexts_per_page: int = 2,
        delimiter: str = "_",
        min_distinct_patterns_for_high: int = 2,
    ):
        self.llm = llm_client
        self.contexts = context_repository
        self.context_per_term = context_per_term
        self.batch_size = max(1, batch_size)
        self.diversify_by_page = diversify_by_page
        self.max_contexts_per_page = max_contexts_per_page
        self.delimiter = delimiter
        self.min_distinct_patterns_for_high = min_distinct_patterns_for_high

    # ------------------------------------------------------------------
    def validate(self, candidates: Sequence[CandidateRelation]) -> ValidationOutcome:
        outcome = ValidationOutcome()
        batches = [
            candidates[i: i + self.batch_size]
            for i in range(0, len(candidates), self.batch_size)
        ]
        logger.info("LLM 검증 시작: 후보 %s쌍 / 배치 %s개", len(candidates), len(batches))

        for index, batch in enumerate(batches, start=1):
            try:
                outcome.validations.extend(self._validate_batch(batch, outcome))
            except LLMError as exc:
                # 배치 하나가 실패해도 전체 작업은 계속한다 (§51).
                logger.error("배치 %s/%s LLM 검증 실패: %s", index, len(batches), exc)
                outcome.failed_pairs.extend(c.pair_key for c in batch)
            except Exception as exc:
                logger.exception("배치 %s/%s 처리 중 오류: %s", index, len(batches), exc)
                outcome.failed_pairs.extend(c.pair_key for c in batch)

        logger.info(
            "LLM 검증 완료 - 판정 %s쌍, 실패 %s쌍, 스킵 %s쌍",
            len(outcome.validations), len(outcome.failed_pairs), len(outcome.skipped_pairs),
        )
        return outcome

    # ------------------------------------------------------------------
    def _validate_batch(self, batch: Sequence[CandidateRelation],
                        outcome: ValidationOutcome) -> List[RelationValidation]:
        payload = []
        pair_index: Dict[str, CandidateRelation] = {}
        context_index: Dict[str, Tuple[List[TermContext], List[TermContext]]] = {}

        for order, candidate in enumerate(batch, start=1):
            contexts_a, contexts_b = self.contexts.find_for_pair(
                candidate.term_a,
                candidate.term_b,
                self.context_per_term,
                self.diversify_by_page,
                self.max_contexts_per_page,
            )
            if not contexts_a or not contexts_b:
                # 근거 문맥이 없으면 LLM 에 물어볼 의미가 없다.
                logger.debug("문맥 부족으로 스킵: %s", candidate.pair_key)
                outcome.skipped_pairs.append(candidate.pair_key)
                continue

            # 배치 안의 순번이 아니라 후보 자체의 식별자를 쓴다.
            # 순번을 쓰면 응답이 밀렸을 때 조용히 다른 쌍에 붙는다.
            pair_id = self.pair_id_of(candidate, order)
            pair_index[pair_id] = candidate
            context_index[pair_id] = (contexts_a, contexts_b)
            payload.append(
                {
                    "pair_id": pair_id,
                    "term_a": candidate.term_a,
                    "term_b": candidate.term_b,
                    "display_a": display_term(candidate.term_a, self.delimiter),
                    "display_b": display_term(candidate.term_b, self.delimiter),
                    "similarity": candidate.fasttext_similarity,
                    "contexts_a": contexts_a,
                    "contexts_b": contexts_b,
                }
            )

        if not payload:
            return []

        user_prompt = prompts.build_relation_validation_prompt(
            payload, self.context_per_term
        )
        response = self.llm.call_tool(
            prompts.RELATION_VALIDATION_SYSTEM,
            user_prompt,
            prompts.RELATION_VALIDATION_TOOL,
            prompts.RELATION_VALIDATION_TOOL["name"],
        )

        results = response.get("results") or []
        if not results:
            raise LLMError("LLM 이 결과를 반환하지 않았습니다.")
        if len(results) != len(payload):
            # 개수가 어긋나면 조용히 넘어가지 않는다. 미검증이 쌓이는 주된 원인이다.
            logger.warning(
                "배치 응답 수 불일치: 요청 %s쌍, 응답 %s쌍", len(payload), len(results)
            )

        validations: List[RelationValidation] = []
        answered: set[str] = set()

        for item in results:
            pair_id = str(item.get("pairId", ""))
            candidate = pair_index.get(pair_id)
            if candidate is None:
                logger.warning("알 수 없는 pairId 응답 무시: %s", pair_id)
                continue
            answered.add(pair_id)
            valid_ids = {
                c.context_id
                for c in (context_index[pair_id][0] + context_index[pair_id][1])
                if c.context_id is not None
            }
            validations.append(self._to_validation(candidate, item, valid_ids))

        for pair_id, candidate in pair_index.items():
            if pair_id not in answered:
                logger.warning("응답 누락 pair: %s", candidate.pair_key)
                outcome.failed_pairs.append(candidate.pair_key)

        return validations

    # ------------------------------------------------------------------
    @staticmethod
    def pair_id_of(candidate: CandidateRelation, order: int) -> str:
        """LLM 프로토콜에서 쓰는 후보 식별자.

        DB 에 저장된 candidate_id 가 있으면 그것을 쓴다. 없으면 pair_key 를 쓴다.
        어느 쪽이든 문자열을 다시 정규화해서 맞추지 않아도 되도록 한다.
        """
        if candidate.candidate_id is not None:
            return f"c{candidate.candidate_id}"
        return candidate.pair_key

    @staticmethod
    def _sanitize_direction(relation_type: RelationType, a_to_b: bool,
                            b_to_a: bool) -> tuple[bool, bool]:
        """관계 유형과 방향 판정이 모순이면 안전한 쪽으로 보정한다."""
        if relation_type in _NEVER_EXPAND_TYPES:
            return False, False
        if relation_type is RelationType.BROADER:
            # termA 가 상위어. 하위어(B)를 찾는 사람에게 상위어(A)는 줄 수 있다.
            return False, True
        if relation_type is RelationType.NARROWER:
            return True, False
        return a_to_b, b_to_a

    def _to_validation(self, candidate: CandidateRelation, item: dict,
                       valid_context_ids: set) -> RelationValidation:
        relation_type = safe_enum(
            RelationType, item.get("relationType"), RelationType.UNKNOWN
        )
        confidence = safe_enum(Confidence, item.get("confidence"), Confidence.LOW)

        # LLM 이 만들어낸 context id 는 신뢰하지 않고 실제 제공한 것만 남긴다.
        evidence = [
            int(cid)
            for cid in (item.get("evidenceContextIds") or [])
            if isinstance(cid, (int, str)) and str(cid).isdigit()
            and int(cid) in valid_context_ids
        ]

        confidence = self._apply_evidence_guard(candidate, relation_type, confidence)
        safe_a_to_b = bool(item.get("safeAToB", False))
        safe_b_to_a = bool(item.get("safeBToA", False))
        safe_a_to_b, safe_b_to_a = self._sanitize_direction(
            relation_type, safe_a_to_b, safe_b_to_a
        )

        return RelationValidation(
            term_a=candidate.term_a,
            term_b=candidate.term_b,
            relation_type=relation_type,
            confidence=confidence,
            term_a_type=safe_enum(TermType, item.get("termAType"), TermType.UNKNOWN),
            term_b_type=safe_enum(TermType, item.get("termBType"), TermType.UNKNOWN),
            term_a_entity_type=safe_enum(EntityType, item.get("termAEntityType"), None)
            if item.get("termAEntityType") else None,
            term_b_entity_type=safe_enum(EntityType, item.get("termBEntityType"), None)
            if item.get("termBEntityType") else None,
            reason=str(item.get("reason", ""))[:1000],
            evidence_context_ids=evidence,
            safe_a_to_b=safe_a_to_b,
            safe_b_to_a=safe_b_to_a,
            candidate_id=candidate.candidate_id,
            fasttext_similarity=candidate.fasttext_similarity,
        )

    def _apply_evidence_guard(self, candidate: CandidateRelation,
                              relation_type: RelationType,
                              confidence: Confidence) -> Confidence:
        """단일 문장 패턴만으로 HIGH ALIAS 를 확정하지 않는다 (§38)."""
        if confidence is not Confidence.HIGH or relation_type not in _STRONG_IDENTITY_TYPES:
            return confidence

        counter = getattr(self.contexts, "distinct_pattern_count", None)
        if counter is None:
            return confidence

        patterns_a = counter(candidate.term_a)
        patterns_b = counter(candidate.term_b)
        if min(patterns_a, patterns_b) < self.min_distinct_patterns_for_high:
            logger.info(
                "근거 문맥 패턴 부족으로 HIGH -> MEDIUM 하향: %s (patterns %s/%s)",
                candidate.pair_key, patterns_a, patterns_b,
            )
            return Confidence.MEDIUM
        return confidence
