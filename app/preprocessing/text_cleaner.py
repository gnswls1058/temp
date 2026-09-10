"""Text Cleaner (§8).

Confluence storage format(XHTML + ac:/ri: 매크로 마크업)에서 본문 텍스트만 추출한다.
제목/본문/표/목록 텍스트는 보존하고, 사내 약어·제품명(SGAS, FO, VDA5050 …)은
어떤 경우에도 손상시키지 않는다.
"""
from __future__ import annotations

import html
import logging
import re
from typing import Iterable, List, Optional, Sequence

from app.models.document import CleanedDocument, Document
from app.preprocessing.html_to_markdown import html_to_markdown

logger = logging.getLogger(__name__)

try:
    from bs4 import BeautifulSoup, NavigableString
    _HAS_BS4 = True
except ImportError:  # pragma: no cover
    BeautifulSoup = None  # type: ignore
    NavigableString = None  # type: ignore
    _HAS_BS4 = False

DEFAULT_DROP_TAGS = ("script", "style", "ac:parameter", "ri:attachment", "ri:url")
DEFAULT_BLOCK_TAGS = (
    "p", "div", "li", "tr", "br", "td", "th",
    "h1", "h2", "h3", "h4", "h5", "h6",
)

_TAG_RE = re.compile(r"<[^>]+>")
_MULTI_SPACE_RE = re.compile(r"[ \t ]+")
_MULTI_NEWLINE_RE = re.compile(r"\n{2,}")
# 의미 없는 장식 문자만 제거한다. 하이픈/점/슬래시는 용어의 일부일 수 있어 남긴다.
_DECORATION_RE = re.compile(r"[​﻿  ]|[▶◆■□○●▲△★☆※]+")


class TextCleaner:
    def __init__(
        self,
        drop_tags: Sequence[str] = DEFAULT_DROP_TAGS,
        block_tags: Sequence[str] = DEFAULT_BLOCK_TAGS,
        *,
        keep_code_blocks: bool = False,
        min_text_length: int = 10,
        output_format: str = "text",
    ):
        self.drop_tags = {t.lower() for t in drop_tags}
        self.block_tags = {t.lower() for t in block_tags}
        self.keep_code_blocks = keep_code_blocks
        self.min_text_length = min_text_length
        # "markdown" 이면 제목/목록/표 구조를 남긴다. 표 셀과 서술 문장을 구분해야
        # 문장다움(sentence_ratio) 판정이 제 역할을 한다.
        self.output_format = str(output_format).lower()
        if self.output_format not in ("text", "markdown"):
            raise ValueError(
                f"text_cleaner.output_format 은 text 또는 markdown 이어야 합니다: {output_format}"
            )

    # ------------------------------------------------------------------
    def clean(self, documents: Iterable[Document]) -> List[CleanedDocument]:
        cleaned: List[CleanedDocument] = []
        for doc in documents:
            text = self.clean_text(doc.body)
            if len(text) < self.min_text_length:
                logger.debug("본문이 너무 짧아 제외: page=%s", doc.page_id)
                continue
            doc.clean_text = text
            cleaned.append(
                CleanedDocument(page_id=doc.page_id, title=doc.title, clean_text=text)
            )
        return cleaned

    def clean_text(self, body: Optional[str]) -> str:
        if not body:
            return ""
        if self.output_format == "markdown":
            # 표준 라이브러리만 쓰므로 bs4 가 없어도 동작한다.
            return html_to_markdown(body, keep_code_blocks=self.keep_code_blocks)
        if _HAS_BS4 and "<" in body:
            text = self._clean_with_bs4(body)
        else:
            text = self._clean_with_regex(body)
        return self._normalize_whitespace(text)

    # ------------------------------------------------------------------
    def _clean_with_bs4(self, body: str) -> str:
        soup = BeautifulSoup(body, "html.parser")

        for tag_name in self.drop_tags:
            for tag in soup.find_all(tag_name):
                tag.decompose()

        # 코드 매크로는 기본적으로 NLP corpus 에서 제외한다.
        if not self.keep_code_blocks:
            for tag in soup.find_all(["ac:plain-text-body", "code", "pre"]):
                tag.decompose()
            for macro in soup.find_all("ac:structured-macro"):
                if (macro.get("ac:name") or "").lower() in {"code", "noformat", "html"}:
                    macro.decompose()

        # 블록 태그 뒤에 개행을 삽입하여 문장이 뭉치지 않게 한다.
        for tag in soup.find_all(list(self.block_tags)):
            tag.append(NavigableString("\n"))

        text = soup.get_text(separator=" ")
        return html.unescape(text)

    def _clean_with_regex(self, body: str) -> str:
        text = body
        for tag_name in self.drop_tags | {"ac:structured-macro"}:
            text = re.sub(
                rf"<{re.escape(tag_name)}\b.*?</{re.escape(tag_name)}>",
                " ",
                text,
                flags=re.DOTALL | re.IGNORECASE,
            )
        for tag_name in self.block_tags:
            text = re.sub(rf"</?{re.escape(tag_name)}\b[^>]*>", "\n", text, flags=re.IGNORECASE)
        text = _TAG_RE.sub(" ", text)
        return html.unescape(text)

    @staticmethod
    def _normalize_whitespace(text: str) -> str:
        text = _DECORATION_RE.sub(" ", text)
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = _MULTI_SPACE_RE.sub(" ", text)
        lines = [line.strip() for line in text.split("\n")]
        text = "\n".join(line for line in lines if line)
        return _MULTI_NEWLINE_RE.sub("\n", text).strip()
