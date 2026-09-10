"""Gensim Phrases 기반 복합어 처리 (§15 ~ §18).

- Bigram 을 먼저 학습하고, 필요하면 Bigram 결과 위에서 Trigram 을 학습한다.
- ``<DATE>`` 등 placeholder 가 포함된 phrase 는 거부하고 원래 token 으로 되돌린다 (§17).
- 내부 표현(``패밀리_오픈``)과 표시 표현(``패밀리 오픈``)을 분리해서 관리한다 (§18).
"""
from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

from app.models.context import Sentence
from app.models.enums import PLACEHOLDER_TOKENS

logger = logging.getLogger(__name__)

try:
    from gensim.models.phrases import Phrases
    _HAS_GENSIM = True
except ImportError:  # pragma: no cover
    Phrases = None  # type: ignore
    _HAS_GENSIM = False


class PhrasesUnavailableError(RuntimeError):
    pass


def display_term(term_key: str, delimiter: str = "_") -> str:
    """내부 token 표현을 사용자/LLM 에게 보여줄 표현으로 변환한다 (§18)."""
    return term_key.replace(delimiter, " ") if delimiter else term_key


class _SimpleBigramModel:
    """gensim 이 없을 때 쓰는 대체 구현 (테스트/스모크 용).

    gensim 의 기본 스코어(original_scorer)와 동일한 식을 사용한다::

        score = (count(ab) - min_count) / (count(a) * count(b)) * len_vocab
    """

    def __init__(self, sentences: Iterable[Sequence[str]], min_count: int,
                 threshold: float, delimiter: str):
        unigram: Dict[str, int] = defaultdict(int)
        bigram: Dict[str, int] = defaultdict(int)
        for tokens in sentences:
            prev = None
            for token in tokens:
                unigram[token] += 1
                if prev is not None:
                    bigram[f"{prev}{delimiter}{token}"] += 1
                prev = token

        self.delimiter = delimiter
        len_vocab = len(unigram) + len(bigram)
        self.phrasegrams: Dict[str, float] = {}
        for pair, count in bigram.items():
            a, b = pair.split(delimiter, 1)
            if count < min_count or unigram[a] == 0 or unigram[b] == 0:
                continue
            score = (count - min_count) / (unigram[a] * unigram[b]) * len_vocab
            if score > threshold:
                self.phrasegrams[pair] = score

    def __getitem__(self, tokens: Sequence[str]) -> List[str]:
        out: List[str] = []
        index = 0
        while index < len(tokens):
            if index + 1 < len(tokens):
                joined = f"{tokens[index]}{self.delimiter}{tokens[index + 1]}"
                if joined in self.phrasegrams:
                    out.append(joined)
                    index += 2
                    continue
            out.append(tokens[index])
            index += 1
        return out


class PhraseProcessor:
    def __init__(
        self,
        *,
        min_count: int = 3,
        threshold: float = 10.0,
        enable_trigram: bool = True,
        delimiter: str = "_",
        max_vocab_size: int = 40_000_000,
        forbidden_tokens: Sequence[str] = (),
        allow_fallback: bool = False,
    ):
        self.min_count = min_count
        self.threshold = threshold
        self.enable_trigram = enable_trigram
        self.delimiter = delimiter
        self.max_vocab_size = max_vocab_size
        self.forbidden = set(forbidden_tokens) | set(PLACEHOLDER_TOKENS)
        self.allow_fallback = allow_fallback

        self.bigram = None
        self.trigram = None

    # ------------------------------------------------------------------
    def train(self, sentences: Sequence[Sentence]) -> "PhraseProcessor":
        corpus = [s.tokens for s in sentences if s.tokens]
        if not corpus:
            logger.warning("Phrase 학습 corpus 가 비어 있습니다.")
            return self

        self.bigram = self._fit(corpus)
        bigram_corpus = [self._apply(self.bigram, tokens) for tokens in corpus]

        if self.enable_trigram:
            self.trigram = self._fit(bigram_corpus)

        logger.info(
            "Phrase 학습 완료 - bigram %s개, trigram %s개",
            self.phrase_count(self.bigram), self.phrase_count(self.trigram),
        )
        return self

    def _fit(self, corpus: List[List[str]]):
        if _HAS_GENSIM:
            model = Phrases(
                corpus,
                min_count=self.min_count,
                threshold=self.threshold,
                delimiter=self.delimiter,
                max_vocab_size=self.max_vocab_size,
            )
            return model.freeze()
        if self.allow_fallback:
            logger.warning("gensim 이 없어 대체 bigram 구현을 사용합니다 (품질 저하).")
            return _SimpleBigramModel(corpus, self.min_count, self.threshold, self.delimiter)
        raise PhrasesUnavailableError(
            "gensim 이 설치되지 않았습니다. `pip install gensim` 을 실행하세요."
        )

    # ------------------------------------------------------------------
    def transform(self, sentences: Sequence[Sentence]) -> List[List[str]]:
        """문장의 ``phrase_tokens`` 를 채우고 FastText 학습 corpus 를 반환한다."""
        corpus: List[List[str]] = []
        for sentence in sentences:
            tokens = self.apply(sentence.tokens)
            sentence.phrase_tokens = tokens
            if tokens:
                corpus.append(tokens)
        return corpus

    def apply(self, tokens: Sequence[str]) -> List[str]:
        if not tokens:
            return []
        result = list(tokens)
        if self.bigram is not None:
            result = self._apply(self.bigram, result)
        if self.trigram is not None:
            result = self._apply(self.trigram, result)
        return result

    def _apply(self, model, tokens: Sequence[str]) -> List[str]:
        if model is None:
            return list(tokens)
        transformed = list(model[list(tokens)])
        return self._reject_forbidden(transformed)

    def _reject_forbidden(self, tokens: Sequence[str]) -> List[str]:
        """placeholder 가 섞인 phrase 는 거부하고 원래 token 으로 복원한다 (§17)."""
        result: List[str] = []
        for token in tokens:
            if self.delimiter in token and self._contains_forbidden(token):
                result.extend(token.split(self.delimiter))
            else:
                result.append(token)
        return result

    def _contains_forbidden(self, token: str) -> bool:
        parts = token.split(self.delimiter)
        return any(part in self.forbidden for part in parts)

    # ------------------------------------------------------------------
    def phrase_count(self, model=None) -> int:
        model = model if model is not None else self.bigram
        if model is None:
            return 0
        phrasegrams = getattr(model, "phrasegrams", {})
        return sum(
            1 for key in phrasegrams
            if not self._contains_forbidden(
                key if isinstance(key, str) else self.delimiter.join(key)
            )
        )

    def learned_phrases(self) -> List[str]:
        """placeholder 가 없는 유효 phrase 목록 (User Dictionary 후보, §14)."""
        phrases: List[str] = []
        for model in (self.bigram, self.trigram):
            if model is None:
                continue
            for key in getattr(model, "phrasegrams", {}):
                token = key if isinstance(key, str) else self.delimiter.join(key)
                if not self._contains_forbidden(token):
                    phrases.append(token)
        return sorted(set(phrases))

    # ------------------------------------------------------------------
    def save(self, directory: str | Path) -> None:
        if not _HAS_GENSIM:
            return
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        if self.bigram is not None:
            self.bigram.save(str(directory / "phrases_bigram.model"))
        if self.trigram is not None:
            self.trigram.save(str(directory / "phrases_trigram.model"))

    def load(self, directory: str | Path) -> "PhraseProcessor":
        if not _HAS_GENSIM:
            return self
        from gensim.models.phrases import FrozenPhrases

        directory = Path(directory)
        bigram_path = directory / "phrases_bigram.model"
        trigram_path = directory / "phrases_trigram.model"
        if bigram_path.exists():
            self.bigram = FrozenPhrases.load(str(bigram_path))
        if trigram_path.exists():
            self.trigram = FrozenPhrases.load(str(trigram_path))
        return self
