"""문장 단위 Context 모델 (§20 ~ §22).

원본 문장 / normalized 문장 / token 결과를 모두 보존한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Sentence:
    """문서에서 분리된 문장 하나."""

    page_id: str
    page_title: str
    sentence_index: int
    original_sentence: str
    normalized_sentence: Optional[str] = None
    # Komoran 필터링 후 토큰
    tokens: List[str] = field(default_factory=list)
    # Phrases 적용 후 토큰 (FastText 학습 입력)
    phrase_tokens: List[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.page_id}#{self.sentence_index}"


@dataclass
class TermContext:
    """특정 term 이 등장한 문장 근거."""

    term_key: str
    display_term: str
    page_id: str
    page_title: str
    sentence_index: int
    original_sentence: str
    normalized_sentence: str
    processed_sentence: str
    context_id: Optional[int] = None
