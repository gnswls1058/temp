"""Relation 승인 정책 / Term Dictionary 테스트 (§39 ~ §47)."""
import json

import pytest

from app.dictionary.dictionary_builder import DictionaryBuilder, RelationPolicy
from app.models.enums import (
    Confidence, EntityType, RelationStatus, RelationType, TermType,
)
from app.models.relation import RelationValidation
from app.models.term import Term
from app.repository.database import Database
from app.repository.relation_repository import SqliteRelationRepository
from app.repository.term_repository import SqliteTermRepository


@pytest.fixture
def policy():
    return RelationPolicy()


# ----------------------------------------------------------------------
@pytest.mark.parametrize("relation_type", [
    RelationType.SAME_ENTITY, RelationType.EXACT_ALIAS, RelationType.ABBREVIATION,
])
def test_high_confidence_becomes_active(policy, relation_type):
    """자동 ACTIVE 는 동일성이 강한 관계로 좁힌다."""
    assert policy.decide_status(relation_type, Confidence.HIGH) is RelationStatus.ACTIVE


@pytest.mark.parametrize("relation_type", [
    RelationType.ALIAS, RelationType.SYNONYM, RelationType.NEAR_SYNONYM,
])
def test_soft_identity_types_need_human_review(policy, relation_type):
    """유의어 하나가 잘못 들어가면 검색 결과가 광범위하게 오염된다.

    ALIAS/SYNONYM/NEAR_SYNONYM 은 HIGH 여도 자동 승인하지 않고 REVIEW 로 보낸다.
    """
    assert policy.decide_status(relation_type, Confidence.HIGH) is RelationStatus.REVIEW


def test_one_way_relation_is_not_auto_active(policy):
    """한 방향으로만 치환 가능한 관계는 자동 승인하지 않는다."""
    assert policy.decide_status(
        RelationType.EXACT_ALIAS, Confidence.HIGH, bidirectional=False
    ) is RelationStatus.REVIEW


@pytest.mark.parametrize("relation_type", [RelationType.BROADER, RelationType.NARROWER])
def test_hierarchy_relations_are_review(policy, relation_type):
    """'인덱스' 와 '전문 인덱스' 는 유의어가 아니라 상하위 관계다."""
    assert policy.decide_status(relation_type, Confidence.HIGH) is RelationStatus.REVIEW


def test_medium_confidence_becomes_review(policy):
    assert policy.decide_status(RelationType.ABBREVIATION,
                                Confidence.MEDIUM) is RelationStatus.REVIEW


def test_low_confidence_is_rejected(policy):
    assert policy.decide_status(RelationType.ABBREVIATION,
                                Confidence.LOW) is RelationStatus.REJECTED


def test_related_is_stored_but_not_active(policy):
    """RELATED 는 삭제하지 않고 저장하되 기본 확장에는 쓰지 않는다 (§46)."""
    status = policy.decide_status(RelationType.RELATED, Confidence.HIGH)
    assert status is RelationStatus.REVIEW
    assert not policy.is_expandable(TermType.CONCEPT, RelationType.RELATED, status)


def test_antonym_and_unrelated_never_expand(policy):
    assert policy.decide_status(RelationType.UNRELATED, Confidence.HIGH) is RelationStatus.REJECTED
    assert policy.decide_status(RelationType.UNKNOWN, Confidence.HIGH) is RelationStatus.REJECTED
    antonym_status = policy.decide_status(RelationType.ANTONYM, Confidence.HIGH)
    assert not policy.is_expandable(TermType.CONCEPT, RelationType.ANTONYM, antonym_status)


def test_entity_expands_only_identity_relations(policy):
    """ENTITY 는 동일 엔티티 관계만 확장 허용 (§44)."""
    assert policy.is_expandable(TermType.ENTITY, RelationType.ALIAS, RelationStatus.ACTIVE)
    assert policy.is_expandable(TermType.ENTITY, RelationType.ABBREVIATION, RelationStatus.ACTIVE)
    assert not policy.is_expandable(TermType.ENTITY, RelationType.RELATED, RelationStatus.ACTIVE)
    assert not policy.is_expandable(TermType.ENTITY, RelationType.NEAR_SYNONYM, RelationStatus.ACTIVE)


def test_concept_expands_synonyms(policy):
    """CONCEPT 는 유의어 계열 확장 가능 (§45)."""
    assert policy.is_expandable(TermType.CONCEPT, RelationType.SYNONYM, RelationStatus.ACTIVE)
    assert policy.is_expandable(TermType.CONCEPT, RelationType.NEAR_SYNONYM, RelationStatus.ACTIVE)
    assert not policy.is_expandable(TermType.CONCEPT, RelationType.RELATED, RelationStatus.ACTIVE)


def test_unknown_term_type_never_expands(policy):
    assert not policy.is_expandable(TermType.UNKNOWN, RelationType.ALIAS, RelationStatus.ACTIVE)


def test_review_status_is_not_expandable(policy):
    assert not policy.is_expandable(TermType.ENTITY, RelationType.ALIAS, RelationStatus.REVIEW)


def test_alias_transitivity_is_disabled_by_default(policy):
    assert policy.enable_alias_transitivity is False


# ----------------------------------------------------------------------
@pytest.fixture
def builder(tmp_path):
    db = Database(tmp_path / "dict.db")
    db.initialize()
    terms = SqliteTermRepository(db)
    relations = SqliteRelationRepository(db)
    terms.upsert_many([
        Term(term_key="FO", display_term="FO", frequency=42, document_frequency=12),
        Term(term_key="패밀리_오픈", display_term="패밀리 오픈", frequency=30,
             document_frequency=9),
        Term(term_key="매장_오픈", display_term="매장 오픈", frequency=12,
             document_frequency=5),
    ])
    terms.update_classification("FO", "ENTITY", "DOMAIN_TERM")
    terms.update_classification("패밀리_오픈", "ENTITY", "DOMAIN_TERM")
    terms.update_classification("매장_오픈", "CONCEPT", None)
    yield DictionaryBuilder(terms, relations, output_dir=tmp_path), terms, relations
    db.close()


def test_apply_validations_saves_both_directions(builder):
    dictionary_builder, _, relations = builder
    stats = dictionary_builder.apply_validations([
        RelationValidation(
            term_a="FO", term_b="패밀리_오픈",
            relation_type=RelationType.EXACT_ALIAS, confidence=Confidence.HIGH,
            fasttext_similarity=0.87, reason="동일 업무 문맥",
            safe_a_to_b=True, safe_b_to_a=True,
        )
    ])
    assert stats.active == 1
    assert relations.relations_for_term("FO")
    assert relations.relations_for_term("패밀리_오픈")


def test_rebuild_writes_expected_dictionary(builder):
    dictionary_builder, _, _ = builder
    dictionary_builder.apply_validations([
        RelationValidation(
            term_a="FO", term_b="패밀리_오픈",
            relation_type=RelationType.EXACT_ALIAS, confidence=Confidence.HIGH,
            fasttext_similarity=0.87,
            safe_a_to_b=True, safe_b_to_a=True,
        ),
        RelationValidation(
            term_a="FO", term_b="매장_오픈",
            relation_type=RelationType.RELATED, confidence=Confidence.HIGH,
            fasttext_similarity=0.78,
        ),
    ])
    path = dictionary_builder.rebuild()
    data = json.loads(path.read_text(encoding="utf-8"))

    entry = data["FO"]
    assert entry["termType"] == "ENTITY"
    assert entry["entityType"] == "DOMAIN_TERM"
    assert entry["frequency"] == 42

    by_target = {r["term"]: r for r in entry["relations"]}
    assert by_target["패밀리 오픈"]["relationType"] == "EXACT_ALIAS"
    assert by_target["패밀리 오픈"]["status"] == "ACTIVE"
    assert by_target["패밀리 오픈"]["expandable"] is True
    # ENTITY 의 RELATED 는 저장되지만 확장 대상이 아니다.
    assert by_target["매장 오픈"]["status"] == "REVIEW"
    assert by_target["매장 오픈"]["expandable"] is False


def test_review_queue_export(builder):
    dictionary_builder, _, _ = builder
    dictionary_builder.apply_validations([
        RelationValidation(
            term_a="FO", term_b="매장_오픈",
            relation_type=RelationType.NEAR_SYNONYM, confidence=Confidence.MEDIUM,
            fasttext_similarity=0.7, reason="문맥 근거 부족",
        )
    ])
    rows = json.loads(dictionary_builder.export_review_queue().read_text(encoding="utf-8"))
    assert len(rows) == 1  # 대칭 저장이지만 큐에는 한 번만 나온다
    assert rows[0]["llmConfidence"] == "MEDIUM"
