"""Term 모델 (§41)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.models.enums import EntityType, TermType


@dataclass
class Term:
    term_key: str          # FastText 내부 표현 (예: 패밀리_오픈)
    display_term: str      # 사용자/LLM 표시 표현 (예: 패밀리 오픈)
    frequency: int = 0
    document_frequency: int = 0
    # 이 용어가 등장한 문맥 중 '완결된 문장'의 비율.
    # 표 셀이나 코드 조각으로만 나오는 토큰은 0 에 가깝다.
    sentence_ratio: float = 0.0

    # --- 후보 생성 자격 (비파괴 보존) ---
    # term catalog 에서는 아무것도 지우지 않는다. 불용어도, 용언 어간도,
    # 저빈도어도 행으로 남기고 자격만 flag 로 관리한다. 그래야 정책을 바꿀 때
    # 수집~전처리 전체를 다시 돌리지 않아도 된다.
    is_stopword: bool = False
    # 후보 생성에서 제외된 이유. 빈 문자열이면 자격 있음.
    excluded_reason: str = ""

    @property
    def candidate_eligible(self) -> bool:
        return not self.excluded_reason
    term_type: TermType = TermType.UNKNOWN
    entity_type: Optional[EntityType] = None
    first_seen_at: Optional[str] = None
    last_seen_at: Optional[str] = None
    active: bool = True
    term_id: Optional[int] = None
