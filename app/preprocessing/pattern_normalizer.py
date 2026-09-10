"""Pattern Normalizer (§9, §10).

날짜/시간/숫자/URL/이메일처럼 값만 다르고 의미는 같은 표현을 placeholder 로 바꾼다.
원본 문장은 절대 변경하지 않고 ``NormalizedSentence`` 로 두 형태를 함께 보관한다 (§5.1).

사내 고유명사(VDA5050, K8s, Project2026 …)는 protected pattern 으로 보호하여
``VDA<NUMBER>`` 같은 파괴적 치환이 일어나지 않게 한다.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date as _date
from typing import List, Sequence

logger = logging.getLogger(__name__)

DATE_TOKEN = "<DATE>"
TIME_TOKEN = "<TIME>"
NUMBER_TOKEN = "<NUMBER>"
URL_TOKEN = "<URL>"
EMAIL_TOKEN = "<EMAIL>"

# 보호 구간 sentinel — 숫자를 포함하지 않아야 NUMBER 규칙에 걸리지 않는다.
_SENTINEL_OPEN = ""
_SENTINEL_CLOSE = ""
_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

_URL_RE = re.compile(r"\bhttps?://[^\s<>\"']+|\bwww\.[^\s<>\"']+", re.IGNORECASE)
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")

_DATE_DELIM_RE = re.compile(r"\b(\d{4})[-./](\d{1,2})[-./](\d{1,2})\b")
_DATE_KOREAN_RE = re.compile(r"\b(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일?")
_DATE_KOREAN_YM_RE = re.compile(r"\b(\d{4})\s*년\s*(\d{1,2})\s*월(?!\s*\d)")
_DATE_MONTH_DAY_RE = re.compile(r"(?<![\d년])(\d{1,2})\s*월\s*(\d{1,2})\s*일")
_DATE_COMPACT_RE = re.compile(r"(?<![\dA-Za-z])(\d{8})(?![\dA-Za-z])")

_TIME_COLON_RE = re.compile(r"(?<![\d:])([01]?\d|2[0-3]):([0-5]\d)(?::([0-5]\d))?(?![\d:])")
_TIME_KOREAN_RE = re.compile(r"\b([01]?\d|2[0-3])\s*시(?:\s*([0-5]?\d)\s*분)?")


def _encode_index(index: int) -> str:
    """숫자를 쓰지 않는 sentinel 인덱스 인코딩."""
    if index == 0:
        return _ALPHABET[0]
    chars = []
    while index:
        index, rem = divmod(index, 26)
        chars.append(_ALPHABET[rem])
    return "".join(reversed(chars))


@dataclass
class NormalizedSentence:
    """원본과 정규화 결과를 함께 보관한다 (§9)."""

    original_sentence: str
    normalized_sentence: str


class PatternNormalizer:
    def __init__(
        self,
        *,
        date: bool = True,
        time: bool = True,
        number: bool = True,
        url: bool = True,
        email: bool = True,
        number_min_digits: int = 2,
        date_year_min: int = 1990,
        date_year_max: int = 2100,
        date_context_hints: Sequence[str] = (),
        require_context_hint_for_yyyymmdd: bool = False,
        protected_patterns: Sequence[str] = (),
    ):
        self.enable_date = date
        self.enable_time = time
        self.enable_number = number
        self.enable_url = url
        self.enable_email = email
        self.number_min_digits = max(1, number_min_digits)
        self.date_year_min = date_year_min
        self.date_year_max = date_year_max
        self.date_context_hints = tuple(date_context_hints)
        self.require_context_hint_for_yyyymmdd = require_context_hint_for_yyyymmdd

        self._protected_res = []
        for pattern in protected_patterns:
            try:
                self._protected_res.append(re.compile(pattern))
            except re.error as exc:
                logger.warning("잘못된 protected_pattern 무시: %s (%s)", pattern, exc)

        # 앞뒤에 영문/숫자가 붙은 값(고유명사 일부)은 치환하지 않는다.
        self._number_re = re.compile(
            rf"(?<![\dA-Za-z])\d{{{self.number_min_digits},}}(?:,\d{{3}})*(?:\.\d+)?(?![\dA-Za-z])"
        )

    # ------------------------------------------------------------------
    def normalize(self, sentences) -> List[NormalizedSentence]:
        """문장 리스트를 정규화한다. 입력이 문자열이면 단건 처리."""
        if isinstance(sentences, str):
            sentences = [sentences]
        return [
            NormalizedSentence(original_sentence=s, normalized_sentence=self.normalize_text(s))
            for s in sentences
        ]

    def normalize_text(self, text: str) -> str:
        if not text:
            return ""

        protected: List[str] = []
        working = self._protect(text, protected)

        if self.enable_url:
            working = _URL_RE.sub(URL_TOKEN, working)
        if self.enable_email:
            working = _EMAIL_RE.sub(EMAIL_TOKEN, working)
        if self.enable_date:
            working = self._normalize_dates(working)
        if self.enable_time:
            working = self._normalize_times(working)
        if self.enable_number:
            working = self._number_re.sub(NUMBER_TOKEN, working)

        return self._restore(working, protected)

    # ------------------------------------------------------------------
    def _protect(self, text: str, store: List[str]) -> str:
        """사내 고유명사 구간을 sentinel 로 치환한다 (§10 금지 예 방지)."""
        spans: List[tuple[int, int]] = []
        for regex in self._protected_res:
            for match in regex.finditer(text):
                token = match.group(0)
                if not token.strip():
                    continue
                # 버전 표기 보호 패턴이 '2026.09.01' 같은 날짜까지 잡지 않도록 한다.
                if self.enable_date and self._looks_like_date(token):
                    continue
                spans.append((match.start(), match.end()))
        if not spans:
            return text

        spans.sort()
        merged: List[tuple[int, int]] = []
        for start, end in spans:
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))

        out: List[str] = []
        cursor = 0
        for start, end in merged:
            out.append(text[cursor:start])
            token = f"{_SENTINEL_OPEN}{_encode_index(len(store))}{_SENTINEL_CLOSE}"
            store.append(text[start:end])
            out.append(token)
            cursor = end
        out.append(text[cursor:])
        return "".join(out)

    @staticmethod
    def _restore(text: str, store: List[str]) -> str:
        for index, original in enumerate(store):
            text = text.replace(
                f"{_SENTINEL_OPEN}{_encode_index(index)}{_SENTINEL_CLOSE}", original
            )
        return text

    # ------------------------------------------------------------------
    def _normalize_dates(self, text: str) -> str:
        def sub_ymd(match: re.Match) -> str:
            y, m, d = (int(match.group(i)) for i in (1, 2, 3))
            return DATE_TOKEN if self._is_valid_date(y, m, d) else match.group(0)

        text = _DATE_DELIM_RE.sub(sub_ymd, text)
        text = _DATE_KOREAN_RE.sub(sub_ymd, text)

        def sub_ym(match: re.Match) -> str:
            y, m = int(match.group(1)), int(match.group(2))
            return DATE_TOKEN if self._is_valid_date(y, m, 1) else match.group(0)

        text = _DATE_KOREAN_YM_RE.sub(sub_ym, text)

        def sub_md(match: re.Match) -> str:
            m, d = int(match.group(1)), int(match.group(2))
            if 1 <= m <= 12 and 1 <= d <= 31:
                return DATE_TOKEN
            return match.group(0)

        text = _DATE_MONTH_DAY_RE.sub(sub_md, text)
        return self._normalize_compact_dates(text)

    def _normalize_compact_dates(self, text: str) -> str:
        """YYYYMMDD 8자리는 정규식만으로 날짜라고 단정하지 않는다 (§10)."""

        def sub(match: re.Match) -> str:
            raw = match.group(1)
            year, month, day = int(raw[:4]), int(raw[4:6]), int(raw[6:])
            if not self._is_valid_date(year, month, day):
                return raw
            if self.require_context_hint_for_yyyymmdd and not self._has_date_hint(
                text, match.start(), match.end()
            ):
                return raw
            return DATE_TOKEN

        return _DATE_COMPACT_RE.sub(sub, text)

    def _looks_like_date(self, token: str) -> bool:
        match = _DATE_DELIM_RE.fullmatch(token)
        if not match:
            return False
        y, m, d = (int(match.group(i)) for i in (1, 2, 3))
        return self._is_valid_date(y, m, d)

    def _has_date_hint(self, text: str, start: int, end: int, window: int = 20) -> bool:
        around = text[max(0, start - window): min(len(text), end + window)]
        return any(hint in around for hint in self.date_context_hints)

    def _is_valid_date(self, year: int, month: int, day: int) -> bool:
        if not (self.date_year_min <= year <= self.date_year_max):
            return False
        try:
            _date(year, month, day)
        except ValueError:
            return False
        return True

    def _normalize_times(self, text: str) -> str:
        text = _TIME_COLON_RE.sub(TIME_TOKEN, text)

        def sub_korean(match: re.Match) -> str:
            hour = int(match.group(1))
            return TIME_TOKEN if 0 <= hour <= 23 else match.group(0)

        return _TIME_KOREAN_RE.sub(sub_korean, text)
