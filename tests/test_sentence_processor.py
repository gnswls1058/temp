"""Sentence Processor 테스트 (§11)."""
from app.models.document import CleanedDocument
from app.preprocessing.sentence_processor import SentenceProcessor


def make_processor():
    return SentenceProcessor(abbreviation_guards=["예", "cf"], use_kiwi=False)


def test_split_into_sentences():
    doc = CleanedDocument(
        page_id="1",
        title="FO 일정 안내",
        clean_text=(
            "FO 일정이 확정되었습니다. FO 대상 매장을 확인해주세요.\n"
            "패밀리 오픈은 9월 1일 예정입니다."
        ),
    )
    sentences = make_processor().split([doc])
    texts = [s.original_sentence for s in sentences]

    assert "FO 일정 안내" in texts          # 제목도 Context 로 보존
    assert "FO 일정이 확정되었습니다." in texts
    assert "FO 대상 매장을 확인해주세요." in texts
    assert "패밀리 오픈은 9월 1일 예정입니다." in texts
    assert [s.sentence_index for s in sentences] == list(range(len(sentences)))


def test_numeric_dot_does_not_split():
    result = make_processor().split_text("오픈일은 2026.09.01 이며 변경될 수 있습니다.")
    assert len(result) == 1


def test_page_metadata_is_attached():
    doc = CleanedDocument(page_id="123", title="제목", clean_text="문장 하나입니다.")
    sentence = make_processor().split([doc])[0]
    assert sentence.page_id == "123"
    assert sentence.page_title == "제목"


def test_long_sentence_is_chunked():
    processor = SentenceProcessor(max_length=30, use_kiwi=False)
    long_text = " ".join(["단어"] * 40)
    chunks = processor.split_text(long_text)
    assert len(chunks) > 1
    assert all(len(c) <= 30 for c in chunks)
