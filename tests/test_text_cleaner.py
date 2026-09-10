"""Text Cleaner 테스트 (§8)."""
from app.models.document import Document
from app.preprocessing.text_cleaner import TextCleaner

BODY = """
<h1>FO 일정 안내</h1>
<p>SGAS 시스템과 <strong>FortiClient</strong> 설정이 필요합니다.</p>
<ul><li>VDA5050 규격 확인</li><li>K8s 배포 준비</li></ul>
<table><tr><td>MES</td><td>ERP</td></tr></table>
<script>alert('x')</script>
<style>.a{color:red}</style>
<ac:structured-macro ac:name="code"><ac:plain-text-body>print("hello")</ac:plain-text-body></ac:structured-macro>
"""


def test_markup_removed_and_terms_preserved():
    cleaner = TextCleaner()
    text = cleaner.clean_text(BODY)

    for term in ["SGAS", "FortiClient", "VDA5050", "K8s", "MES", "ERP", "FO 일정 안내"]:
        assert term in text, term

    assert "<h1>" not in text
    assert "alert" not in text
    assert "color:red" not in text
    assert 'print("hello")' not in text


def test_clean_documents_sets_clean_text():
    cleaner = TextCleaner()
    doc = Document(page_id="1", space_id="s", title="t", body=BODY, version=1)
    cleaned = cleaner.clean([doc])
    assert len(cleaned) == 1
    assert doc.clean_text == cleaned[0].clean_text
    assert doc.body == BODY  # 원본 보존 (§5.1)


def test_short_document_is_dropped():
    cleaner = TextCleaner(min_text_length=50)
    doc = Document(page_id="1", space_id="s", title="t", body="<p>짧음</p>", version=1)
    assert cleaner.clean([doc]) == []


def test_html_entities_are_unescaped():
    assert "A&B" in TextCleaner().clean_text("<p>A&amp;B</p>")
