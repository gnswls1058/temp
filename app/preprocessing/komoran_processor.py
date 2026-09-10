"""KoNLPy / Komoran 형태소 분석 (§12 ~ §14).

- ``pos()`` 결과를 품사 기준으로 필터링한다. 단순 ``nouns()`` 를 쓰지 않는다.
  (예정/실패/변경 같은 업무 문맥 동사·형용사가 사라지기 때문)
- ``<DATE>`` 등 placeholder 는 형태소 분석기에 넣지 않고 원형 그대로 보존한다.
- 사용자 사전은 선택 사항이다. 없어도 파이프라인이 동작해야 한다.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

from app.models.context import Sentence
from app.models.enums import PLACEHOLDER_TOKENS

logger = logging.getLogger(__name__)

_PLACEHOLDER_SPLIT_RE = re.compile(r"(<[A-Z]+>)")

DEFAULT_KEEP_POS = ("NNG", "NNP", "SL", "SH", "VV", "VA", "XR")


class KomoranUnavailableError(RuntimeError):
    """KoNLPy/Komoran 을 사용할 수 없을 때."""


class _WhitespaceFallback:
    """Komoran 이 없을 때만 쓰는 최소 대체 분석기 (테스트/스모크 용).

    운영에서는 사용하지 않는다. ``allow_fallback`` 을 켠 경우에만 활성화된다.
    """

    _PARTICLES = ("으로", "에서", "에게", "까지", "부터", "이나", "은", "는", "이", "가",
                  "을", "를", "의", "에", "도", "와", "과", "로", "만")

    def pos(self, text: str):
        result = []
        for token in text.split():
            token = token.strip(".,!?()[]{}\"'·")
            if not token:
                continue
            stripped = self._strip_particle(token)
            tag = "SL" if re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", stripped) else "NNG"
            result.append((stripped, tag))
        return result

    def _strip_particle(self, token: str) -> str:
        if not re.search(r"[가-힣]$", token):
            return token
        for particle in self._PARTICLES:
            if token.endswith(particle) and len(token) - len(particle) >= 2:
                return token[: -len(particle)]
        return token


class KomoranProcessor:
    def __init__(
        self,
        *,
        keep_pos: Sequence[str] = DEFAULT_KEEP_POS,
        stem_pos: Sequence[str] = ("VV", "VA"),
        user_dictionary_path: Optional[str] = None,
        min_token_length: int = 1,
        min_verb_stem_length: int = 2,
        skip_on_error: bool = True,
        stopwords: Sequence[str] = (),
        allow_fallback: bool = False,
        token_delimiter: str = "_",
    ):
        self.keep_pos = {p.upper() for p in keep_pos}
        self.stem_pos = {p.upper() for p in stem_pos}
        self.min_token_length = min_token_length
        self.min_verb_stem_length = min_verb_stem_length
        self.skip_on_error = skip_on_error
        self.stopwords = {w for w in stopwords}
        self.token_delimiter = token_delimiter
        # 토큰이 어떤 품사로 나왔는지 기록한다. 용언으로만 등장한 어간은
        # FastText 문맥에는 쓰되 용어 사전 후보에서는 빼기 위해 필요하다.
        self._verb_tokens: set[str] = set()
        self._noun_tokens: set[str] = set()
        self._analyzer = self._create_analyzer(user_dictionary_path, allow_fallback)

    # ------------------------------------------------------------------
    def _create_analyzer(self, user_dictionary_path: Optional[str], allow_fallback: bool):
        try:
            from konlpy.tag import Komoran
        except Exception as exc:  # konlpy 미설치 또는 JVM 문제
            if allow_fallback:
                logger.warning(
                    "Komoran 을 사용할 수 없어 fallback 분석기를 사용합니다 (품질 저하): %s", exc
                )
                return _WhitespaceFallback()
            raise KomoranUnavailableError(
                "KoNLPy/Komoran 을 사용할 수 없습니다. JDK 설치와 "
                "`pip install konlpy JPype1` 을 확인하세요."
            ) from exc

        userdic = self._resolve_user_dictionary(user_dictionary_path)
        try:
            analyzer = Komoran(userdic=userdic) if userdic else Komoran()
        except Exception as exc:
            if userdic:
                logger.warning("사용자 사전 로딩 실패, 기본 Komoran 으로 진행합니다: %s", exc)
                analyzer = Komoran()
            else:
                raise
        logger.info("Komoran 초기화 완료 (user_dictionary=%s)", userdic or "없음")
        return analyzer

    @staticmethod
    def _resolve_user_dictionary(path: Optional[str]) -> Optional[str]:
        """사용자 사전은 선택 사항 (§14). 없거나 비어 있으면 사용하지 않는다."""
        if not path:
            return None
        file_path = Path(path)
        if not file_path.exists():
            logger.info("사용자 사전이 없어 기본 사전으로 진행합니다: %s", file_path)
            return None
        if file_path.stat().st_size == 0:
            logger.info("사용자 사전이 비어 있어 사용하지 않습니다: %s", file_path)
            return None
        return str(file_path.resolve())

    # ------------------------------------------------------------------
    def process(self, sentences: Iterable[Sentence]) -> List[Sentence]:
        """각 문장의 ``tokens`` 를 채운다. 실패한 문장은 건너뛴다 (§50)."""
        processed: List[Sentence] = []
        failures = 0
        for sentence in sentences:
            source = sentence.normalized_sentence or sentence.original_sentence
            try:
                sentence.tokens = self.tokenize(source)
            except Exception as exc:
                failures += 1
                if not self.skip_on_error:
                    raise
                logger.debug("형태소 분석 실패 page=%s idx=%s: %s",
                             sentence.page_id, sentence.sentence_index, exc)
                continue
            if sentence.tokens:
                processed.append(sentence)
        if failures:
            logger.warning("형태소 분석 실패 문장 %s건은 제외되었습니다.", failures)
        logger.info("Komoran 처리 문장 수: %s", len(processed))
        return processed

    @property
    def verb_only_stems(self) -> set:
        """용언으로만 등장한 어간 집합.

        '돌려주', '묶이', '쌓이' 같은 어간은 문맥 신호로는 쓸모가 있지만
        용어 사전의 표제어가 될 수 없다. 명사로도 쓰인 토큰('배포', '검토')은
        여기 포함되지 않으므로 정상 용어를 잃지 않는다.
        """
        return self._verb_tokens - self._noun_tokens

    def tokenize(self, text: str) -> List[str]:
        """문장 하나를 FastText 학습용 token 열로 변환한다."""
        if not text:
            return []

        tokens: List[str] = []
        for segment in _PLACEHOLDER_SPLIT_RE.split(text):
            if not segment:
                continue
            if segment in PLACEHOLDER_TOKENS:
                tokens.append(segment)          # placeholder 는 그대로 보존
                continue
            tokens.extend(self._analyze_segment(segment))
        return tokens

    def _analyze_segment(self, segment: str) -> List[str]:
        segment = segment.strip()
        if not segment:
            return []

        tokens: List[str] = []
        for morph, tag in self._analyzer.pos(segment):
            tag = tag.upper()
            if tag not in self.keep_pos:
                continue
            token = morph.strip()
            if not token:
                continue
            if tag in self.stem_pos and len(token) < self.min_verb_stem_length:
                continue                        # '하', '되' 같은 기능성 어간 제거
            if len(token) < self.min_token_length:
                continue
            if token in self.stopwords:
                continue
            # 사용자 사전 항목은 '감사 로그'처럼 공백을 품은 토큰을 만든다.
            # 학습 corpus 는 공백으로 join 해서 저장하므로 그대로 두면 다시 읽을 때
            # 두 토큰으로 쪼개진다. 여기서 구분자로 통일한다.
            token = self.token_delimiter.join(token.split())
            if tag in self.stem_pos:
                self._verb_tokens.add(token)
            else:
                self._noun_tokens.add(token)
            tokens.append(token)
        return tokens
