"""설정 로딩 테스트 (§56)."""
import pytest
import yaml

from app.config.settings import load_settings


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_BASE_URL", "https://corp.atlassian.net/wiki")
    monkeypatch.delenv("TEST_MISSING", raising=False)
    data = {
        "confluence": {
            "base_url": "${TEST_BASE_URL}",
            "api_token": "${TEST_MISSING}",
            "email": "${TEST_MISSING:-fallback@corp.com}",
            "space_ids": ["1", "2"],
        },
        "fasttext": {"window": 7, "epochs": 20},
        "storage": {"db_path": str(tmp_path / "x.db")},
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def test_env_expansion(config_file):
    settings = load_settings(config_file, load_env=False)
    assert settings.confluence.base_url == "https://corp.atlassian.net/wiki"
    assert settings.confluence.api_token == ""
    assert settings.confluence.email == "fallback@corp.com"


def test_dot_access_and_get(config_file):
    settings = load_settings(config_file, load_env=False)
    assert settings.fasttext.window == 7
    assert settings.get("fasttext.epochs") == 20
    assert settings.get("fasttext.missing", 5) == 5
    assert settings.get("nothing.at.all") is None


def test_missing_key_raises(config_file):
    settings = load_settings(config_file, load_env=False)
    with pytest.raises(AttributeError):
        settings.fasttext.no_such_option


def test_lists_are_preserved(config_file):
    settings = load_settings(config_file, load_env=False)
    assert list(settings.confluence.space_ids) == ["1", "2"]


def test_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_settings(tmp_path / "none.yaml", load_env=False)


def test_repo_config_loads():
    """저장소에 포함된 config.yaml 이 항상 유효해야 한다.

    임계값(min_similarity 등)은 corpus 에 맞춰 조정되는 값이므로 특정 수치를
    고정하지 않고, 필수 키가 존재하고 범위가 타당한지만 확인한다.
    """
    settings = load_settings("config.yaml", load_env=False)

    assert settings.get("fasttext.vector_size", 0) > 0
    assert settings.get("fasttext.window", 0) > 0
    assert 0.0 < settings.get("candidate.min_similarity", -1) <= 1.0
    assert settings.get("candidate.top_n", 0) > 0
    assert settings.get("candidate.min_term_frequency", 0) >= 1
    assert "<DATE>" in list(settings.get("phrases.forbidden_tokens"))
    assert settings.get("llm_validation.context_per_term", 0) > 0

    # 관계 승인 정책의 필수 항목
    policy = settings.get("relation_policy", {})
    active = list(policy.get("active_relation_types", []))
    # 자동 승인은 동일성이 강한 관계로 좁혀야 한다. ALIAS/SYNONYM 처럼 판단이
    # 갈리는 유형이 여기 들어가면 검수 없이 사전에 실린다.
    assert "EXACT_ALIAS" in active
    assert "SYNONYM" not in active and "NEAR_SYNONYM" not in active
    assert policy.get("require_bidirectional_for_active") is True
    assert policy.get("enable_alias_transitivity") is False  # §47
