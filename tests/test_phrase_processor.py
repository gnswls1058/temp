"""Gensim Phrases 테스트 (§15 ~ §18)."""
import pytest

from app.models.context import Sentence
from app.preprocessing.phrase_processor import PhraseProcessor, display_term


def make_sentences(token_lists):
    return [
        Sentence(page_id="1", page_title="t", sentence_index=i,
                 original_sentence=" ".join(tokens), tokens=list(tokens))
        for i, tokens in enumerate(token_lists)
    ]


def test_multiword_expression_is_merged():
    # gensim 기본 스코어는 vocabulary 크기에 비례하므로 소형 corpus 에서는 threshold 를 낮춘다.
    corpus = [["패밀리", "오픈", "일정"]] * 5 + [["패밀리", "오픈", "매장"]] * 5
    processor = PhraseProcessor(min_count=2, threshold=0.3, enable_trigram=False)
    sentences = make_sentences(corpus)
    processor.train(sentences)
    processor.transform(sentences)

    assert any("패밀리_오픈" in s.phrase_tokens for s in sentences)


def test_placeholder_is_never_part_of_phrase():
    corpus = [["FO", "<DATE>", "예정"]] * 20
    processor = PhraseProcessor(min_count=2, threshold=0.05, enable_trigram=True)
    sentences = make_sentences(corpus)
    processor.train(sentences)
    processor.transform(sentences)

    for sentence in sentences:
        for token in sentence.phrase_tokens:
            assert "<DATE>" not in token or token == "<DATE>"
            assert not token.startswith("FO_<")
            assert not token.endswith(">_예정")
        assert "<DATE>" in sentence.phrase_tokens


def test_learned_phrases_exclude_placeholders():
    corpus = [["패밀리", "오픈", "<DATE>", "예정"]] * 10
    processor = PhraseProcessor(min_count=2, threshold=0.05, enable_trigram=False)
    processor.train(make_sentences(corpus))
    for phrase in processor.learned_phrases():
        assert "<DATE>" not in phrase


def test_display_term_conversion():
    assert display_term("패밀리_오픈") == "패밀리 오픈"
    assert display_term("생산_관리_시스템") == "생산 관리 시스템"
    assert display_term("FO") == "FO"
