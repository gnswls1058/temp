"""Context Store 구축 (§20 ~ §22).

문서 전처리와 동시에 term 별 원본 문맥을 저장한다.
사용자가 문맥을 직접 입력하는 구조가 아니라, 시스템이 자동으로 보관하고 조회한다.
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from typing import Dict, Iterable, List, Sequence, Set, Tuple

from app.models.context import Sentence, TermContext
from app.models.enums import PLACEHOLDER_TOKENS
from app.models.term import Term
from app.preprocessing.phrase_processor import display_term
from app.repository.base import ContextRepository

logger = logging.getLogger(__name__)

# 완결된 한국어/영어 문장으로 끝나는지 판정한다.
# 표 셀("NOT NULL")이나 코드 조각은 여기에 걸리지 않는다.
_SENTENCE_END_RE = re.compile(r"(?:다|요|음|임|까|죠|함|됨)[.!?]?$|[.!?…]$")
_MIN_SENTENCE_LENGTH = 12


def looks_like_sentence(text: str) -> bool:
    """문맥이 표 조각이 아니라 실제 문장인지 판단한다."""
    text = (text or "").strip()
    if len(text) < _MIN_SENTENCE_LENGTH:
        return False
    return bool(_SENTENCE_END_RE.search(text))


class ContextBuilder:
    def __init__(
        self,
        repository: ContextRepository,
        *,
        delimiter: str = "_",
        stopwords: Sequence[str] = (),
        max_contexts_per_term: int = 50,
    ):
        self.repository = repository
        self.delimiter = delimiter
        self.stopwords = set(stopwords)
        self.max_contexts_per_term = max_contexts_per_term

    # ------------------------------------------------------------------
    def build(self, sentences: Iterable[Sentence]) -> Tuple[int, List[Term]]:
        """Context 를 저장하고 term 통계(frequency / document_frequency)를 만든다."""
        contexts: List[TermContext] = []
        per_term_count: Dict[str, int] = defaultdict(int)
        frequency: Dict[str, int] = defaultdict(int)
        doc_pages: Dict[str, Set[str]] = defaultdict(set)
        sentence_hits: Dict[str, int] = defaultdict(int)
        sentence_total: Dict[str, int] = defaultdict(int)

        for sentence in sentences:
            tokens = sentence.phrase_tokens or sentence.tokens
            if not tokens:
                continue

            processed_sentence = " ".join(tokens)
            is_sentence = looks_like_sentence(sentence.original_sentence)
            seen_in_sentence: Set[str] = set()

            for token in tokens:
                if not self._is_indexable(token):
                    continue
                frequency[token] += 1
                doc_pages[token].add(sentence.page_id)

                # 문장다움은 문장 단위로 한 번만 센다.
                if token not in seen_in_sentence:
                    sentence_total[token] += 1
                    if is_sentence:
                        sentence_hits[token] += 1

                # 동일 문장 안에서 같은 term 은 Context 를 하나만 만든다 (§22).
                if token in seen_in_sentence:
                    continue
                seen_in_sentence.add(token)

                if per_term_count[token] >= self.max_contexts_per_term:
                    continue
                per_term_count[token] += 1

                contexts.append(
                    TermContext(
                        term_key=token,
                        display_term=display_term(token, self.delimiter),
                        page_id=sentence.page_id,
                        page_title=sentence.page_title,
                        sentence_index=sentence.sentence_index,
                        original_sentence=sentence.original_sentence,
                        normalized_sentence=sentence.normalized_sentence or "",
                        processed_sentence=processed_sentence,
                    )
                )

        saved = self.repository.save_many(contexts)
        terms = [
            Term(
                term_key=key,
                display_term=display_term(key, self.delimiter),
                frequency=count,
                document_frequency=len(doc_pages[key]),
                sentence_ratio=(
                    sentence_hits[key] / sentence_total[key]
                    if sentence_total[key] else 0.0
                ),
            )
            for key, count in frequency.items()
        ]
        logger.info("Context 저장 %s건, term 후보 %s개", saved, len(terms))
        return saved, terms

    # ------------------------------------------------------------------
    def _is_indexable(self, token: str) -> bool:
        if token in PLACEHOLDER_TOKENS:
            return False
        if token in self.stopwords:
            return False
        if not token.strip():
            return False
        return True
