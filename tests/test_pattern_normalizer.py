"""Pattern Normalizer 테스트 (§9, §10)."""
import pytest

from app.preprocessing.pattern_normalizer import PatternNormalizer

PROTECTED = [
    r"VDA\d+",
    r"K8s",
    r"P?[A-Z]{2,}\d+[A-Za-z]*",
    r"[A-Za-z]+\d+(?:\.\d+)*",
    r"\d+(?:\.\d+)+",
]
HINTS = ["예정", "오픈", "시작", "일정"]


@pytest.fixture
def normalizer():
    return PatternNormalizer(
        date_context_hints=HINTS,
        protected_patterns=PROTECTED,
    )


@pytest.mark.parametrize("text", [
    "FO 2026-09-01 예정",
    "FO 2026.09.01 예정",
    "FO 2026/09/01 예정",
    "FO 20260901 예정",
    "FO 2026년 9월 1일 예정",
])
def test_date_variants_become_single_token(normalizer, text):
    assert normalizer.normalize_text(text) == "FO <DATE> 예정"


def test_invalid_compact_number_is_not_a_date(normalizer):
    # 20261345 는 실제 존재하는 날짜가 아니다.
    assert "<DATE>" not in normalizer.normalize_text("주문번호 20261345 확인")


def test_out_of_range_year_is_not_a_date(normalizer):
    assert "<DATE>" not in normalizer.normalize_text("코드 18500101 참조")


def test_context_hint_requirement(monkeypatch):
    strict = PatternNormalizer(
        date_context_hints=HINTS,
        protected_patterns=PROTECTED,
        require_context_hint_for_yyyymmdd=True,
    )
    assert strict.normalize_text("패밀리 오픈 20260901 예정") == "패밀리 오픈 <DATE> 예정"
    assert "<DATE>" not in strict.normalize_text("식별자 20260901 값")


@pytest.mark.parametrize("text,expected", [
    ("회의는 14:30 시작", "회의는 <TIME> 시작"),
    ("회의는 09:00 시작", "회의는 <TIME> 시작"),
    ("회의는 18시 30분 시작", "회의는 <TIME> 시작"),
])
def test_time_normalization(normalizer, text, expected):
    assert normalizer.normalize_text(text) == expected


@pytest.mark.parametrize("term", ["VDA5050", "K8s", "Project2026", "SGAS2026"])
def test_protected_terms_are_untouched(normalizer, term):
    result = normalizer.normalize_text(f"{term} 관련 안내")
    assert term in result
    assert "<NUMBER>" not in result


def test_version_string_is_protected(normalizer):
    assert normalizer.normalize_text("버전 1.2.3 배포") == "버전 1.2.3 배포"


def test_plain_number_is_replaced(normalizer):
    assert normalizer.normalize_text("대상 매장 300개") == "대상 매장 <NUMBER>개"


def test_single_digit_kept(normalizer):
    assert normalizer.normalize_text("총 5개 매장") == "총 5개 매장"


def test_url_and_email(normalizer):
    text = "문의는 admin@corp.com 또는 https://wiki.corp.com/x 참고"
    result = normalizer.normalize_text(text)
    assert "<EMAIL>" in result and "<URL>" in result


def test_original_sentence_is_preserved(normalizer):
    original = "FO 2026-09-01 예정"
    results = normalizer.normalize([original])
    assert results[0].original_sentence == original
    assert results[0].normalized_sentence == "FO <DATE> 예정"
