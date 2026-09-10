"""Komoran 형태소 분석 테스트 (§12 ~ §14).

JVM/Komoran 을 사용할 수 없는 환경에서는 자동으로 skip 된다.
"""
import pytest

from app.models.context import Sentence
from app.preprocessing.komoran_processor import KomoranProcessor, KomoranUnavailableError


@pytest.fixture(scope="module")
def processor():
    try:
        return KomoranProcessor(
            keep_pos=["NNG", "NNP", "SL", "SH", "VV", "VA", "XR"],
            stopwords=["것", "수", "등"],
        )
    except KomoranUnavailableError as exc:
        pytest.skip(f"Komoran 사용 불가: {exc}")


def test_particles_are_removed(processor):
    tokens = processor.tokenize("외부에서 VPN을 이용하여 사내 시스템에 접속한다.")
    assert "VPN" in tokens
    assert "시스템" in tokens
    assert "접속" in tokens
    assert "에서" not in tokens and "을" not in tokens


def test_placeholder_survives_analysis(processor):
    tokens = processor.tokenize("패밀리 오픈 <DATE> 예정")
    assert "<DATE>" in tokens
    assert "DATE" not in tokens
    assert "패밀리" in tokens and "오픈" in tokens and "예정" in tokens


def test_stopwords_filtered(processor):
    assert "것" not in processor.tokenize("이 것 은 문서 입니다")


def test_process_fills_tokens(processor):
    sentence = Sentence(
        page_id="1", page_title="t", sentence_index=0,
        original_sentence="FO 2026-09-01 예정",
        normalized_sentence="FO <DATE> 예정",
    )
    result = processor.process([sentence])
    assert result and result[0].tokens
    assert "<DATE>" in result[0].tokens
    # 원본은 그대로 보존된다 (§5.1)
    assert result[0].original_sentence == "FO 2026-09-01 예정"


def test_fallback_analyzer_strips_particles():
    """Komoran 이 없을 때 쓰는 대체 분석기의 최소 동작."""
    from app.preprocessing.komoran_processor import _WhitespaceFallback

    pairs = _WhitespaceFallback().pos("패밀리 오픈은 VPN을 사용한다")
    tokens = [morph for morph, _ in pairs]
    assert "오픈" in tokens
    assert any(tag == "SL" for _, tag in pairs)
