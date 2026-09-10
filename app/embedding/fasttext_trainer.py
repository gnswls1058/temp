"""FastText 학습 (§23 ~ §25).

FastText 는 "유의어 판정기"가 아니라 "문맥적으로 유사하게 사용되는 용어 후보 탐색기"다.
여기서는 학습과 유사 term 조회까지만 담당하고, 의미 관계 판정은 하지 않는다.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

try:
    from gensim.models import FastText
    _HAS_GENSIM = True
except ImportError:  # pragma: no cover
    FastText = None  # type: ignore
    _HAS_GENSIM = False


class FastTextUnavailableError(RuntimeError):
    pass


class FastTextTrainer:
    def __init__(
        self,
        *,
        vector_size: int = 100,
        window: int = 5,
        min_count: int = 2,
        sg: int = 1,
        epochs: int = 20,
        min_n: int = 2,
        max_n: int = 5,
        workers: int = 4,
        seed: int = 42,
        model_filename: str = "fasttext.model",
    ):
        self.params = dict(
            vector_size=vector_size,
            window=window,
            min_count=min_count,
            sg=sg,
            epochs=epochs,
            min_n=min_n,
            max_n=max_n,
            workers=workers,
            seed=seed,
        )
        self.model_filename = model_filename
        self.model = None

    # ------------------------------------------------------------------
    def train(self, corpus: Sequence[Sequence[str]]):
        if not _HAS_GENSIM:
            raise FastTextUnavailableError(
                "gensim 이 설치되지 않았습니다. `pip install gensim` 을 실행하세요."
            )
        if not corpus:
            raise ValueError("FastText 학습 corpus 가 비어 있습니다.")

        logger.info("FastText 학습 시작 - 문장 %s개, params=%s", len(corpus), self.params)
        model = FastText(**self.params)
        model.build_vocab(corpus_iterable=corpus)
        model.train(
            corpus_iterable=corpus,
            total_examples=model.corpus_count,
            epochs=model.epochs,
        )
        self.model = model
        logger.info("FastText 학습 완료 - vocabulary %s개", self.vocabulary_size)
        return model

    # ------------------------------------------------------------------
    @property
    def vocabulary_size(self) -> int:
        if self.model is None:
            return 0
        return len(self.model.wv.key_to_index)

    def has_term(self, term: str) -> bool:
        return self.model is not None and term in self.model.wv.key_to_index

    def most_similar(self, term: str, top_n: int = 20) -> List[Tuple[str, float]]:
        if self.model is None:
            raise RuntimeError("FastText 모델이 학습되지 않았습니다.")
        if term not in self.model.wv.key_to_index:
            return []
        return [(t, float(s)) for t, s in self.model.wv.most_similar(term, topn=top_n)]

    def vocabulary(self) -> List[str]:
        if self.model is None:
            return []
        return list(self.model.wv.key_to_index.keys())

    # ------------------------------------------------------------------
    def save(self, directory: str | Path) -> Path:
        if self.model is None:
            raise RuntimeError("저장할 모델이 없습니다.")
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / self.model_filename
        self.model.save(str(path))
        logger.info("FastText 모델 저장: %s", path)
        return path

    def load(self, directory: str | Path):
        if not _HAS_GENSIM:
            raise FastTextUnavailableError("gensim 이 설치되지 않았습니다.")
        path = Path(directory) / self.model_filename
        if not path.exists():
            raise FileNotFoundError(f"모델 파일이 없습니다: {path}")
        self.model = FastText.load(str(path))
        logger.info("FastText 모델 로딩: %s (vocab %s)", path, self.vocabulary_size)
        return self.model


class NullFastTextTrainer:
    """FastText 를 끈 경우에 쓰는 빈 구현.

    내부망처럼 gensim 설치가 어려운 환경에서 임베딩 경로만 비활성화하고
    나머지 후보 경로(포함관계 / 문자열 규칙 / LLM 제안)로 파이프라인을 돌린다.
    NEXBRIDGE gold set 250쌍 실측에서 분포 경로 단독의 유의미 비율은 1.9% 였고,
    끄더라도 정답 회수는 그대로였다.
    """

    def train(self, corpus):
        logger.info("fasttext.enabled=false — 임베딩 학습을 건너뜁니다.")
        return self

    @property
    def vocabulary_size(self) -> int:
        return 0

    def has_term(self, term: str) -> bool:
        return False

    def most_similar(self, term: str, top_n: int = 20):
        return []

    def vocabulary(self):
        return []

    def save(self, directory):
        return None

    def load(self, directory):
        return self
