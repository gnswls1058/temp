"""Candidate Relation 생성 (§26 ~ §29).

FastText 유사도를 그대로 유의어로 저장하지 않는다. LLM 검증 대상 후보만 만든다.

이 corpus 에서 확인된 사실 두 가지가 설계를 좌우한다.

1. 절대 cosine 은 변별력이 없다. 후보의 96%가 0.87 이상이고, 검증된 쌍에서
   NEAR_SYNONYM 을 가려내는 ROC-AUC 가 0.61 수준이다. 그래서 cosine 을
   개수 조절 노브로 쓰지 않고, 이웃 목록에서의 **순위**를 신호로 쓴다.
2. FastText 는 빈도가 낮은 term 을 다루지 못한다. 그런데 프로젝트 고유 용어일수록
   빈도가 낮다. 그래서 문자열 규칙 경로를 따로 두고, 빈도 하한은 FastText 경로에만
   적용한다.
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from app.models.enums import PLACEHOLDER_TOKENS, CandidateSource, CandidateStatus
from app.models.relation import CandidateRelation
from app.models.term import Term

logger = logging.getLogger(__name__)

# 영문 대문자 약어(FO, AI, DB, MES …)는 길이 규칙에서 보호한다 (§29).
_UPPER_ABBREVIATION_RE = re.compile(r"^[A-Z][A-Z0-9]{0,7}$")
_HAS_MEANING_RE = re.compile(r"[0-9A-Za-z가-힣]")
_HANGUL_RE = re.compile(r"[가-힣]")
# 복합어 끝에 붙으면 분석 오류인 한 글자들(조사·어미). '키', '값'처럼 실제
# 핵명사가 되는 한 글자와 구분하기 위해 명시적으로 나열한다.
_TRAILING_PARTICLES = frozenset(
    "로 을 를 이 가 은 는 에 의 와 과 도 만 서 고 나 며 야 라 죠 함 됨 임 음".split()
)
# 수량·시점 표현은 용어가 아니다. <DATE> 정규화가 잡지 못하는 '3월', '20건' 등.
# 분포 기반 경로. 이들만으로 지목된 쌍은 신호가 없다시피 하다.
_DISTRIBUTIONAL_SOURCES = {
    CandidateSource.FASTTEXT.value,
    CandidateSource.CONTEXT_PROFILE.value,
}

_MEASURE_RE = re.compile(r"^\d+\s*(년|월|일|시|분|초|개|건|명|원|만원|주|회|차|번|배|위|%)$")

# 후보 자격 박탈 사유. term catalog 에는 남기고 이 값만 기록한다.
REASON_PLACEHOLDER = "placeholder"
REASON_STOPWORD = "stopword"
REASON_NO_MEANING = "무의미 토큰"
REASON_SENTENCE_RATIO = "문장 근거 없음"
REASON_VERB_STEM = "용언 어간"
REASON_MALFORMED = "형태소 분석 오류"
REASON_MEASURE = "수량·시점 표현"
REASON_TOO_SHORT = "한 글자"
REASON_LOW_FREQUENCY = "저빈도(FastText 경로 제외)"


class CandidateGenerator:
    def __init__(
        self,
        *,
        top_n: int = 20,
        min_similarity: float = 0.60,
        min_term_frequency: int = 3,
        min_term_length: int = 2,
        max_candidates_per_term: int = 10,
        max_total_candidates: int = 5000,
        stopwords: Sequence[str] = (),
        delimiter: str = "_",
        excluded_pairs: Sequence[str] = (),
        min_sentence_ratio: float = 0.0,
        mutual_top_k: int = 0,
        strong_rank_k: int = 3,
        verb_stems: Sequence[str] = (),
        profile_index=None,
        profile_top_n: int = 20,
        profile_min_similarity: float = 0.15,
        lexical_pairs: Optional[Dict[Tuple[str, str], Tuple[List[str], float]]] = None,
        llm_pairs: Optional[Dict[Tuple[str, str], float]] = None,
        require_corroboration: bool = False,
    ):
        self.top_n = top_n
        self.min_similarity = min_similarity
        self.min_term_frequency = min_term_frequency
        self.min_term_length = min_term_length
        self.max_candidates_per_term = max_candidates_per_term
        self.max_total_candidates = max_total_candidates
        self.stopwords = set(stopwords)
        self.delimiter = delimiter
        # 사람 검수에서 제거한 쌍 (canonical pair_key). 다시 후보로 올리지 않는다.
        self.excluded_pairs = set(excluded_pairs)
        # 표 셀이나 코드 조각으로만 등장하는 토큰을 걸러내기 위한 하한
        self.min_sentence_ratio = min_sentence_ratio
        # 상호 최근접 조건. hard filter 가 아니라 느슨한 recall 조건으로 쓴다.
        # "서로 top-k 안" 또는 "한쪽이 top-strong_rank_k 안" 이면 통과시킨다.
        self.mutual_top_k = mutual_top_k
        self.strong_rank_k = strong_rank_k
        # 용언으로만 등장한 어간('돌려주', '묶이'). 문맥에는 쓰지만 표제어는 될 수 없다.
        self.verb_stems = set(verb_stems)
        # 문자열 규칙 경로가 찾아낸 쌍. 빈도 하한을 적용하지 않는다.
        self.lexical_pairs = lexical_pairs or {}
        # 주변 단어 분포 비교 경로. FastText 와 잡아내는 것이 다르므로 병행한다.
        self.profile_index = profile_index
        self.profile_top_n = profile_top_n
        self.profile_min_similarity = profile_min_similarity
        # LLM 이 용어 목록을 읽고 제안한 쌍. 분포 통계가 놓치는 구간을 메운다.
        self.llm_pairs = llm_pairs or {}
        # 분포 경로(FastText / 문맥 프로파일)만 지목한 쌍을 버릴지 여부.
        # gold set 250쌍 실측: 분포 단독은 유의미 비율 1.9%, 두 분포 경로가
        # 합의해도 0%. 반면 포함관계/문자열/LLM 경로는 62%.
        self.require_corroboration = require_corroboration

    # ------------------------------------------------------------------
    def generate(self, trainer, terms: Iterable[Term],
                 run_id: Optional[str] = None) -> List[CandidateRelation]:
        term_map: Dict[str, Term] = {t.term_key: t for t in terms}

        # term catalog 에는 전부 남기고 자격만 기록한다 (비파괴 보존).
        self.annotate(terms)

        eligible = [k for k, t in term_map.items() if not self.disqualify(t)]
        fasttext_pool = [k for k in eligible
                         if term_map[k].frequency >= self.min_term_frequency]
        fasttext_pool.sort(key=lambda k: term_map[k].frequency, reverse=True)
        logger.info(
            "표제어 자격 %s개 / FastText 경로 대상 %s개 (전체 %s개)",
            len(eligible), len(fasttext_pool), len(term_map),
        )

        pairs: Dict[Tuple[str, str], dict] = {}
        self._add_neighbor_pairs(
            pairs, trainer, fasttext_pool, term_map,
            source=CandidateSource.FASTTEXT,
            top_n=self.top_n, min_similarity=self.min_similarity,
        )
        if self.profile_index is not None:
            self._add_neighbor_pairs(
                pairs, self.profile_index, fasttext_pool, term_map,
                source=CandidateSource.CONTEXT_PROFILE,
                top_n=self.profile_top_n,
                min_similarity=self.profile_min_similarity,
            )
        self._add_lexical_pairs(pairs, set(eligible))
        self._add_llm_pairs(pairs, set(eligible))

        if self.require_corroboration:
            dropped = [p for p, info in pairs.items()
                       if not (info["sources"] - _DISTRIBUTIONAL_SOURCES)]
            for pair in dropped:
                del pairs[pair]
            if dropped:
                logger.info("분포 경로만 지목한 %s쌍을 제외했습니다.", len(dropped))

        candidates = [
            self._to_candidate(pair, info, run_id)
            for pair, info in pairs.items()
            if f"{pair[0]}||{pair[1]}" not in self.excluded_pairs
        ]
        candidates.sort(key=lambda c: c.priority_score, reverse=True)
        candidates = self._cap_degree(candidates)

        if len(candidates) > self.max_total_candidates:
            logger.warning("후보 상한 %s 초과분을 잘라냅니다.", self.max_total_candidates)
            candidates = candidates[: self.max_total_candidates]

        by_source: Dict[str, int] = defaultdict(int)
        for candidate in candidates:
            for source in candidate.sources:
                by_source[source] += 1
        logger.info("Candidate 생성 완료: %s쌍 (경로별 %s)", len(candidates), dict(by_source))
        return candidates

    # ------------------------------------------------------------------
    def _add_neighbor_pairs(self, pairs: Dict[Tuple[str, str], dict], provider,
                            pool: Sequence[str], term_map: Dict[str, Term], *,
                            source: CandidateSource, top_n: int,
                            min_similarity: float) -> None:
        """이웃 목록을 주는 provider(FastText / 문맥 프로파일)로 쌍을 만든다."""
        neighbors = self._collect_neighbors(provider, pool, term_map, top_n)

        for term_key, items in neighbors.items():
            for rank, (other_key, similarity) in enumerate(items, start=1):
                if similarity < min_similarity:
                    break  # most_similar 는 내림차순
                a, b = CandidateRelation.canonical_pair(term_key, other_key)
                info = pairs.setdefault(
                    (a, b),
                    {"sources": set(), "similarity": 0.0, "lexical": 0.0,
                     "llm": 0.0, "rank_a_to_b": None, "rank_b_to_a": None},
                )
                info["sources"].add(source.value)
                if source is CandidateSource.FASTTEXT:
                    info["similarity"] = max(info["similarity"], float(similarity))
                # a→b 방향인지 b→a 방향인지 기록한다.
                field = "rank_a_to_b" if term_key == a else "rank_b_to_a"
                current = info[field]
                info[field] = rank if current is None else min(current, rank)

        # 느슨한 상호 최근접 조건으로 걸러낸다.
        if self.mutual_top_k > 0:
            for pair in list(pairs):
                info = pairs[pair]
                if CandidateSource.FASTTEXT.value not in info["sources"]:
                    continue
                if not self._passes_rank_condition(info):
                    del pairs[pair]

    def _passes_rank_condition(self, info: dict) -> bool:
        """양쪽이 서로 top-k 안이거나, 한쪽이 아주 앞순위면 통과시킨다.

        hard filter 로 쓰던 '서로 top-5' 는 recall 을 크게 깎는다. 순위 정보 자체는
        점수로 보존하고, 여기서는 명백히 약한 신호만 떨어뜨린다.
        """
        rank_a, rank_b = info["rank_a_to_b"], info["rank_b_to_a"]
        if rank_a is not None and rank_b is not None:
            if rank_a <= self.mutual_top_k and rank_b <= self.mutual_top_k:
                return True
        best = min(r for r in (rank_a, rank_b) if r is not None)
        return best <= self.strong_rank_k

    def _add_lexical_pairs(self, pairs: Dict[Tuple[str, str], dict],
                           eligible: Set[str]) -> None:
        """문자열 규칙 후보. 빈도 하한을 적용하지 않는다."""
        for (a, b), (sources, score) in self.lexical_pairs.items():
            if a not in eligible or b not in eligible:
                continue
            info = pairs.setdefault(
                (a, b),
                {"sources": set(), "similarity": 0.0, "lexical": 0.0,
                 "llm": 0.0, "rank_a_to_b": None, "rank_b_to_a": None},
            )
            info["sources"].update(sources)
            info["lexical"] = max(info["lexical"], float(score))

    def _add_llm_pairs(self, pairs: Dict[Tuple[str, str], dict],
                       eligible: Set[str]) -> None:
        """LLM 제안 쌍. 문자열 규칙과 마찬가지로 빈도 하한을 적용하지 않는다."""
        for (a, b), score in self.llm_pairs.items():
            if a not in eligible or b not in eligible:
                continue
            info = pairs.setdefault(
                (a, b),
                {"sources": set(), "similarity": 0.0, "lexical": 0.0,
                 "llm": 0.0, "rank_a_to_b": None, "rank_b_to_a": None},
            )
            info["sources"].add(CandidateSource.LLM_PROPOSED.value)
            info["llm"] = max(info.get("llm", 0.0), float(score))

    # ------------------------------------------------------------------
    def _to_candidate(self, pair: Tuple[str, str], info: dict,
                      run_id: Optional[str]) -> CandidateRelation:
        a, b = pair
        return CandidateRelation(
            term_a=a,
            term_b=b,
            fasttext_similarity=round(info["similarity"], 4),
            status=CandidateStatus.PENDING,
            run_id=run_id,
            sources=sorted(info["sources"]),
            rank_a_to_b=info["rank_a_to_b"],
            rank_b_to_a=info["rank_b_to_a"],
            lexical_score=round(info["lexical"], 4),
            priority_score=self.priority_score(info),
        )

    def priority_score(self, info: dict) -> float:
        """후보 정렬 점수.

        cosine 은 변별력이 없으므로 비중을 낮게 두고, 문자열 근거와 이웃 순위,
        여러 경로가 겹쳤는지를 함께 본다. 이 점수는 순위를 매기기 위한 것이지
        유의어 확률이 아니다.
        """
        rank_a, rank_b = info["rank_a_to_b"], info["rank_b_to_a"]
        ranks = [r for r in (rank_a, rank_b) if r is not None]
        rank_score = sum(1.0 / r for r in ranks) / 2 if ranks else 0.0
        mutual_bonus = 0.15 if len(ranks) == 2 else 0.0
        source_bonus = 0.15 * (len(info["sources"]) - 1)
        # LLM 은 실제 문맥을 읽고 판단하므로 분포 통계보다 신뢰도가 높다.
        return round(
            0.80 * info.get("llm", 0.0)
            + 0.45 * info["lexical"]
            + 0.30 * rank_score
            + 0.10 * info["similarity"]
            + mutual_bonus
            + source_bonus,
            6,
        )

    def _cap_degree(self, candidates: Sequence[CandidateRelation]) -> List[CandidateRelation]:
        """term 하나가 가질 수 있는 최종 관계 수를 제한한다.

        기존 구현은 루프의 기준 term 에만 상한을 걸어서 실제 degree 가 상한을
        넘었다(최대 11). 여기서는 양쪽 degree 를 모두 세어 실제로 제한한다.
        점수 내림차순으로 훑으므로 약한 쌍부터 잘린다.
        """
        if self.max_candidates_per_term <= 0:
            return list(candidates)

        degree: Dict[str, int] = defaultdict(int)
        chosen: Set[int] = set()
        kept: List[CandidateRelation] = []

        def take(index: int, candidate: CandidateRelation) -> None:
            chosen.add(index)
            degree[candidate.term_a] += 1
            degree[candidate.term_b] += 1
            kept.append(candidate)

        # 점수 내림차순으로 배분한다. 약한 쌍부터 잘린다.
        for index, candidate in enumerate(candidates):
            if index in chosen:
                continue
            if (degree[candidate.term_a] >= self.max_candidates_per_term
                    or degree[candidate.term_b] >= self.max_candidates_per_term):
                continue
            take(index, candidate)

        kept.sort(key=lambda c: c.priority_score, reverse=True)
        return kept

    def _collect_neighbors(self, trainer, pool: Sequence[str],
                           term_map: Dict[str, Term],
                           top_n: Optional[int] = None) -> Dict[str, List[tuple]]:
        """후보 자격이 있는 상대만 남긴 이웃 목록을 term 별로 만든다."""
        neighbors: Dict[str, List[tuple]] = {}
        for term_key in pool:
            if not trainer.has_term(term_key):
                continue
            neighbors[term_key] = [
                (other, score)
                for other, score in trainer.most_similar(term_key, top_n or self.top_n)
                if self._is_valid_counterpart(term_key, other, term_map)
            ]
        return neighbors

    # ------------------------------------------------------------------
    def annotate(self, terms: Iterable[Term]) -> None:
        """term 마다 후보 자격과 제외 사유를 채운다. 행을 지우지 않는다."""
        for term in terms:
            term.is_stopword = term.term_key in self.stopwords
            term.excluded_reason = self.disqualify(term) or ""

    def disqualify(self, term: Term) -> str:
        """후보 자격이 없으면 사유 문자열, 있으면 빈 문자열을 돌려준다.

        빈도는 여기서 보지 않는다. 저빈도는 'FastText 경로 제외'일 뿐
        '표제어 자격 없음'이 아니기 때문이다 (문자열 규칙 경로는 계속 쓴다).
        """
        key = term.term_key
        if key in PLACEHOLDER_TOKENS:
            return REASON_PLACEHOLDER
        if key in self.stopwords:
            return REASON_STOPWORD
        if not _HAS_MEANING_RE.search(key):
            return REASON_NO_MEANING
        if term.sentence_ratio < self.min_sentence_ratio:
            return REASON_SENTENCE_RATIO
        if self._is_verb_like(key):
            return REASON_VERB_STEM
        if self._has_broken_part(key):
            return REASON_MALFORMED
        if _MEASURE_RE.match(key.replace(self.delimiter, " ")):
            return REASON_MEASURE
        # 짧다는 이유만으로 사내 약어를 버리지 않는다.
        if len(key) < self.min_term_length and not self.is_protected_abbreviation(key):
            return REASON_TOO_SHORT
        return ""

    def is_eligible(self, term: Term) -> bool:
        return not self.disqualify(term)

    def _has_broken_part(self, term_key: str) -> bool:
        """복합어 앞쪽에 한 글자 한글 조각이 섞였으면 분석 오류로 보고 버린다.

        '을_반환', '버_수정' 처럼 조사나 잘린 어절이 앞에 붙은 토큰이 여기 걸린다.
        마지막 성분은 대상이 아니다. '중복 방지 키', '잔여 값'처럼 한 글자 핵명사로
        끝나는 정상 용어를 죽이면 안 된다.
        영문 한 글자('A_등급')는 약어일 수 있으므로 대상이 아니다.
        """
        parts = [p for p in re.split(rf"[{re.escape(self.delimiter)} ]", term_key) if p]
        if len(parts) < 2:
            return False
        if any(len(p) == 1 and _HANGUL_RE.match(p) for p in parts[:-1]):
            return True
        # 끝이 조사면 어절이 잘린 것이다('DB 로', '인덱스 를').
        return parts[-1] in _TRAILING_PARTICLES

    def _is_verb_like(self, term_key: str) -> bool:
        """표제어가 용언 어간으로 끝나면 후보에서 뺀다.

        한국어 복합어는 뒤쪽이 핵이므로 마지막 성분만 본다.
        '지연_로딩' 처럼 명사로 끝나는 복합어는 그대로 남는다.
        """
        if not self.verb_stems:
            return False
        last = term_key.split(self.delimiter)[-1].split(" ")[-1]
        return term_key in self.verb_stems or last in self.verb_stems

    @staticmethod
    def is_protected_abbreviation(term_key: str) -> bool:
        return bool(_UPPER_ABBREVIATION_RE.match(term_key))

    def _is_valid_counterpart(self, term_key: str, other_key: str,
                              term_map: Dict[str, Term]) -> bool:
        if other_key == term_key:
            return False
        if other_key in PLACEHOLDER_TOKENS or other_key in self.stopwords:
            return False
        if not _HAS_MEANING_RE.search(other_key):
            return False
        # 표기만 다른 동일 문자열은 문자열 규칙 경로가 담당한다.
        if term_key.replace(self.delimiter, "") == other_key.replace(self.delimiter, ""):
            return False

        other = term_map.get(other_key)
        if other is None:
            # corpus 통계에 없는 subword 유래 토큰은 제외한다.
            return False
        # 상대편도 벡터 품질이 담보돼야 한다. 저빈도 상대는 문자열 규칙 경로가 맡는다.
        if other.frequency < self.min_term_frequency:
            return False
        return self.is_eligible(other)
