"""FastText 학습 corpus 생성 (§19).

전처리 결과(normalize -> Komoran -> Phrases)를 token 열로 모아 학습 입력을 만든다.
원본 문서/문장은 그대로 남고, 학습용 데이터만 별도로 생성된다 (§5.1).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, List, Sequence

from app.models.context import Sentence

logger = logging.getLogger(__name__)


class CorpusBuilder:
    def __init__(self, *, min_tokens_per_sentence: int = 2):
        self.min_tokens_per_sentence = min_tokens_per_sentence

    def build(self, sentences: Iterable[Sentence]) -> List[List[str]]:
        corpus = [
            list(s.phrase_tokens or s.tokens)
            for s in sentences
            if len(s.phrase_tokens or s.tokens) >= self.min_tokens_per_sentence
        ]
        logger.info("학습 corpus 문장 수: %s", len(corpus))
        return corpus

    @staticmethod
    def save(corpus: Sequence[Sequence[str]], path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fp:
            for tokens in corpus:
                fp.write(" ".join(tokens) + "\n")
        logger.info("학습 corpus 저장: %s", path)
        return path

    @staticmethod
    def load(path: str | Path) -> List[List[str]]:
        path = Path(path)
        with path.open("r", encoding="utf-8") as fp:
            return [line.split() for line in fp if line.strip()]
