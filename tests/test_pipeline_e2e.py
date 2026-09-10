"""End-to-End 파이프라인 테스트 (§57 예제 재현).

Confluence 수집만 대역(로컬 캐시)으로 바꾸고, 나머지 단계
(Cleaner -> Normalizer -> Komoran -> Phrases -> FastText -> Candidate -> LLM -> Dictionary)
는 실제 구현을 그대로 실행한다. LLM 만 결정적 stub 으로 대체한다.
"""
import json

import pytest
import yaml

from app.config.settings import load_settings
from app.models.document import Document
from app.preprocessing.komoran_processor import KomoranProcessor, KomoranUnavailableError
from app.validation.llm_client import LLMClient

pytest.importorskip("gensim")


@pytest.fixture(scope="module", autouse=True)
def require_komoran():
    try:
        KomoranProcessor()
    except KomoranUnavailableError as exc:
        pytest.skip(f"Komoran 사용 불가: {exc}")


class ScriptedLLM(LLMClient):
    """FO ↔ 패밀리 오픈 을 ALIAS 로 판정하는 결정적 LLM 대역."""

    def __init__(self):
        self.prompts = []

    def call_tool(self, system, user_prompt, tool, tool_name):
        self.prompts.append(user_prompt)

        if tool_name == "report_term_types":
            results = []
            for line in user_prompt.splitlines():
                if line.startswith("termKey: "):
                    key = line.removeprefix("termKey: ").strip()
                    is_entity = key in {"FO", "패밀리_오픈"}
                    results.append({
                        "termKey": key,
                        "termType": "ENTITY" if is_entity else "CONCEPT",
                        "entityType": "DOMAIN_TERM" if is_entity else None,
                    })
            return {"results": results}

        results = []
        for line in user_prompt.splitlines():
            if line.startswith("[쌍 "):
                pair_id = line[3:-1].strip()
                results.append({
                    "pairId": pair_id,
                    "termAType": "ENTITY", "termBType": "ENTITY",
                    "termAEntityType": "DOMAIN_TERM", "termBEntityType": "DOMAIN_TERM",
                    "relationType": "EXACT_ALIAS", "confidence": "HIGH",
                    "safeAToB": True, "safeBToA": True,
                    "reason": "여러 독립 문맥에서 동일 위치로 사용됨",
                })
        return {"results": results}


TEMPLATES = [
    ("{t} 일정이 {d} 로 확정되었습니다.", "일정"),
    ("{t} 대상 매장을 확인해주세요.", "매장"),
    ("{t} 준비 현황을 공유합니다.", "준비"),
    ("{t} 개시일이 변경되었습니다.", "변경"),
    ("{t} 관련 담당자를 지정했습니다.", "담당"),
]


def build_documents():
    documents = []
    page_id = 1
    for index in range(8):
        for term in ("FO", "패밀리 오픈"):
            body = "<p>" + "</p><p>".join(
                template.format(t=term, d=f"2026-09-0{(index % 9) + 1}")
                for template, _ in TEMPLATES
            ) + "</p>"
            documents.append(
                Document(
                    page_id=str(page_id),
                    space_id="1",
                    title=f"{term} 안내 {index}",
                    body=body,
                    version=1,
                )
            )
            page_id += 1
    return documents


@pytest.fixture
def config_path(tmp_path):
    config = {
        "confluence": {"base_url": "https://example.invalid", "email": "", "api_token": ""},
        "storage": {
            "db_path": str(tmp_path / "e2e.db"),
            "model_dir": str(tmp_path / "models"),
            "output_dir": str(tmp_path / "output"),
        },
        "cleaning": {"min_text_length": 5},
        "normalization": {
            "date": True, "time": True, "number": True,
            "date_context_hints": ["예정", "오픈", "확정"],
            "protected_patterns": [r"VDA\d+", r"K8s"],
        },
        "sentence": {"min_length": 4, "max_length": 300},
        "komoran": {"user_dictionary_path": None,
                    "keep_pos": ["NNG", "NNP", "SL", "SH", "VV", "VA", "XR"]},
        # gensim 기본 스코어는 vocabulary 크기에 비례한다. 소형 테스트 corpus 기준값.
        "phrases": {"min_count": 2, "threshold": 0.6, "enable_trigram": False},
        "fasttext": {"vector_size": 32, "window": 5, "min_count": 1, "sg": 1,
                     "epochs": 30, "workers": 1, "seed": 7},
        # max_candidates_per_term 은 이제 '기준 term 방향'이 아니라 실제 degree 상한이다.
        # 이 합성 corpus 는 모든 문장이 같은 템플릿이라 동반 출현어가 몰리므로
        # 상한을 넉넉히 둬야 FO ↔ 패밀리_오픈 이 살아남는다.
        "candidate": {"top_n": 10, "min_similarity": -1.0, "min_term_frequency": 2,
                      "max_candidates_per_term": 12},
        "stopwords": ["것", "수", "등"],
        "llm_validation": {"context_per_term": 4, "batch_size": 5, "dry_run": False},
        "relation_policy": {},
        "logging": {"level": "WARNING", "file": str(tmp_path / "pipeline.log")},
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    return path


def test_end_to_end_builds_alias_relation(config_path, monkeypatch):
    from app.pipeline import term_dictionary_pipeline as pipeline_module

    llm = ScriptedLLM()
    monkeypatch.setattr(pipeline_module, "create_llm_client", lambda conf: llm)

    settings = load_settings(config_path, load_env=False)
    pipeline = pipeline_module.TermDictionaryPipeline(
        settings, pipeline_module.PipelineOptions(skip_collect=True)
    )
    try:
        for document in build_documents():
            pipeline.documents.upsert(document)

        run = pipeline.build_term_dictionary()

        assert run.status == "COMPLETED"
        assert run.document_count == 16
        assert run.sentence_count > 0
        assert run.vocabulary_size > 0
        assert run.candidate_count > 0

        # 원본 문서는 변경되지 않는다 (§5.1)
        assert "2026-09-01" in pipeline.documents.get("1").body

        # Context Store 에는 원본 문장과 정규화 문장이 함께 남는다 (§21)
        contexts = pipeline.contexts.find_by_term("FO", limit=5)
        assert contexts
        stored = pipeline.contexts.find_by_term("FO", limit=50, diversify_by_page=False)
        assert any("2026-09-0" in c.original_sentence for c in stored)
        assert any("<DATE>" in c.normalized_sentence for c in stored)

        # 대표 Context 는 서로 다른 문서/문장 패턴에서 선택된다 (§32)
        assert len({c.page_id for c in contexts}) > 1

        # Phrase 로 '패밀리 오픈' 이 하나의 term 이 된다 (§15)
        assert pipeline.terms.get("패밀리_오픈") is not None

        # FO ↔ 패밀리_오픈 후보가 만들어지고 ALIAS 로 확정된다
        pairs = {(c.term_a, c.term_b) for c in pipeline.relations.list_candidates()}
        assert ("FO", "패밀리_오픈") in pairs

        dictionary = json.loads(
            (settings.output_dir / "term_dictionary.json").read_text(encoding="utf-8")
        )
        entry = dictionary["FO"]
        assert entry["termType"] == "ENTITY"
        alias = next(r for r in entry["relations"] if r["termKey"] == "패밀리_오픈")
        assert alias["relationType"] == "EXACT_ALIAS"
        assert alias["status"] == "ACTIVE"
        assert alias["expandable"] is True

        # placeholder 는 사전에 들어가지 않는다 (§25)
        assert "<DATE>" not in dictionary
    finally:
        pipeline.close()


def test_dry_run_skips_llm(config_path):
    """dry_run 모드에서는 LLM 호출 없이 후보까지만 만들어진다 (§51)."""
    from app.pipeline import term_dictionary_pipeline as pipeline_module

    settings = load_settings(config_path, load_env=False)
    settings._data["llm_validation"]["dry_run"] = True

    pipeline = pipeline_module.TermDictionaryPipeline(
        settings, pipeline_module.PipelineOptions(skip_collect=True)
    )
    try:
        for document in build_documents():
            pipeline.documents.upsert(document)
        run = pipeline.build_term_dictionary()
        assert run.status == "COMPLETED"
        assert run.candidate_count > 0
        assert run.active_relation_count == 0
    finally:
        pipeline.close()
