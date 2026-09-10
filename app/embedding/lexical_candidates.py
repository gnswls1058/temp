"""문자열 규칙 기반 후보 생성 (§26 확장).

FastText 는 빈도가 낮은 term 에 쓸 만한 벡터를 주지 못한다. 그런데 프로젝트
고유 용어일수록 빈도가 낮다. 그래서 빈도와 무관하게 동작하는 경로를 따로 둔다.

여기서 만드는 것은 어디까지나 '후보'다. '메일 인증'과 '인증 메일'이 실제로
같은 뜻인지는 LLM 이 문맥을 보고 판단한다.
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from itertools import combinations
from typing import Dict, Iterable, List, Sequence, Tuple

from app.models.term import Term

logger = logging.getLogger(__name__)

_NON_WORD_RE = re.compile(r"[^0-9A-Za-z가-힣]+")
_UPPER_ACRONYM_RE = re.compile(r"^[A-Z][A-Z0-9]{1,7}$")


def _parts(term_key: str, delimiter: str) -> List[str]:
    return [p for p in _NON_WORD_RE.split(term_key.replace(delimiter, " ")) if p]


def _squash(term_key: str, delimiter: str) -> str:
    return "".join(_parts(term_key, delimiter)).lower()


class LexicalCandidateFinder:
    """빈도 하한 없이 동작하는 문자열 규칙 후보 생성기."""

    def __init__(self, *, delimiter: str = "_", min_part_length: int = 2):
        self.delimiter = delimiter
        self.min_part_length = min_part_length

    # ------------------------------------------------------------------
    def find(self, terms: Sequence[Term]) -> Dict[Tuple[str, str], Tuple[List[str], float]]:
        """``{(term_a, term_b): (sources, lexical_score)}`` 를 돌려준다."""
        found: Dict[Tuple[str, str], Tuple[List[str], float]] = {}

        for pair, score in self._word_order_variants(terms).items():
            found[pair] = (["LEXICAL"], score)

        for pair, score in self._acronym_matches(terms).items():
            sources, prev = found.get(pair, ([], 0.0))
            found[pair] = (sorted(set(sources + ["LEXICAL"])), max(prev, score))

        for pair, score in self._containment(terms).items():
            sources, prev = found.get(pair, ([], 0.0))
            found[pair] = (sorted(set(sources + ["CONTAINMENT"])), max(prev, score))

        logger.info("문자열 규칙 후보 %s쌍", len(found))
        return found

    # ------------------------------------------------------------------
    def _word_order_variants(self, terms: Sequence[Term]) -> Dict[Tuple[str, str], float]:
        """어순·띄어쓰기만 다른 쌍. '메일 인증' ↔ '인증 메일'."""
        buckets: Dict[frozenset, List[str]] = defaultdict(list)
        squashed: Dict[str, List[str]] = defaultdict(list)

        for term in terms:
            parts = _parts(term.term_key, self.delimiter)
            if len(parts) >= 2:
                buckets[frozenset(p.lower() for p in parts)].append(term.term_key)
            squashed[_squash(term.term_key, self.delimiter)].append(term.term_key)

        pairs: Dict[Tuple[str, str], float] = {}
        for keys in buckets.values():
            for a, b in combinations(sorted(set(keys)), 2):
                pairs[(a, b)] = 1.0
        for keys in squashed.values():
            # 띄어쓰기만 다른 경우. 표기 차이일 뿐이므로 점수를 최고로 준다.
            for a, b in combinations(sorted(set(keys)), 2):
                pairs[(a, b)] = 1.0
        return pairs

    def _acronym_matches(self, terms: Sequence[Term]) -> Dict[Tuple[str, str], float]:
        """머리글자 약어. 'API' ↔ 'Application Programming Interface'."""
        acronyms = [t.term_key for t in terms
                    if _UPPER_ACRONYM_RE.match(t.term_key.replace(self.delimiter, ""))]
        if not acronyms:
            return {}

        initials: Dict[str, List[str]] = defaultdict(list)
        for term in terms:
            parts = _parts(term.term_key, self.delimiter)
            if len(parts) < 2:
                continue
            head = "".join(p[0] for p in parts).upper()
            initials[head].append(term.term_key)

        pairs: Dict[Tuple[str, str], float] = {}
        for acronym in acronyms:
            plain = acronym.replace(self.delimiter, "").upper()
            for other in initials.get(plain, []):
                if other == acronym:
                    continue
                a, b = sorted((acronym, other))
                pairs[(a, b)] = 0.9
        return pairs

    def _containment(self, terms: Sequence[Term]) -> Dict[Tuple[str, str], float]:
        """한쪽이 다른 쪽을 통째로 포함하는 쌍. '인덱스' ↔ '전문 인덱스'.

        이것은 유의어가 아니라 상하위 관계일 가능성이 높다. 후보로 올리되
        방향 판정은 LLM 에 맡긴다.
        """
        by_parts = [(t.term_key, _parts(t.term_key, self.delimiter)) for t in terms]
        shorts = [(k, p[0]) for k, p in by_parts
                  if len(p) == 1 and len(p[0]) >= self.min_part_length]
        longs = [(k, [x.lower() for x in p]) for k, p in by_parts if len(p) >= 2]

        pairs: Dict[Tuple[str, str], float] = {}
        for short_key, word in shorts:
            lowered = word.lower()
            for long_key, parts in longs:
                if lowered not in parts:
                    continue
                a, b = sorted((short_key, long_key))
                # 구성 요소 수가 적을수록 가까운 관계로 본다.
                pairs[(a, b)] = round(1.0 / len(parts), 4)
        return pairs
