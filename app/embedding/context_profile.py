"""문맥 프로파일(PPMI) 기반 유사도 (§26 확장).

FastText 는 이 규모의 corpus 에서 의미 동일성보다 형태적 파생과 주제 근접성을
학습한다. '탈퇴'의 이웃으로 '탈퇴_후', '탈퇴_회원'을 주지 '해지'를 주지 않는다.

유의어는 같은 문서에 함께 등장하지 않아도 **같은 단어들과 어울린다**.
'회원을 조회한다' 와 '고객을 조회한다' 처럼. 그래서 각 term 의 주변 단어 분포를
PPMI 로 가중해 벡터로 만들고 코사인으로 비교한다.

학습이 아니라 세기(counting)라서 재현성이 완전하고, 규칙을 사람이 쓸 필요도 없다.
NEXBRIDGE corpus 실측(정답 8쌍 기준 이웃 순위):

    쌍                     FastText   PPMI
    포인트 ↔ 적립금            18위      3위
    회원 ↔ 고객               >50위     8위
    탈퇴 ↔ 해지               22위      4위
    멱등키 ↔ 중복 방지 키          9위     >50위

서로 잡아내는 것이 다르므로 대체가 아니라 병행한다.
"""
from __future__ import annotations

import logging
import math
from collections import Counter, defaultdict
from typing import Dict, List, Sequence, Tuple

logger = logging.getLogger(__name__)


class ContextProfileIndex:
    """term 의 주변 단어 분포를 PPMI 로 가중한 뒤 코사인 유사도를 준다."""

    def __init__(self, *, window: int = 5, min_count: int = 5,
                 max_neighbors: int = 50):
        self.window = window
        self.min_count = min_count
        self.max_neighbors = max_neighbors
        self._vocab: List[str] = []
        self._index: Dict[str, int] = {}
        self._vectors = None          # numpy 배열 (L2 정규화된 PPMI)

    # ------------------------------------------------------------------
    def build(self, corpus: Sequence[Sequence[str]]) -> "ContextProfileIndex":
        try:
            import numpy as np
        except ImportError:  # pragma: no cover
            logger.warning("numpy 가 없어 문맥 프로파일 경로를 건너뜁니다.")
            return self

        counts: Counter = Counter()
        contexts: Dict[str, Counter] = defaultdict(Counter)
        for tokens in corpus:
            length = len(tokens)
            for position, token in enumerate(tokens):
                counts[token] += 1
                start = max(0, position - self.window)
                stop = min(length, position + self.window + 1)
                for other in range(start, stop):
                    if other != position:
                        contexts[token][tokens[other]] += 1

        self._vocab = [w for w, c in counts.items() if c >= self.min_count]
        self._index = {w: i for i, w in enumerate(self._vocab)}
        if not self._vocab:
            logger.warning("문맥 프로파일 대상 term 이 없습니다.")
            return self

        column_totals: Counter = Counter()
        for term in self._vocab:
            for context, n in contexts[term].items():
                column_totals[context] += n
        grand_total = sum(column_totals.values())
        columns = {c: i for i, c in enumerate(column_totals)}

        matrix = np.zeros((len(self._vocab), len(columns)), dtype="float32")
        for term in self._vocab:
            row = self._index[term]
            row_total = sum(contexts[term].values())
            for context, n in contexts[term].items():
                # PPMI: 우연보다 자주 함께 나오는 문맥만 남긴다.
                pmi = math.log((n * grand_total) / (row_total * column_totals[context]) + 1e-12)
                if pmi > 0:
                    matrix[row, columns[context]] = pmi

        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        self._vectors = matrix / norms
        logger.info(
            "문맥 프로파일 생성 - term %s개, 문맥 차원 %s개",
            len(self._vocab), len(columns),
        )
        return self

    # ------------------------------------------------------------------
    @property
    def vocabulary_size(self) -> int:
        return len(self._vocab)

    def has_term(self, term: str) -> bool:
        return self._vectors is not None and term in self._index

    def most_similar(self, term: str, top_n: int = 20) -> List[Tuple[str, float]]:
        if not self.has_term(term):
            return []
        import numpy as np

        similarities = self._vectors @ self._vectors[self._index[term]]
        limit = min(top_n + 1, len(self._vocab))
        candidates = np.argpartition(-similarities, limit - 1)[:limit]
        ordered = candidates[np.argsort(-similarities[candidates])]
        return [
            (self._vocab[i], float(similarities[i]))
            for i in ordered if self._vocab[i] != term
        ][:top_n]
