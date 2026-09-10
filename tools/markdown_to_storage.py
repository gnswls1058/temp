"""코퍼스 본문(경량 Markdown) → Confluence storage format 변환기.

지원: 제목(h2~h4), 불릿/번호 목록, 표, 코드 블록, 인용, 구분선, 문단,
     인라인 코드/굵게. Confluence storage format 은 XHTML 이므로 엄격히 닫는다.
"""
from __future__ import annotations

import html
import re
from typing import List

_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_BOLD_RE = re.compile(r"\*\*([^*\n]+)\*\*")
_TABLE_SEP_RE = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?$")

_LANG_ALIASES = {
    "json": "json", "sql": "sql", "http": "text", "bash": "bash", "sh": "bash",
    "java": "java", "kotlin": "kotlin", "yaml": "yaml", "text": "text",
    "js": "javascript", "ts": "typescript", "py": "python", "": "text",
}


def _inline(text: str) -> str:
    """인라인 마크업을 처리하며 XML 이스케이프한다."""
    parts: List[str] = []
    cursor = 0
    for match in _INLINE_CODE_RE.finditer(text):
        parts.append(_escape_bold(text[cursor:match.start()]))
        parts.append(f"<code>{html.escape(match.group(1))}</code>")
        cursor = match.end()
    parts.append(_escape_bold(text[cursor:]))
    return "".join(parts)


def _escape_bold(text: str) -> str:
    out: List[str] = []
    cursor = 0
    for match in _BOLD_RE.finditer(text):
        out.append(html.escape(text[cursor:match.start()]))
        out.append(f"<strong>{html.escape(match.group(1))}</strong>")
        cursor = match.end()
    out.append(html.escape(text[cursor:]))
    return "".join(out)


def _code_macro(language: str, code: str) -> str:
    lang = _LANG_ALIASES.get(language.strip().lower(), "text")
    body = code.rstrip("\n").replace("]]>", "]]&gt;")
    return (
        '<ac:structured-macro ac:name="code" ac:schema-version="1">'
        f'<ac:parameter ac:name="language">{lang}</ac:parameter>'
        f"<ac:plain-text-body><![CDATA[{body}]]></ac:plain-text-body>"
        "</ac:structured-macro>"
    )


def _split_row(line: str) -> List[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [cell.strip() for cell in line.split("|")]


def convert(markdown: str) -> str:
    lines = markdown.replace("\r\n", "\n").split("\n")
    out: List[str] = []
    index = 0
    total = len(lines)

    while index < total:
        line = lines[index]
        stripped = line.strip()

        if not stripped:
            index += 1
            continue

        # 코드 블록
        if stripped.startswith("```"):
            language = stripped[3:].strip()
            index += 1
            buffer: List[str] = []
            while index < total and not lines[index].strip().startswith("```"):
                buffer.append(lines[index])
                index += 1
            index += 1  # 닫는 fence
            out.append(_code_macro(language, "\n".join(buffer)))
            continue

        # 제목
        heading = re.match(r"^(#{1,4})\s+(.*)$", stripped)
        if heading:
            level = min(len(heading.group(1)) + 1, 6)  # '#' 은 h2 부터 사용
            out.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
            index += 1
            continue

        # 구분선
        if stripped in {"---", "***", "___"}:
            out.append("<hr />")
            index += 1
            continue

        # 표
        if stripped.startswith("|") and index + 1 < total and _TABLE_SEP_RE.match(lines[index + 1].strip()):
            header = _split_row(lines[index])
            index += 2
            rows: List[List[str]] = []
            while index < total and lines[index].strip().startswith("|"):
                rows.append(_split_row(lines[index]))
                index += 1
            cells = "".join(f"<th>{_inline(c)}</th>" for c in header)
            body = "".join(
                "<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in row) + "</tr>"
                for row in rows
            )
            out.append(f"<table><tbody><tr>{cells}</tr>{body}</tbody></table>")
            continue

        # 인용
        if stripped.startswith("> "):
            buffer = []
            while index < total and lines[index].strip().startswith("> "):
                buffer.append(lines[index].strip()[2:])
                index += 1
            out.append(f"<blockquote><p>{_inline(' '.join(buffer))}</p></blockquote>")
            continue

        # 목록 (중첩 1단계까지)
        bullet = re.match(r"^(\s*)([-*])\s+(.*)$", line)
        number = re.match(r"^(\s*)(\d+)\.\s+(.*)$", line)
        if bullet or number:
            ordered = number is not None
            tag = "ol" if ordered else "ul"
            items: List[str] = []
            while index < total:
                current = lines[index]
                match = (
                    re.match(r"^(\s*)(\d+)\.\s+(.*)$", current) if ordered
                    else re.match(r"^(\s*)([-*])\s+(.*)$", current)
                )
                if not match:
                    break
                indent = len(match.group(1))
                content = _inline(match.group(3))
                if indent >= 2 and items:
                    items[-1] = items[-1][:-len("</li>")] + f"<ul><li>{content}</li></ul></li>"
                else:
                    items.append(f"<li>{content}</li>")
                index += 1
            out.append(f"<{tag}>{''.join(items)}</{tag}>")
            continue

        # 문단 (다음 빈 줄/블록 시작 전까지)
        buffer = []
        while index < total:
            current = lines[index]
            if not current.strip():
                break
            if re.match(r"^\s*([-*]|\d+\.)\s+", current):
                break
            if current.strip().startswith(("```", "#", "|", "> ")):
                break
            buffer.append(current.strip())
            index += 1
        out.append(f"<p>{_inline(' '.join(buffer))}</p>")

    return "".join(out)
