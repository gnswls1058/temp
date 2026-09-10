"""LLM Term Classification (§32 ~ §34).

용어 자체의 종류(ENTITY / CONCEPT / UNKNOWN)와 ENTITY subtype 을 판정한다.
근거는 Context Store 에서 자동 조회한 실제 사내 문맥이다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from app.models.enums import EntityType, TermType, safe_enum
from app.models.term import Term
from app.repository.base import ContextRepository, TermRepository
from app.validation import prompts
from app.validation.llm_client import LLMClient, LLMError

logger = logging.getLogger(__name__)


@dataclass
class TermClassification:
    term_key: str
    term_type: TermType
    entity_type: Optional[EntityType]
    reason: str = ""


class TermClassifier:
    def __init__(
        self,
        llm_client: LLMClient,
        context_repository: ContextRepository,
        term_repository: TermRepository,
        *,
        context_per_term: int = 5,
        batch_size: int = 10,
        diversify_by_page: bool = True,
        max_contexts_per_page: int = 2,
    ):
        self.llm = llm_client
        self.contexts = context_repository
        self.terms = term_repository
        self.context_per_term = context_per_term
        self.batch_size = max(1, batch_size)
        self.diversify_by_page = diversify_by_page
        self.max_contexts_per_page = max_contexts_per_page

    # ------------------------------------------------------------------
    def classify(self, terms: Sequence[Term]) -> List[TermClassification]:
        results: List[TermClassification] = []
        batches = [
            terms[i: i + self.batch_size] for i in range(0, len(terms), self.batch_size)
        ]
        logger.info("Term 분류 시작: %s개 / 배치 %s개", len(terms), len(batches))

        for index, batch in enumerate(batches, start=1):
            try:
                results.extend(self._classify_batch(batch))
            except LLMError as exc:
                logger.error("배치 %s/%s Term 분류 실패: %s", index, len(batches), exc)
            except Exception as exc:
                logger.exception("배치 %s/%s Term 분류 오류: %s", index, len(batches), exc)

        logger.info("Term 분류 완료: %s개", len(results))
        return results

    def classify_and_save(self, terms: Sequence[Term]) -> List[TermClassification]:
        results = self.classify(terms)
        for item in results:
            self.terms.update_classification(
                item.term_key,
                item.term_type.value,
                item.entity_type.value if item.entity_type else None,
            )
        return results

    # ------------------------------------------------------------------
    def _classify_batch(self, batch: Sequence[Term]) -> List[TermClassification]:
        payload = []
        term_index: Dict[str, Term] = {}

        for term in batch:
            contexts = self.contexts.find_by_term(
                term.term_key,
                self.context_per_term,
                self.diversify_by_page,
                self.max_contexts_per_page,
            )
            if not contexts:
                logger.debug("문맥이 없어 분류 생략: %s", term.term_key)
                continue
            term_index[term.term_key] = term
            payload.append(
                {
                    "term_key": term.term_key,
                    "display_term": term.display_term,
                    "frequency": term.frequency,
                    "contexts": contexts,
                }
            )

        if not payload:
            return []

        response = self.llm.call_tool(
            prompts.TERM_CLASSIFICATION_SYSTEM,
            prompts.build_term_classification_prompt(payload, self.context_per_term),
            prompts.TERM_CLASSIFICATION_TOOL,
            prompts.TERM_CLASSIFICATION_TOOL["name"],
        )

        results: List[TermClassification] = []
        for item in response.get("results") or []:
            term_key = str(item.get("termKey", ""))
            if term_key not in term_index:
                logger.warning("알 수 없는 termKey 응답 무시: %s", term_key)
                continue
            term_type = safe_enum(TermType, item.get("termType"), TermType.UNKNOWN)
            entity_type = (
                safe_enum(EntityType, item.get("entityType"), None)
                if term_type is TermType.ENTITY and item.get("entityType")
                else None
            )
            results.append(
                TermClassification(
                    term_key=term_key,
                    term_type=term_type,
                    entity_type=entity_type,
                    reason=str(item.get("reason", ""))[:500],
                )
            )
        return results
