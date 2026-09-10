"""Sentence Processor (§11).

문서를 문장 단위 Context 로 분리한다. 문장은 Context Store 의 최소 단위다.

- kiwipiepy 가 설치되어 있으면 사용하고, 없으면 규칙 기반 분리기로 동작한다.
- ``2026.01.01`` 같은 숫자 사이의 마침표에서는 문장을 자르지 않는다.
  (Pattern Normalizer 는 문장 분리 이후에 동작한다.)
"""
from __future__ import annotations

import logging
import re
from typing import Iterable, List, Sequence

from app.models.context import Sentence
from app.models.document import CleanedDocument

logger = logging.getLogger(__name__)

try:
    from kiwipiepy import Kiwi
    _HAS_KIWI = True
except ImportError:  # pragma: no cover
    Kiwi = None  # type: ignore
    _HAS_KIWI = False

# 종결부호 뒤 공백/줄끝에서 분리. 숫자 사이의 '.' 는 제외한다.
# 두 대안 모두 zero-width 이므로 종결부호가 문장에서 사라지지 않는다.
_SPLIT_RE = re.compile(r"(?<!\d)(?<=[.!?…])(?=\s)|(?<=[다요음임]\.)(?=\S)")
_WS_RE = re.compile(r"\s+")


class SentenceProcessor:
    def __init__(
        self,
        *,
        min_length: int = 4,
        max_length: int = 400,
        abbreviation_guards: Sequence[str] = (),
        use_kiwi: bool = True,
    ):
        self.min_length = min_length
        self.max_length = max_length
        self.abbreviation_guards = tuple(abbreviation_guards)
        self._kiwi = None
        if use_kiwi and _HAS_KIWI:
            try:
                self._kiwi = Kiwi()
                logger.info("문장 분리기: kiwipiepy")
            except Exception as exc:  # pragma: no cover
                logger.warning("kiwipiepy 초기화 실패, 규칙 기반으로 대체합니다: %s", exc)
        if self._kiwi is None:
            logger.info("문장 분리기: 규칙 기반")

    # ------------------------------------------------------------------
    def split(self, documents: Iterable[CleanedDocument]) -> List[Sentence]:
        sentences: List[Sentence] = []
        doc_count = 0
        for doc in documents:
            doc_count += 1
            index = 0
            # 제목도 하나의 문장 Context 로 취급한다 (용어가 제목에만 있는 경우 대비).
            for text in self._split_document(doc):
                sentences.append(
                    Sentence(
                        page_id=doc.page_id,
                        page_title=doc.title,
                        sentence_index=index,
                        original_sentence=text,
                    )
                )
                index += 1
        logger.info("문장 분리 완료: %s문서 -> %s문장", doc_count, len(sentences))
        return sentences

    def _split_document(self, doc: CleanedDocument) -> List[str]:
        parts: List[str] = []
        title = (doc.title or "").strip()
        if self._is_valid(title):
            parts.append(title)
        for line in (doc.clean_text or "").split("\n"):
            parts.extend(self.split_text(line))
        return parts

    def split_text(self, text: str) -> List[str]:
        text = (text or "").strip()
        if not text:
            return []

        raw = self._kiwi_split(text) if self._kiwi else self._rule_split(text)

        results: List[str] = []
        for sentence in raw:
            sentence = _WS_RE.sub(" ", sentence).strip()
            if not sentence:
                continue
            for chunk in self._enforce_max_length(sentence):
                if self._is_valid(chunk):
                    results.append(chunk)
        return results

    # ------------------------------------------------------------------
    def _kiwi_split(self, text: str) -> List[str]:
        try:
            return [s.text for s in self._kiwi.split_into_sents(text)]
        except Exception as exc:  # pragma: no cover
            logger.debug("kiwi 문장 분리 실패, 규칙 기반 사용: %s", exc)
            return self._rule_split(text)

    def _rule_split(self, text: str) -> List[str]:
        pieces = _SPLIT_RE.split(text)
        merged: List[str] = []
        for piece in pieces:
            piece = (piece or "").strip()
            if not piece:
                continue
            # 약어 뒤에서 잘린 조각은 앞 문장에 다시 붙인다.
            if merged and self._ends_with_guard(merged[-1]):
                merged[-1] = f"{merged[-1]} {piece}"
            else:
                merged.append(piece)
        return merged

    def _ends_with_guard(self, sentence: str) -> bool:
        stripped = sentence.rstrip(". ")
        return any(stripped.endswith(guard) for guard in self.abbreviation_guards)

    def _enforce_max_length(self, sentence: str) -> List[str]:
        if len(sentence) <= self.max_length:
            return [sentence]
        chunks: List[str] = []
        current = ""
        for token in sentence.split(" "):
            if len(current) + len(token) + 1 > self.max_length and current:
                chunks.append(current)
                current = token
            else:
                current = f"{current} {token}".strip()
        if current:
            chunks.append(current)
        return chunks

    def _is_valid(self, sentence: str) -> bool:
        if len(sentence) < self.min_length:
            return False
        # 의미 있는 문자가 하나도 없으면 버린다.
        return bool(re.search(r"[0-9A-Za-z가-힣]", sentence))
