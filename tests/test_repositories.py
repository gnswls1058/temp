"""저장소 계층 테스트 (§6, §22, §32, §43)."""
import pytest

from app.models.context import TermContext
from app.models.document import Document
from app.models.enums import (
    CandidateStatus, Confidence, RelationStatus, RelationType, TermType,
)
from app.models.relation import CandidateRelation, TermRelation
from app.models.term import Term
from app.repository.context_repository import SqliteContextRepository
from app.repository.database import Database
from app.repository.document_repository import SqliteDocumentRepository
from app.repository.relation_repository import SqliteRelationRepository
from app.repository.term_repository import SqliteTermRepository


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "test.db")
    database.initialize()
    yield database
    database.close()


def make_context(term, page, index, sentence):
    return TermContext(
        term_key=term, display_term=term.replace("_", " "),
        page_id=page, page_title=f"page {page}", sentence_index=index,
        original_sentence=sentence, normalized_sentence=sentence,
        processed_sentence=sentence,
    )


# ----------------------------------------------------------------------
def test_document_version_change_detection(db):
    repo = SqliteDocumentRepository(db)
    doc = Document(page_id="100", space_id="1", title="t", body="<p>a</p>", version=5)
    repo.upsert(doc)

    assert repo.get_version("100") == 5
    assert repo.get_version("999") is None

    doc.version = 6
    doc.body = "<p>b</p>"
    repo.upsert(doc)
    assert repo.get_version("100") == 6
    assert repo.get("100").body == "<p>b</p>"
    assert repo.count() == 1


def test_document_error_does_not_break_pipeline(db):
    repo = SqliteDocumentRepository(db)
    repo.log_error("run-1", "102", "clean", "parsing 실패")
    assert repo.count_errors("run-1") == 1
    assert repo.count_errors("run-2") == 0


# ----------------------------------------------------------------------
def test_context_dedup_per_sentence(db):
    repo = SqliteContextRepository(db)
    ctx = make_context("FO", "1", 0, "FO 일정 안내")
    repo.save_many([ctx, ctx])
    assert repo.count() == 1


def test_context_selection_prefers_distinct_pages_and_patterns(db):
    repo = SqliteContextRepository(db)
    # 같은 문서에서 사실상 같은 문장 패턴만 반복되는 경우 (§32 나쁜 예)
    repeated = [
        make_context("FO", "1", i, f"FO 2026-01-0{i} 예정입니다.") for i in range(1, 5)
    ]
    # 서로 다른 문서의 서로 다른 문맥 (§32 좋은 예)
    varied = [
        make_context("FO", "2", 0, "FO 대상 매장을 확인해주세요."),
        make_context("FO", "3", 0, "FO 준비 현황을 공유합니다."),
        make_context("FO", "4", 0, "FO 오픈일이 변경되었습니다."),
    ]
    repo.save_many(repeated + varied)

    selected = repo.find_by_term("FO", limit=4, diversify_by_page=True, max_per_page=2)
    pages = {c.page_id for c in selected}
    assert len(pages) >= 3
    assert len(selected) <= 4


def test_distinct_pattern_count(db):
    repo = SqliteContextRepository(db)
    repo.save_many([
        make_context("FO", "1", i, f"FO 2026-01-0{i} 예정") for i in range(1, 5)
    ])
    # 사실상 같은 패턴이므로 1개로 계산된다 (§38 근거 강도)
    assert repo.distinct_pattern_count("FO") == 1

    repo.save_many([make_context("FO", "2", 0, "FO 대상 매장 확인")])
    assert repo.distinct_pattern_count("FO") == 2


def test_find_for_pair_returns_both_sides(db):
    repo = SqliteContextRepository(db)
    repo.save_many([
        make_context("FO", "1", 0, "FO 일정 안내"),
        make_context("패밀리_오픈", "2", 0, "패밀리 오픈 일정 안내"),
    ])
    a, b = repo.find_for_pair("FO", "패밀리_오픈")
    assert a and b
    assert a[0].term_key == "FO"
    assert b[0].display_term == "패밀리 오픈"


# ----------------------------------------------------------------------
def test_candidate_pair_is_canonical(db):
    repo = SqliteRelationRepository(db)
    repo.save_candidates([
        CandidateRelation(term_a="FO", term_b="패밀리_오픈", fasttext_similarity=0.87),
        CandidateRelation(term_a="패밀리_오픈", term_b="FO", fasttext_similarity=0.85),
    ])
    candidates = repo.list_candidates()
    assert len(candidates) == 1
    assert candidates[0].fasttext_similarity == pytest.approx(0.87)


def test_candidate_status_update(db):
    repo = SqliteRelationRepository(db)
    repo.save_candidates([CandidateRelation("A", "B", 0.7)])
    repo.update_candidate_status("A||B", CandidateStatus.VALIDATION_FAILED.value)
    assert repo.list_candidates()[0].status is CandidateStatus.VALIDATION_FAILED


def test_relation_requires_registered_terms(db):
    repo = SqliteRelationRepository(db)
    relation = TermRelation(
        source_term_key="FO", target_term_key="패밀리_오픈",
        relation_type=RelationType.ALIAS, status=RelationStatus.ACTIVE,
    )
    assert repo.save_relation(relation) is None  # term 미등록


def test_relation_and_evidence_roundtrip(db):
    terms = SqliteTermRepository(db)
    contexts = SqliteContextRepository(db)
    relations = SqliteRelationRepository(db)

    contexts.save_many([make_context("FO", "1", 0, "FO 일정 안내")])
    context_id = contexts.find_by_term("FO")[0].context_id

    terms.upsert_many([
        Term(term_key="FO", display_term="FO", frequency=10, document_frequency=3),
        Term(term_key="패밀리_오픈", display_term="패밀리 오픈", frequency=8,
             document_frequency=2),
    ])
    relation_id = relations.save_relation(TermRelation(
        source_term_key="FO", target_term_key="패밀리_오픈",
        relation_type=RelationType.ALIAS, status=RelationStatus.ACTIVE,
        fasttext_similarity=0.87, llm_confidence=Confidence.HIGH,
        reason="동일 업무 문맥", evidence_context_ids=[context_id],
    ))
    assert relation_id is not None

    stored = relations.relations_for_term("FO", [RelationStatus.ACTIVE.value])
    assert len(stored) == 1
    assert stored[0].relation_type is RelationType.ALIAS
    assert stored[0].evidence_context_ids == [context_id]
    assert relations.count_by_status(RelationStatus.ACTIVE.value) == 1


def test_term_classification_update(db):
    repo = SqliteTermRepository(db)
    repo.upsert_many([Term(term_key="SGAS", display_term="SGAS", frequency=5)])
    repo.update_classification("SGAS", "ENTITY", "SYSTEM")

    term = repo.get("SGAS")
    assert term.term_type is TermType.ENTITY
    assert term.entity_type.value == "SYSTEM"


def test_retain_only_removes_stale_terms(tmp_path):
    """terms 는 upsert 라서 과거 실행의 토큰이 남는다. 매 실행마다 정리해야 한다."""
    from app.models.term import Term
    from app.repository.database import Database
    from app.repository.term_repository import SqliteTermRepository

    db = Database(tmp_path / "t.db")
    db.initialize()
    repo = SqliteTermRepository(db)
    repo.upsert_many([
        Term(term_key="결제", display_term="결제", frequency=10, document_frequency=5),
        Term(term_key="김_도윤", display_term="김 도윤", frequency=8, document_frequency=4),
    ])
    assert repo.count() == 2

    removed = repo.retain_only(["결제"])
    assert removed == 1
    assert repo.get("김_도윤") is None
    assert repo.get("결제") is not None

    # 빈 목록은 안전장치로 아무것도 지우지 않는다
    assert repo.retain_only([]) == 0
    assert repo.count() == 1
    db.close()
