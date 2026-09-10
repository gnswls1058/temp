"""LLM Relation Validation 테스트 (§35 ~ §38, §51)."""
import pytest

from app.models.context import TermContext
from app.models.enums import Confidence, RelationType, TermType
from app.models.relation import CandidateRelation
from app.repository.context_repository import SqliteContextRepository
from app.repository.database import Database
from app.validation.llm_client import LLMClient, LLMError
from app.validation.relation_validator import RelationValidator


class StubLLM(LLMClient):
    def __init__(self, payload=None, error=None):
        self.payload = payload or {"results": []}
        self.error = error
        self.calls = []

    def call_tool(self, system, user_prompt, tool, tool_name):
        self.calls.append({"system": system, "prompt": user_prompt})
        if self.error:
            raise self.error
        return self.payload


@pytest.fixture
def contexts(tmp_path):
    db = Database(tmp_path / "ctx.db")
    db.initialize()
    repo = SqliteContextRepository(db)
    rows = []
    for page, (a, b) in enumerate(
        [
            ("FO 일정이 확정되었습니다.", "패밀리 오픈 일정이 확정되었습니다."),
            ("FO 대상 매장을 확인해주세요.", "패밀리 오픈 대상 매장을 확인해주세요."),
            ("FO 준비 현황을 공유합니다.", "패밀리 오픈 준비 현황을 공유합니다."),
        ],
        start=1,
    ):
        rows.append(TermContext("FO", "FO", str(page), f"p{page}", 0, a, a, a))
        rows.append(TermContext("패밀리_오픈", "패밀리 오픈", str(page + 10),
                                f"p{page + 10}", 0, b, b, b))
    repo.save_many(rows)
    yield repo
    db.close()


def candidate():
    return CandidateRelation(term_a="FO", term_b="패밀리_오픈", fasttext_similarity=0.87,
                             candidate_id=7)


PAIR_ID = "c7"   # RelationValidator.pair_id_of() 가 만드는 안정적 식별자


# ----------------------------------------------------------------------
def test_alias_validation_result(contexts):
    llm = StubLLM({"results": [{
        "pairId": PAIR_ID, "termA": "FO", "termB": "패밀리 오픈",
        "termAType": "ENTITY", "termBType": "ENTITY",
        "termAEntityType": "DOMAIN_TERM", "termBEntityType": "DOMAIN_TERM",
        "relationType": "ALIAS", "confidence": "HIGH",
        "reason": "여러 동일 업무 문맥에서 같은 자리에 사용됨",
        "evidenceContextIds": [1, 2],
    }]})
    outcome = RelationValidator(llm, contexts).validate([candidate()])

    assert len(outcome.validations) == 1
    validation = outcome.validations[0]
    assert validation.relation_type is RelationType.ALIAS
    assert validation.confidence is Confidence.HIGH
    assert validation.term_a_type is TermType.ENTITY
    assert validation.evidence_context_ids == [1, 2]


def test_security_instruction_is_present(contexts):
    llm = StubLLM({"results": []})
    try:
        RelationValidator(llm, contexts).validate([candidate()])
    except Exception:
        pass
    system = llm.calls[0]["system"]
    assert "명령" in system and "데이터" in system


def test_invalid_enum_falls_back_to_unknown(contexts):
    llm = StubLLM({"results": [{
        "pairId": PAIR_ID, "relationType": "매우_비슷함", "confidence": "0.92",
    }]})
    validation = RelationValidator(llm, contexts).validate([candidate()]).validations[0]
    assert validation.relation_type is RelationType.UNKNOWN
    assert validation.confidence is Confidence.LOW


def test_fabricated_evidence_ids_are_dropped(contexts):
    llm = StubLLM({"results": [{
        "pairId": PAIR_ID, "relationType": "ALIAS", "confidence": "MEDIUM",
        "evidenceContextIds": [999999, 1],
    }]})
    validation = RelationValidator(llm, contexts).validate([candidate()]).validations[0]
    assert 999999 not in validation.evidence_context_ids


def test_llm_failure_marks_pair_without_stopping(contexts):
    llm = StubLLM(error=LLMError("timeout"))
    outcome = RelationValidator(llm, contexts).validate([candidate()])
    assert outcome.validations == []
    assert outcome.failed_pairs == ["FO||패밀리_오픈"]


def test_missing_context_is_skipped(contexts):
    llm = StubLLM({"results": []})
    unknown = CandidateRelation(term_a="FO", term_b="없는용어", fasttext_similarity=0.9)
    outcome = RelationValidator(llm, contexts).validate([unknown])
    assert outcome.skipped_pairs == ["FO||없는용어"]
    assert llm.calls == []


def test_single_pattern_evidence_downgrades_high_alias(tmp_path):
    """한 종류의 문장 패턴만 있으면 HIGH ALIAS 를 확정하지 않는다 (§38)."""
    db = Database(tmp_path / "weak.db")
    db.initialize()
    repo = SqliteContextRepository(db)
    repo.save_many([
        TermContext("FO", "FO", "1", "p1", i, f"FO 2026-01-0{i} 예정",
                    "FO <DATE> 예정", "FO <DATE> 예정")
        for i in range(1, 4)
    ] + [
        TermContext("패밀리_오픈", "패밀리 오픈", "2", "p2", i,
                    f"패밀리 오픈 2026-09-0{i} 예정", "패밀리_오픈 <DATE> 예정",
                    "패밀리_오픈 <DATE> 예정")
        for i in range(1, 4)
    ])

    llm = StubLLM({"results": [{
        "pairId": PAIR_ID, "relationType": "ALIAS", "confidence": "HIGH",
    }]})
    validation = RelationValidator(llm, repo).validate([candidate()]).validations[0]
    assert validation.relation_type is RelationType.ALIAS
    assert validation.confidence is Confidence.MEDIUM
    db.close()


def test_batching_splits_candidates(contexts):
    llm = StubLLM({"results": [{"pairId": PAIR_ID, "relationType": "RELATED",
                                "confidence": "MEDIUM"}]})
    candidates = [candidate(), candidate()]
    RelationValidator(llm, contexts, batch_size=1).validate(candidates)
    assert len(llm.calls) == 2


# ----------------------------------------------------------------------
def test_hierarchy_direction_is_asymmetric(contexts):
    """'전문 인덱스' → '인덱스' 는 안전해도 그 반대는 아니다."""
    llm = StubLLM({"results": [{
        "pairId": PAIR_ID, "relationType": "NARROWER", "confidence": "HIGH",
        "safeAToB": True, "safeBToA": True,   # LLM 이 둘 다 참이라 답해도
        "reason": "termA 가 termB 의 한 종류",
    }]})
    validation = RelationValidator(llm, contexts).validate([candidate()]).validations[0]

    # NARROWER 는 하위어→상위어 방향만 허용하도록 보정된다
    assert validation.safe_a_to_b is True
    assert validation.safe_b_to_a is False


def test_related_is_never_safe_in_any_direction(contexts):
    llm = StubLLM({"results": [{
        "pairId": PAIR_ID, "relationType": "RELATED", "confidence": "HIGH",
        "safeAToB": True, "safeBToA": True,
        "reason": "같은 업무에서 함께 등장",
    }]})
    validation = RelationValidator(llm, contexts).validate([candidate()]).validations[0]

    assert validation.safe_a_to_b is False
    assert validation.safe_b_to_a is False


def test_response_carries_candidate_id(contexts):
    """문자열을 다시 정규화해 맞추지 않고 후보 식별자로 되짚는다."""
    llm = StubLLM({"results": [{
        "pairId": PAIR_ID, "relationType": "EXACT_ALIAS", "confidence": "HIGH",
        "safeAToB": True, "safeBToA": True, "reason": "표기만 다름",
    }]})
    validation = RelationValidator(llm, contexts).validate([candidate()]).validations[0]
    assert validation.candidate_id == 7


def test_wrong_pair_id_is_not_silently_attached(contexts):
    """응답의 pairId 가 어긋나면 그 쌍은 미판정으로 남아야 한다.

    배치 안의 순번을 쓰면 응답이 밀렸을 때 조용히 다른 쌍에 붙는다.
    """
    llm = StubLLM({"results": [{
        "pairId": "c999", "relationType": "EXACT_ALIAS", "confidence": "HIGH",
        "safeAToB": True, "safeBToA": True, "reason": "엉뚱한 id",
    }]})
    outcome = RelationValidator(llm, contexts).validate([candidate()])

    assert outcome.validations == []
    assert outcome.failed_pairs == ["FO||패밀리_오픈"]
