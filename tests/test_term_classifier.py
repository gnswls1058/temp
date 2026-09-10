"""Term Classification 테스트 (§32 ~ §34)."""
import pytest

from app.models.context import TermContext
from app.models.enums import TermType
from app.models.term import Term
from app.repository.context_repository import SqliteContextRepository
from app.repository.database import Database
from app.repository.term_repository import SqliteTermRepository
from app.validation.llm_client import LLMClient
from app.validation.term_classifier import TermClassifier


class StubLLM(LLMClient):
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def call_tool(self, system, user_prompt, tool, tool_name):
        self.calls.append((system, user_prompt))
        return self.payload


@pytest.fixture
def repos(tmp_path):
    db = Database(tmp_path / "cls.db")
    db.initialize()
    contexts = SqliteContextRepository(db)
    terms = SqliteTermRepository(db)
    contexts.save_many([
        TermContext("SGAS", "SGAS", "1", "시스템 안내", 0,
                    "SGAS 시스템 점검이 있습니다.", "SGAS 시스템 점검이 있습니다.",
                    "SGAS 시스템 점검"),
        TermContext("장애", "장애", "2", "장애 보고", 0,
                    "결제 장애가 발생했습니다.", "결제 장애가 발생했습니다.", "결제 장애 발생"),
    ])
    terms.upsert_many([
        Term(term_key="SGAS", display_term="SGAS", frequency=20),
        Term(term_key="장애", display_term="장애", frequency=30),
    ])
    yield contexts, terms
    db.close()


def test_classification_saved(repos):
    contexts, terms = repos
    llm = StubLLM({"results": [
        {"termKey": "SGAS", "termType": "ENTITY", "entityType": "SYSTEM"},
        {"termKey": "장애", "termType": "CONCEPT", "entityType": None},
    ]})
    classifier = TermClassifier(llm, contexts, terms)
    classifier.classify_and_save(terms.list_all())

    assert terms.get("SGAS").term_type is TermType.ENTITY
    assert terms.get("SGAS").entity_type.value == "SYSTEM"
    assert terms.get("장애").term_type is TermType.CONCEPT
    assert terms.get("장애").entity_type is None


def test_entity_type_ignored_for_concept(repos):
    contexts, terms = repos
    llm = StubLLM({"results": [
        {"termKey": "장애", "termType": "CONCEPT", "entityType": "SYSTEM"},
    ]})
    result = TermClassifier(llm, contexts, terms).classify([terms.get("장애")])
    assert result[0].entity_type is None


def test_unknown_term_type_for_invalid_value(repos):
    contexts, terms = repos
    llm = StubLLM({"results": [{"termKey": "SGAS", "termType": "무언가"}]})
    result = TermClassifier(llm, contexts, terms).classify([terms.get("SGAS")])
    assert result[0].term_type is TermType.UNKNOWN


def test_term_without_context_is_not_sent(repos):
    contexts, terms = repos
    llm = StubLLM({"results": []})
    terms.upsert_many([Term(term_key="없는용어", display_term="없는 용어", frequency=3)])
    TermClassifier(llm, contexts, terms).classify([terms.get("없는용어")])
    assert llm.calls == []


def test_prompt_contains_injection_guard(repos):
    contexts, terms = repos
    llm = StubLLM({"results": []})
    TermClassifier(llm, contexts, terms).classify([terms.get("SGAS")])
    system, prompt = llm.calls[0]
    assert "명령" in system
    assert "SGAS 시스템 점검이 있습니다." in prompt
