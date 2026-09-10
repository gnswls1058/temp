"""HTML -> Markdown 변환 테스트 (내부망 Confluence 대응)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.preprocessing.html_to_markdown import html_to_markdown  # noqa: E402
from app.preprocessing.text_cleaner import TextCleaner  # noqa: E402


def test_headings_lists_tables_are_preserved():
    """표 셀과 서술 문장을 구분해야 문장다움 판정이 제 역할을 한다."""
    markdown = html_to_markdown(
        "<h2>배경</h2><p>현재는 별도로 관리합니다.</p>"
        "<ul><li>첫째</li><li>둘째</li></ul>"
        "<table><tr><th>제공자</th><th>동의</th></tr>"
        "<tr><td>카카오</td><td>선택</td></tr></table>"
    )
    assert "## 배경" in markdown
    assert "- 첫째" in markdown
    assert "| 제공자 | 동의 |" in markdown
    assert "| --- | --- |" in markdown
    assert "| 카카오 | 선택 |" in markdown


def test_code_macro_is_dropped_by_default():
    markdown = html_to_markdown(
        "<p>설명</p><ac:structured-macro ac:name='code'>"
        "<ac:plain-text-body>SELECT 1;</ac:plain-text-body></ac:structured-macro>"
    )
    assert "설명" in markdown
    assert "SELECT" not in markdown


def test_entities_and_inline_tags_do_not_break_terms():
    """사내 약어·제품명은 어떤 경우에도 손상되면 안 된다 (§8)."""
    markdown = html_to_markdown("<p><strong>FO</strong> 일정과 <em>VDA5050</em> 확인</p>")
    assert "FO" in markdown and "VDA5050" in markdown
    assert "&" not in markdown


def test_pipe_in_cell_is_escaped():
    markdown = html_to_markdown("<table><tr><td>a|b</td><td>c</td></tr></table>")
    assert r"a\|b" in markdown


def test_cleaner_markdown_mode_needs_no_bs4():
    cleaner = TextCleaner(output_format="markdown")
    text = cleaner.clean_text("<h3>제목</h3><p>본문입니다.</p>")
    assert text.startswith("### 제목")
    assert "본문입니다." in text


def test_cleaner_text_mode_still_flattens():
    cleaner = TextCleaner(output_format="text")
    text = cleaner.clean_text("<h3>제목</h3><p>본문입니다.</p>")
    assert "###" not in text
    assert "제목" in text and "본문입니다." in text
