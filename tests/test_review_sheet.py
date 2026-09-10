"""사람 검수 시트 도구 테스트."""
import csv
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.models.enums import EntityType, TermType  # noqa: E402
from app.models.term import Term  # noqa: E402
from tools import review_sheet  # noqa: E402


def make_term(key, display=None, frequency=10, term_type=TermType.UNKNOWN):
    return Term(
        term_key=key,
        display_term=display if display is not None else key.replace("_", " "),
        frequency=frequency,
        document_frequency=max(1, frequency // 2),
        term_type=term_type,
    )


# ----------------------------------------------------------------------
@pytest.mark.parametrize("key,expected", [
    ("product", "필드명 의심"),
    ("amount", "필드명 의심"),
    ("quantity", "필드명 의심"),
    ("NOT", "코드값/키워드 의심"),
    ("VARCHAR", "코드값/키워드 의심"),
    ("<DATE>", "placeholder"),
])
def test_noise_shapes_are_suggested(key, expected):
    assert review_sheet.suggest_term_removal(make_term(key), pattern_count=5) == expected


@pytest.mark.parametrize("key", [
    "Feature_Flag",   # 두 단어 복합어
    "DevOps",         # 대소문자 혼합
    "MySQL",
    "BlueGate",
    "Refresh_Token",
    "결제",
    "부분_취소",
])
def test_normal_terms_are_not_suggested(key):
    """정상 용어를 잡음으로 추천하면 검수자가 신호를 신뢰하지 못한다."""
    assert review_sheet.suggest_term_removal(make_term(key), pattern_count=5) == ""


def test_single_character_is_suggested():
    assert review_sheet.suggest_term_removal(make_term("수"), pattern_count=5) == "한 글자"


def test_repeated_boilerplate_is_suggested():
    """문맥 패턴이 하나뿐인데 자주 등장하면 소제목이나 표 머리글일 가능성이 높다."""
    term = make_term("주의사항", frequency=40)
    assert review_sheet.suggest_term_removal(term, pattern_count=1) == "반복 문구 의심"
    assert review_sheet.suggest_term_removal(term, pattern_count=4) == ""


def test_entity_terms_are_not_suggested_by_type():
    """LLM 분류를 못 받아 UNKNOWN 인 용어를 형식만으로 걸러내면 안 된다."""
    term = make_term("배송지_변경", term_type=TermType.UNKNOWN)
    assert review_sheet.suggest_term_removal(term, pattern_count=6) == ""


# ----------------------------------------------------------------------
@pytest.mark.parametrize("mark,removed", [
    ("X", True), ("x", True), ("O", True), ("제거", True), ("1", True),
    ("", False), (" ", False), ("보류", False),
])
def test_removal_marks(tmp_path, mark, removed):
    path = tmp_path / "terms.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=["제거", "termKey"])
        writer.writeheader()
        writer.writerow({"제거": mark, "termKey": "product"})

    marked = review_sheet._read_marked(path, ["termKey"])
    assert bool(marked) is removed


def test_append_lines_is_idempotent(tmp_path):
    path = tmp_path / "removed_terms.txt"

    added = review_sheet._append_lines(path, ["product", "item"], "테스트")
    assert added == 2

    # 같은 값을 다시 넣어도 중복 기록하지 않는다
    added = review_sheet._append_lines(path, ["product", "type"], "테스트")
    assert added == 1

    lines = [
        line for line in path.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]
    assert lines == ["product", "item", "type"]


def test_append_lines_ignores_comments(tmp_path):
    path = tmp_path / "removed_terms.txt"
    path.write_text("# 주석\nproduct\n", encoding="utf-8")

    added = review_sheet._append_lines(path, ["product", "member"], "테스트")
    assert added == 1


# ----------------------------------------------------------------------
def test_settings_merge_removed_terms(tmp_path):
    """제거 목록 파일이 불용어에 합쳐져야 다음 실행에서 후보가 빠진다."""
    import yaml

    from app.config.settings import load_settings

    removed = tmp_path / "removed_terms.txt"
    removed.write_text("# 검수 제거\nproduct\nitem\n\n", encoding="utf-8")

    pairs = tmp_path / "removed_relations.txt"
    pairs.write_text("# 제거 쌍\nFO||패밀리_오픈\n", encoding="utf-8")

    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump({
        "stopwords": ["것", "수"],
        "stopwords_file": str(removed),
        "excluded_pairs_file": str(pairs),
    }, allow_unicode=True), encoding="utf-8")

    settings = load_settings(config, load_env=False)
    assert settings.stopwords() == ["것", "수", "product", "item"]
    assert settings.excluded_pairs() == ["FO||패밀리_오픈"]


def test_settings_without_removal_files(tmp_path):
    import yaml

    from app.config.settings import load_settings

    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump({"stopwords": ["것"]}, allow_unicode=True),
                      encoding="utf-8")

    settings = load_settings(config, load_env=False)
    assert settings.stopwords() == ["것"]
    assert settings.excluded_pairs() == []


def test_excluded_pairs_block_candidates():
    """제거한 쌍은 다음 후보 생성에서 다시 올라오지 않아야 한다."""
    from app.embedding.candidate_generator import CandidateGenerator

    class StubTrainer:
        def has_term(self, term):
            return True

        def most_similar(self, term, top_n=20):
            return [("패밀리_오픈", 0.95)] if term == "FO" else [("FO", 0.95)]

    terms = [make_term("FO", "FO", 20), make_term("패밀리_오픈", "패밀리 오픈", 18)]

    generator = CandidateGenerator(min_similarity=0.6, min_term_frequency=3)
    assert len(generator.generate(StubTrainer(), terms)) == 1

    blocked = CandidateGenerator(
        min_similarity=0.6, min_term_frequency=3,
        excluded_pairs=["FO||패밀리_오픈"],
    )
    assert blocked.generate(StubTrainer(), terms) == []


# ----------------------------------------------------------------------
def test_verb_stems_are_not_candidates():
    """'돌려주', '묶이' 같은 용언 어간은 문맥에는 쓰지만 표제어가 될 수 없다."""
    from app.embedding.candidate_generator import CandidateGenerator

    generator = CandidateGenerator(min_term_frequency=3, verb_stems=["돌려주", "묶이"])

    assert generator.is_eligible(make_term("돌려주")) is False
    assert generator.is_eligible(make_term("문제_묶이")) is False   # 복합어 핵이 용언
    assert generator.is_eligible(make_term("지연_로딩")) is True    # 명사로 끝나면 유지
    assert generator.is_eligible(make_term("배포")) is True         # 명사로도 쓰이면 유지


def test_broken_compound_tokens_are_rejected():
    """조사나 잘린 어절이 붙은 복합어는 형태소 분석 오류다."""
    from app.embedding.candidate_generator import CandidateGenerator

    generator = CandidateGenerator(min_term_frequency=3)

    assert generator.is_eligible(make_term("을_반환")) is False
    assert generator.is_eligible(make_term("버_수정")) is False
    assert generator.is_eligible(make_term("공통_컴포넌트")) is True
    assert generator.is_eligible(make_term("A_등급")) is True  # 영문 한 글자는 약어


def test_sentence_ratio_filters_table_only_tokens():
    """문장 근거가 없는 표/코드 토큰만 걸러야 한다."""
    from app.embedding.candidate_generator import CandidateGenerator

    generator = CandidateGenerator(min_term_frequency=3, min_sentence_ratio=0.05)

    table_only = make_term("VARCHAR", frequency=30)
    table_only.sentence_ratio = 0.0
    assert generator.is_eligible(table_only) is False

    heading_term = make_term("API_명세", frequency=30)
    heading_term.sentence_ratio = 0.09  # 주로 소제목이지만 문장 근거도 있다
    assert generator.is_eligible(heading_term) is True


def test_measure_expressions_are_rejected():
    """'3월', '20건' 같은 수량·시점 표현은 표제어가 아니다."""
    from app.embedding.candidate_generator import CandidateGenerator

    generator = CandidateGenerator(min_term_frequency=3)

    for key in ("3월", "2024년", "20건", "500만원", "99%"):
        assert generator.is_eligible(make_term(key)) is False, key

    for key in ("3월_정산", "주문"):
        assert generator.is_eligible(make_term(key)) is True, key
