"""파이프라인 전역에서 사용하는 Enum 정의.

LLM 출력은 반드시 여기 정의된 값 중 하나로만 제한된다 (§36).
"""
from __future__ import annotations

from enum import Enum


class TermType(str, Enum):
    ENTITY = "ENTITY"
    CONCEPT = "CONCEPT"
    UNKNOWN = "UNKNOWN"


class EntityType(str, Enum):
    PROJECT = "PROJECT"
    SYSTEM = "SYSTEM"
    PRODUCT = "PRODUCT"
    SERVICE = "SERVICE"
    ORGANIZATION = "ORGANIZATION"
    PERSON = "PERSON"
    LOCATION = "LOCATION"
    EVENT = "EVENT"
    DOCUMENT = "DOCUMENT"
    DOMAIN_TERM = "DOMAIN_TERM"
    OTHER = "OTHER"


class RelationType(str, Enum):
    """관계 유형.

    BROADER/NARROWER 는 방향이 있는 포함 관계다. '전문 인덱스'는 '인덱스'의
    하위어이므로 두 방향의 검색 확장 안전성이 다르다. 이 구분이 없으면
    포함 관계가 전부 NEAR_SYNONYM 으로 뭉개진다.
    """

    SAME_ENTITY = "SAME_ENTITY"
    EXACT_ALIAS = "EXACT_ALIAS"      # 표기만 다른 완전 동일어. 양방향 치환 가능
    ALIAS = "ALIAS"
    ABBREVIATION = "ABBREVIATION"
    SYNONYM = "SYNONYM"
    NEAR_SYNONYM = "NEAR_SYNONYM"
    BROADER = "BROADER"              # term_a 가 term_b 의 상위어
    NARROWER = "NARROWER"            # term_a 가 term_b 의 하위어
    RELATED = "RELATED"
    ANTONYM = "ANTONYM"
    UNRELATED = "UNRELATED"
    UNKNOWN = "UNKNOWN"


class Confidence(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class RelationStatus(str, Enum):
    ACTIVE = "ACTIVE"
    REVIEW = "REVIEW"
    REJECTED = "REJECTED"


class CandidateSource(str, Enum):
    """후보를 만들어낸 경로. 여러 경로가 같은 쌍을 낼 수 있다 (§26 확장)."""

    FASTTEXT = "FASTTEXT"        # 임베딩 이웃. 빈도 하한이 걸린다
    CONTEXT_PROFILE = "CONTEXT_PROFILE"  # 주변 단어 분포(PPMI) 비교
    LEXICAL = "LEXICAL"          # 어순/띄어쓰기/약어 등 문자열 규칙
    CONTAINMENT = "CONTAINMENT"  # 한쪽이 다른 쪽을 구성 요소로 포함
    LLM_PROPOSED = "LLM_PROPOSED"        # LLM 이 용어 목록을 읽고 직접 제안
    USER_DICTIONARY = "USER_DICTIONARY"


class CandidateStatus(str, Enum):
    PENDING = "PENDING"
    VALIDATED = "VALIDATED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    SKIPPED = "SKIPPED"


class RunStatus(str, Enum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


# Pattern Normalizer / Phrase 에서 공통으로 쓰는 placeholder 토큰
PLACEHOLDER_TOKENS = frozenset(
    {"<DATE>", "<TIME>", "<NUMBER>", "<URL>", "<EMAIL>"}
)


def safe_enum(enum_cls, value, default):
    """LLM 응답 등 신뢰할 수 없는 입력을 Enum 으로 안전하게 변환한다."""
    if isinstance(value, enum_cls):
        return value
    if value is None:
        return default
    try:
        return enum_cls(str(value).strip().upper())
    except ValueError:
        return default
