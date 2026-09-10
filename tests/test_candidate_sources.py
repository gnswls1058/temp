"""여러 후보 생성 경로와 순위 기반 점수 테스트.

FastText 하나에 의존하지 않고, 빈도 하한이 걸리지 않는 문자열 규칙 경로를 함께 둔다.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.embedding.candidate_generator import CandidateGenerator  # noqa: E402
from app.embedding.lexical_candidates import LexicalCandidateFinder  # noqa: E402
from app.models.enums import CandidateSource  # noqa: E402
from app.models.term import Term  # noqa: E402


def term(key, frequency=10):
    return Term(term_key=key, display_term=key.replace("_", " "), frequency=frequency)


class StubTrainer:
    def __init__(self, neighbors=None):
        self.neighbors = neighbors or {}

    def has_term(self, key):
        return key in self.neighbors

    def most_similar(self, key, top_n=20):
        return self.neighbors.get(key, [])[:top_n]


# ----------------------------------------------------------------------
def test_word_order_variants_found_without_fasttext():
    """'메일 인증' ↔ '인증 메일' 은 빈도가 낮아도 찾아야 한다."""
    terms = [term("메일_인증", frequency=2), term("인증_메일", frequency=1)]
    found = LexicalCandidateFinder().find(terms)

    assert ("메일_인증", "인증_메일") in found
    sources, score = found[("메일_인증", "인증_메일")]
    assert "LEXICAL" in sources and score == 1.0


def test_containment_pairs_are_marked_separately():
    """'인덱스' ↔ '전문 인덱스' 는 유의어가 아니라 포함 관계 후보다."""
    found = LexicalCandidateFinder().find([term("인덱스"), term("전문_인덱스")])

    sources, _ = found[("인덱스", "전문_인덱스")]
    assert sources == ["CONTAINMENT"]


def test_acronym_matches_initials():
    found = LexicalCandidateFinder().find(
        [term("API"), term("Application_Programming_Interface")]
    )
    assert ("API", "Application_Programming_Interface") in found


def test_low_frequency_pairs_survive_lexical_path():
    """FastText 경로에서 탈락한 저빈도 term 도 문자열 규칙으로는 후보가 된다."""
    terms = [term("메일_인증", frequency=2), term("인증_메일", frequency=1)]
    generator = CandidateGenerator(
        min_term_frequency=5,
        lexical_pairs=LexicalCandidateFinder().find(terms),
    )
    candidates = generator.generate(StubTrainer(), terms)

    assert len(candidates) == 1
    assert candidates[0].has_source(CandidateSource.LEXICAL)


# ----------------------------------------------------------------------
def test_ranks_are_preserved_both_directions():
    """cosine 대신 순위를 보존해야 나중에 calibration 할 수 있다."""
    trainer = StubTrainer({
        "FO": [("잡음", 0.99), ("패밀리_오픈", 0.95)],
        "패밀리_오픈": [("FO", 0.95)],
        "잡음": [("FO", 0.99)],
    })
    terms = [term("FO"), term("패밀리_오픈"), term("잡음")]
    generator = CandidateGenerator(min_similarity=0.6, mutual_top_k=10)

    pairs = {(c.term_a, c.term_b): c for c in generator.generate(trainer, terms)}
    pair = pairs[("FO", "패밀리_오픈")]
    assert pair.rank_a_to_b == 2   # FO 이웃 목록에서 2위
    assert pair.rank_b_to_a == 1   # 패밀리_오픈 이웃 목록에서 1위


def test_one_way_far_rank_is_dropped():
    """한쪽만, 그것도 뒷순위로 지목한 쌍은 신호가 약하다."""
    neighbors = [(f"n{i}", 0.99 - i * 0.001) for i in range(9)] + [("대상", 0.9)]
    trainer = StubTrainer({"기준": neighbors, "대상": [("무관", 0.99)]})
    terms = [term("기준"), term("대상"), term("무관")] + [term(f"n{i}") for i in range(9)]

    generator = CandidateGenerator(min_similarity=0.6, mutual_top_k=5, strong_rank_k=3)
    pairs = {(c.term_a, c.term_b) for c in generator.generate(trainer, terms)}
    assert ("기준", "대상") not in pairs


def test_mutual_pair_beats_one_way_pair_in_priority():
    trainer = StubTrainer({
        "A": [("B", 0.90), ("C", 0.99)],
        "B": [("A", 0.90)],
        "C": [("무관", 0.99)],
    })
    terms = [term("A"), term("B"), term("C"), term("무관")]
    generator = CandidateGenerator(min_similarity=0.6, mutual_top_k=10)

    candidates = generator.generate(trainer, terms)
    ordered = [(c.term_a, c.term_b) for c in candidates]
    assert ordered.index(("A", "B")) < ordered.index(("A", "C"))


# ----------------------------------------------------------------------
def test_degree_cap_limits_actual_degree():
    """상한은 기준 term 방향이 아니라 실제 degree 에 걸려야 한다."""
    trainer = StubTrainer({
        "허브": [(f"n{i}", 0.99) for i in range(10)],
        **{f"n{i}": [("허브", 0.99)] for i in range(10)},
    })
    terms = [term("허브")] + [term(f"n{i}") for i in range(10)]

    generator = CandidateGenerator(
        min_similarity=0.6, max_candidates_per_term=3, mutual_top_k=10
    )
    candidates = generator.generate(trainer, terms)

    degree = {}
    for candidate in candidates:
        for key in (candidate.term_a, candidate.term_b):
            degree[key] = degree.get(key, 0) + 1
    assert max(degree.values()) <= 3


def test_term_catalog_keeps_everything_with_reasons():
    """자격이 없어도 행은 남기고 사유만 기록한다."""
    generator = CandidateGenerator(stopwords=["것"], verb_stems=["돌려주"])
    terms = [term("것"), term("돌려주"), term("을_반환"), term("결제")]
    generator.annotate(terms)

    reasons = {t.term_key: t.excluded_reason for t in terms}
    assert reasons["것"] and reasons["돌려주"] and reasons["을_반환"]
    assert reasons["결제"] == ""
    assert terms[0].is_stopword is True
    assert terms[3].candidate_eligible is True


def test_single_syllable_head_noun_is_kept():
    """'중복 방지 키'처럼 한 글자 핵명사로 끝나는 정상 용어를 버리면 안 된다."""
    from app.embedding.candidate_generator import CandidateGenerator

    generator = CandidateGenerator(min_term_frequency=3)
    for key in ("중복_방지_키", "잔여_값", "A_등급"):
        assert generator.is_eligible(term(key)), key
    # 조사로 끝나면 어절이 잘린 것이다
    for key in ("DB_로", "을_반환", "버_수정"):
        assert not generator.is_eligible(term(key)), key


# ----------------------------------------------------------------------
def test_llm_proposals_bypass_frequency_limit():
    """LLM 제안은 빈도 하한을 받지 않는다. 고유 용어일수록 빈도가 낮다."""
    from app.embedding.candidate_generator import CandidateGenerator
    from app.models.enums import CandidateSource

    terms = [term("회원", frequency=204), term("고객", frequency=3)]
    generator = CandidateGenerator(
        min_term_frequency=50,
        llm_pairs={("고객", "회원"): 1.0},
    )
    candidates = generator.generate(StubTrainer(), terms)

    assert len(candidates) == 1
    assert candidates[0].has_source(CandidateSource.LLM_PROPOSED)


def test_llm_proposals_outrank_distributional_pairs():
    """LLM 은 문맥을 읽고 판단하므로 분포 통계보다 앞에 와야 한다."""
    from app.embedding.candidate_generator import CandidateGenerator

    trainer = StubTrainer({"A": [("B", 0.99)], "B": [("A", 0.99)],
                           "회원": [], "고객": []})
    terms = [term("A"), term("B"), term("회원"), term("고객")]
    generator = CandidateGenerator(
        min_similarity=0.6, llm_pairs={("고객", "회원"): 1.0}
    )
    ordered = [(c.term_a, c.term_b) for c in generator.generate(trainer, terms)]
    assert ordered[0] == ("고객", "회원")


def test_proposals_outside_term_list_are_ignored():
    """LLM 이 만들어낸 term 은 받지 않는다."""
    from app.embedding.candidate_generator import CandidateGenerator

    generator = CandidateGenerator(llm_pairs={("회원", "없는용어"): 1.0})
    assert generator.generate(StubTrainer(), [term("회원")]) == []


def test_distributional_only_pairs_are_dropped_when_corroboration_required():
    """분포 경로만 지목한 쌍은 신호가 없다(gold set 실측 1.9%)."""
    from app.embedding.candidate_generator import CandidateGenerator
    from app.models.enums import CandidateSource

    trainer = StubTrainer({"A": [("B", 0.99)], "B": [("A", 0.99)],
                           "회원": [], "고객": []})
    terms = [term("A"), term("B"), term("회원"), term("고객")]

    loose = CandidateGenerator(min_similarity=0.6, llm_pairs={("고객", "회원"): 1.0})
    assert len(loose.generate(trainer, terms)) == 2

    strict = CandidateGenerator(
        min_similarity=0.6, llm_pairs={("고객", "회원"): 1.0},
        require_corroboration=True,
    )
    kept = strict.generate(trainer, terms)
    assert len(kept) == 1
    assert kept[0].has_source(CandidateSource.LLM_PROPOSED)


def test_corroborated_distributional_pairs_survive():
    """다른 경로가 뒷받침하면 분포 증거도 남는다."""
    from app.embedding.candidate_generator import CandidateGenerator

    trainer = StubTrainer({"인덱스": [("전문_인덱스", 0.99)],
                           "전문_인덱스": [("인덱스", 0.99)]})
    terms = [term("인덱스"), term("전문_인덱스")]
    generator = CandidateGenerator(
        min_similarity=0.6, require_corroboration=True,
        lexical_pairs=LexicalCandidateFinder().find(terms),
    )
    assert len(generator.generate(trainer, terms)) == 1
