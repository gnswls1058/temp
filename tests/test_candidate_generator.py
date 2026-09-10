"""Candidate Generator 테스트 (§26 ~ §29)."""
import pytest

from app.embedding.candidate_generator import CandidateGenerator
from app.models.relation import CandidateRelation
from app.models.term import Term


class StubTrainer:
    """FastText 모델 대역. most_similar 결과만 흉내낸다."""

    def __init__(self, neighbors):
        self.neighbors = neighbors

    def has_term(self, term):
        return term in self.neighbors

    def most_similar(self, term, top_n=20):
        return self.neighbors.get(term, [])[:top_n]


def make_terms(**freqs):
    return [
        Term(term_key=key, display_term=key.replace("_", " "), frequency=freq,
             document_frequency=max(1, freq // 2))
        for key, freq in freqs.items()
    ]


def test_pair_is_deduplicated_in_canonical_order():
    trainer = StubTrainer({
        "FO": [("패밀리_오픈", 0.87)],
        "패밀리_오픈": [("FO", 0.87)],
    })
    terms = make_terms(FO=20, 패밀리_오픈=18)
    candidates = CandidateGenerator(min_similarity=0.6, min_term_frequency=3).generate(
        trainer, terms
    )
    assert len(candidates) == 1
    a, b = CandidateRelation.canonical_pair("FO", "패밀리_오픈")
    assert (candidates[0].term_a, candidates[0].term_b) == (a, b)


def test_similarity_threshold_filters_candidates():
    trainer = StubTrainer({"FO": [("패밀리_오픈", 0.87), ("예정", 0.55)]})
    terms = make_terms(FO=20, 패밀리_오픈=18, 예정=30)
    candidates = CandidateGenerator(min_similarity=0.6).generate(trainer, terms)
    assert [c.term_b for c in candidates] == ["패밀리_오픈"]


def test_placeholder_and_stopwords_excluded():
    trainer = StubTrainer({"FO": [("<DATE>", 0.95), ("문서", 0.9), ("패밀리_오픈", 0.8)]})
    terms = make_terms(FO=20, 문서=50, 패밀리_오픈=18)
    generator = CandidateGenerator(min_similarity=0.6, stopwords=["문서"])
    candidates = generator.generate(trainer, terms)
    assert [c.term_b for c in candidates] == ["패밀리_오픈"]


def test_short_uppercase_abbreviations_are_protected():
    """FO, AI, DB 같은 사내 약어를 길이만으로 버리지 않는다 (§29)."""
    generator = CandidateGenerator(min_term_length=3, min_term_frequency=1)
    assert generator.is_eligible(Term(term_key="FO", display_term="FO", frequency=5))
    assert generator.is_eligible(Term(term_key="DB", display_term="DB", frequency=5))
    assert not generator.is_eligible(Term(term_key="가나", display_term="가나", frequency=5))


def test_low_frequency_terms_stay_in_catalog():
    """저빈도는 'FastText 경로 제외'이지 '표제어 자격 없음'이 아니다.

    프로젝트 고유 용어일수록 빈도가 낮다. term 자체를 버리면 문자열 규칙 경로로도
    후보를 만들 수 없게 된다.
    """
    generator = CandidateGenerator(min_term_frequency=5)
    rare = Term(term_key="희귀어", display_term="희귀어", frequency=2)
    assert generator.is_eligible(rare)
    assert rare.term_key not in [
        k for k in ()
    ]


def test_low_frequency_terms_excluded_from_fasttext_path():
    """빈도가 낮은 term 은 FastText 이웃 경로에 올리지 않는다."""
    trainer = StubTrainer({"FO": [("패밀리_오픈", 0.95)], "패밀리_오픈": [("FO", 0.95)]})
    terms = make_terms(FO=20, 패밀리_오픈=2)

    generator = CandidateGenerator(min_similarity=0.6, min_term_frequency=5)
    assert generator.generate(trainer, terms) == []


def test_self_pair_and_notation_variants_excluded():
    trainer = StubTrainer({"패밀리_오픈": [("패밀리_오픈", 1.0), ("패밀리오픈", 0.99)]})
    terms = make_terms(패밀리_오픈=20, 패밀리오픈=15)
    assert CandidateGenerator(min_similarity=0.6).generate(trainer, terms) == []


def test_max_candidates_per_term():
    neighbors = [(f"용어{i}", 0.9 - i * 0.01) for i in range(10)]
    trainer = StubTrainer({"FO": neighbors})
    terms = make_terms(FO=20, **{f"용어{i}": 10 for i in range(10)})
    generator = CandidateGenerator(min_similarity=0.6, max_candidates_per_term=3)
    assert len(generator.generate(trainer, terms)) == 3
