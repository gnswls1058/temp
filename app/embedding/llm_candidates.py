"""LLM 기반 후보 쌍 제안 (§26 확장).

분포 통계는 두 표기가 서로 다른 문서에 갈려 있으면 잡지 못한다. NEXBRIDGE
corpus 에서 '회원 ↔ 고객'이 FastText 이웃 50위 밖인 것이 그 예다. 사람이
문서를 읽으면 즉시 아는 관계라 LLM 에게 직접 물어보는 편이 확실하고, 비용도
후보 5,887쌍을 전부 검증하는 것보다 훨씬 싸다.

여기서 만드는 것은 어디까지나 '후보'다. 관계 유형과 확신도, 방향별 검색 확장
안전성은 이후 RelationValidator 가 실제 문맥을 다시 보고 판정한다.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Sequence, Tuple

from app.models.relation import CandidateRelation
from app.models.term import Term
from app.repository.base import ContextRepository
from app.validation import prompts
from app.validation.llm_client import LLMClient, LLMError

logger = logging.getLogger(__name__)

_HINT_SCORE = {"SAME": 1.0, "BROADER": 0.7, "NARROWER": 0.7}
_CONFIDENCE_SCORE = {"HIGH": 1.0, "MEDIUM": 0.7, "LOW": 0.4}


class LLMCandidateProposer:
    def __init__(
        self,
        llm_client: LLMClient,
        context_repository: ContextRepository,
        *,
        batch_size: int = 600,
        context_per_term: int = 0,
        max_terms: int = 2000,
        min_frequency: int = 2,
    ):
        self.llm = llm_client
        self.contexts = context_repository
        self.batch_size = max(1, batch_size)
        self.context_per_term = context_per_term
        self.max_terms = max_terms
        # 분포 경로와 달리 빈도 하한을 낮게 둔다. 고유 용어일수록 빈도가 낮고,
        # 그런 용어야말로 통계로 못 잡는 대상이다.
        self.min_frequency = min_frequency

    # ------------------------------------------------------------------
    def propose(self, terms: Sequence[Term]) -> Dict[Tuple[str, str], float]:
        """``{(term_a, term_b): score}`` 를 돌려준다."""
        targets = [
            t for t in terms
            if t.candidate_eligible and t.frequency >= self.min_frequency
        ]
        # 쌍은 같은 배치 안에서만 찾을 수 있다. 빈도순으로만 자르면 '회원'과 '고객'이
        # 다른 배치로 갈리므로, 배치를 크게 잡아 함께 보이게 하는 것이 전제다.
        targets.sort(key=lambda t: t.frequency, reverse=True)
        targets = targets[: self.max_terms]
        if not targets:
            return {}

        allowed = {t.term_key for t in targets}
        batches = [
            targets[i: i + self.batch_size]
            for i in range(0, len(targets), self.batch_size)
        ]
        logger.info("LLM 후보 제안 시작: term %s개 / 배치 %s개", len(targets), len(batches))

        found: Dict[Tuple[str, str], float] = {}
        for index, batch in enumerate(batches, start=1):
            try:
                for pair, score in self._propose_batch(batch, allowed).items():
                    found[pair] = max(found.get(pair, 0.0), score)
            except LLMError as exc:
                # 배치 하나가 실패해도 전체 작업은 계속한다 (§51).
                logger.error("배치 %s/%s 후보 제안 실패: %s", index, len(batches), exc)
            except Exception as exc:
                logger.exception("배치 %s/%s 처리 중 오류: %s", index, len(batches), exc)

        logger.info("LLM 후보 제안 완료: %s쌍", len(found))
        return found

    # ------------------------------------------------------------------
    def _propose_batch(self, batch: Sequence[Term],
                       allowed: set) -> Dict[Tuple[str, str], float]:
        entries = []
        for term in batch:
            contexts = self.contexts.find_by_term(
                term.term_key, limit=self.context_per_term
            ) if self.context_per_term else []
            entries.append({
                "term_key": term.term_key,
                "display": term.display_term,
                "frequency": term.frequency,
                "contexts": contexts,
            })

        response = self.llm.call_tool(
            prompts.PAIR_PROPOSAL_SYSTEM,
            prompts.build_pair_proposal_prompt(entries),
            prompts.PAIR_PROPOSAL_TOOL,
            prompts.PAIR_PROPOSAL_TOOL["name"],
        )

        found: Dict[Tuple[str, str], float] = {}
        for item in response.get("pairs") or []:
            a = str(item.get("termAKey", "")).strip()
            b = str(item.get("termBKey", "")).strip()
            # LLM 이 만들어낸 term 은 받지 않는다.
            if a not in allowed or b not in allowed or a == b:
                if a or b:
                    logger.debug("목록 밖 term 제안 무시: %s / %s", a, b)
                continue
            hint = _HINT_SCORE.get(str(item.get("relationHint", "")).upper(), 0.5)
            confidence = _CONFIDENCE_SCORE.get(
                str(item.get("confidence", "")).upper(), 0.4
            )
            pair = CandidateRelation.canonical_pair(a, b)
            found[pair] = max(found.get(pair, 0.0), round(hint * confidence, 4))
        return found


def score_proposal(hint: str, confidence: str) -> float:
    """제안의 힌트/확신도를 0~1 점수로 바꾼다."""
    return round(
        _HINT_SCORE.get(str(hint).upper(), 0.5)
        * _CONFIDENCE_SCORE.get(str(confidence).upper(), 0.4),
        4,
    )


def load_proposals(path, allowed: set) -> Dict[Tuple[str, str], float]:
    """파일로 받은 제안을 읽는다.

    API 키를 쓸 수 없는 환경에서 tools/llm_bridge.py 가 만들어 둔 결과를
    그대로 사용하기 위한 경로다. 목록 밖 term 은 받지 않는다.
    """
    import json
    from pathlib import Path

    path = Path(path)
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    found: Dict[Tuple[str, str], float] = {}
    ignored = 0
    for item in payload.get("pairs", payload if isinstance(payload, list) else []):
        a = str(item.get("termAKey", "")).strip()
        b = str(item.get("termBKey", "")).strip()
        if a not in allowed or b not in allowed or a == b:
            ignored += 1
            continue
        pair = CandidateRelation.canonical_pair(a, b)
        score = score_proposal(item.get("relationHint", ""), item.get("confidence", ""))
        found[pair] = max(found.get(pair, 0.0), score)
    logger.info("파일에서 읽은 LLM 제안 %s쌍 (목록 밖 %s쌍 무시): %s",
                len(found), ignored, path)
    return found
