"""Confluence 본문(HTML/storage format) -> Markdown 변환 (§8 확장).

내부망 Confluence 는 본문을 HTML 로 내려준다. 태그를 전부 지워 평문으로 만들면
표와 제목이 본문 문장과 뒤섞여, 문장다움(sentence_ratio) 판정이 흐려진다.
'감사 로그' 처럼 표 머리글에만 나오는 토큰과 실제 서술 문장을 구분하려면
구조를 남겨야 한다.

Markdown 으로 바꾸면
  - 제목은 ``## 제목``
  - 목록은 ``- 항목``
  - 표는 ``| 셀 | 셀 |``
로 남아 문장 분리기와 문장다움 판정이 둘을 구분할 수 있다.

표준 라이브러리 ``html.parser`` 만 사용한다(내부망 반입 시 의존성을 줄이기 위함).
"""
from __future__ import annotations

import logging
import re
from html.parser import HTMLParser
from typing import List, Optional

logger = logging.getLogger(__name__)

_HEADINGS = {"h1": "#", "h2": "##", "h3": "###", "h4": "####", "h5": "#####", "h6": "######"}
_DROP = {"script", "style", "ac:parameter", "ri:attachment", "ri:url", "ri:page"}
_CODE = {"code", "pre", "ac:plain-text-body"}
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")
_TRAILING_SPACE_RE = re.compile(r"[ \t]+\n")


class _MarkdownExtractor(HTMLParser):
    """HTML 을 Markdown 으로 옮긴다. 구조만 남기고 장식은 버린다."""

    def __init__(self, keep_code_blocks: bool = False):
        super().__init__(convert_charrefs=True)
        self.keep_code_blocks = keep_code_blocks
        self.parts: List[str] = []
        self._skip_depth = 0
        self._code_depth = 0
        self._heading: Optional[str] = None
        self._list_stack: List[str] = []
        self._in_cell = False
        self._cells: List[str] = []
        self._row_is_header = False
        self._header_written = False

    # --- 유틸 ---
    def _emit(self, text: str) -> None:
        self.parts.append(text)

    def _current_text(self) -> str:
        return "".join(self.parts)

    # --- 태그 ---
    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in _DROP:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag in _CODE:
            self._code_depth += 1
            if not self.keep_code_blocks:
                return
            self._emit("\n```\n")
            return
        if self._code_depth and not self.keep_code_blocks:
            return

        if tag in _HEADINGS:
            self._heading = _HEADINGS[tag]
            self._emit(f"\n\n{self._heading} ")
        elif tag in ("ul", "ol"):
            self._list_stack.append(tag)
            self._emit("\n")
        elif tag == "li":
            indent = "  " * max(0, len(self._list_stack) - 1)
            marker = "1." if (self._list_stack and self._list_stack[-1] == "ol") else "-"
            self._emit(f"\n{indent}{marker} ")
        elif tag in ("p", "div"):
            self._emit("\n\n")
        elif tag == "br":
            self._emit("\n")
        elif tag == "table":
            self._emit("\n\n")
            self._header_written = False
        elif tag == "tr":
            self._cells = []
            self._row_is_header = False
        elif tag in ("td", "th"):
            self._in_cell = True
            self._cells.append("")
            if tag == "th":
                self._row_is_header = True

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in _DROP:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        if tag in _CODE:
            self._code_depth = max(0, self._code_depth - 1)
            if self.keep_code_blocks:
                self._emit("\n```\n")
            return
        if self._code_depth and not self.keep_code_blocks:
            return

        if tag in _HEADINGS:
            self._heading = None
            self._emit("\n")
        elif tag in ("ul", "ol"):
            if self._list_stack:
                self._list_stack.pop()
            self._emit("\n")
        elif tag in ("td", "th"):
            self._in_cell = False
        elif tag == "tr":
            cells = [c.strip().replace("|", "\\|") or " " for c in self._cells]
            if cells:
                self._emit("\n| " + " | ".join(cells) + " |")
                if self._row_is_header and not self._header_written:
                    self._emit("\n| " + " | ".join("---" for _ in cells) + " |")
                    self._header_written = True
            self._cells = []
        elif tag in ("p", "div"):
            self._emit("\n")

    def handle_data(self, data):
        if self._skip_depth:
            return
        if self._code_depth and not self.keep_code_blocks:
            return
        text = data.replace("\r", "")
        if not text.strip():
            # 셀 안이 아니면 공백 하나로 줄인다.
            if not self._in_cell and self.parts and not self.parts[-1].endswith((" ", "\n")):
                self._emit(" ")
            return
        text = re.sub(r"[ \t]*\n[ \t]*", " ", text)
        if self._in_cell:
            self._cells[-1] += text
        else:
            self._emit(text)


def html_to_markdown(body: Optional[str], *, keep_code_blocks: bool = False) -> str:
    """HTML/storage format 을 Markdown 문자열로 바꾼다."""
    if not body:
        return ""
    parser = _MarkdownExtractor(keep_code_blocks=keep_code_blocks)
    try:
        parser.feed(body)
        parser.close()
    except Exception as exc:  # pragma: no cover - 깨진 HTML 방어
        logger.warning("HTML 파싱 실패, 원문을 그대로 사용합니다: %s", exc)
        return body

    text = parser._current_text()
    text = _TRAILING_SPACE_RE.sub("\n", text)
    text = _MULTI_NEWLINE_RE.sub("\n\n", text)
    lines = [line.rstrip() for line in text.split("\n")]
    return "\n".join(lines).strip()
