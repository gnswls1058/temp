"""LLM 검증 단계를 파일로 중계하는 도구.

Anthropic API 키를 쓸 수 없는 환경(오프라인 검토, 수동 검증, 다른 경로의 LLM 사용)에서
STEP 12~13 을 수행하기 위한 것이다. 파이프라인의 프롬프트와 검증 로직을 그대로 사용하며,
LLM 호출 구간만 파일 입출력으로 대체한다.

절차::

    python tools/llm_bridge.py export     # 프롬프트 파일 생성
    # -> data/output/llm/prompts/*.txt 를 LLM 에 전달하고
    #    응답 JSON 을 data/output/llm/answers/ 에 같은 이름으로 저장
    python tools/llm_bridge.py apply      # 응답을 실제 검증 경로로 되돌려 넣고 사전 생성

`apply` 는 TermClassifier / RelationValidator 를 그대로 실행하므로 Enum 강제,
증거 context id 검증, 근거 부족 시 HIGH 하향(§38) 같은 방어 로직이 모두 적용된다.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config.settings import configure_logging, load_settings  # noqa: E402
from app.dictionary.dictionary_builder import DictionaryBuilder, RelationPolicy  # noqa: E402
from app.models.enums import CandidateStatus, RelationStatus  # noqa: E402
from app.repository.context_repository import SqliteContextRepository  # noqa: E402
from app.repository.database import Database  # noqa: E402
from app.repository.relation_repository import SqliteRelationRepository  # noqa: E402
from app.repository.run_repository import SqliteRunRepository  # noqa: E402
from app.repository.term_repository import SqliteTermRepository  # noqa: E402
from app.validation.llm_client import LLMClient  # noqa: E402
from app.embedding.llm_candidates import LLMCandidateProposer  # noqa: E402
from app.validation.relation_validator import RelationValidator  # noqa: E402
from app.validation.term_classifier import TermClassifier  # noqa: E402

PROMPT_DIR = "llm/prompts"
ANSWER_DIR = "llm/answers"

_PAIR_RE = re.compile(
    r"\[쌍 (?P<pair_id>[^\]]+)\]\s*\n"
    r"termA:.*?\(termKey: (?P<term_a>[^)]+)\)\s*\n"
    r"termB:.*?\(termKey: (?P<term_b>[^)]+)\)"
)
_TERM_KEY_RE = re.compile(r"^termKey: (?P<term_key>.+)$", re.MULTILINE)


# ----------------------------------------------------------------------
# LLM Client 대역
# ----------------------------------------------------------------------
class CapturingLLMClient(LLMClient):
    """프롬프트를 파일로 남기고 빈 결과를 반환한다 (export 용)."""

    def __init__(self, output_dir: Path, prefix: str):
        self.output_dir = output_dir
        self.prefix = prefix
        self.count = 0
        self.files: List[str] = []

    def call_tool(self, system: str, user_prompt: str, tool: Dict[str, Any],
                  tool_name: str) -> Dict[str, Any]:
        self.count += 1
        name = f"{self.prefix}_{self.count:03d}.txt"
        path = self.output_dir / name
        path.write_text(
            f"### SYSTEM\n{system}\n\n### USER\n{user_prompt}\n"
            f"\n### 응답 형식\n"
            f"tool `{tool_name}` 의 input JSON 을 answers/{path.stem}.json 에 저장한다.\n"
            f"{json.dumps(tool['input_schema'], ensure_ascii=False, indent=2)}\n",
            encoding="utf-8",
        )
        self.files.append(name)
        return {"results": []}


class ReplayLLMClient(LLMClient):
    """저장된 응답을 프롬프트 내용에 맞춰 되돌려준다 (apply 용).

    배치 구성이 달라져도 동작하도록 순번이 아니라 termKey / 용어 쌍으로 매칭한다.
    """

    def __init__(self, classifications: Dict[str, dict], relations: Dict[tuple, dict],
                 by_pair_id: Dict[str, dict] | None = None):
        self.classifications = classifications
        self.relations = relations
        # pairId(c<candidate_id>) 로 직접 되짚는 경로. 문자열 매칭보다 안전하다.
        self.by_pair_id = by_pair_id or {}
        self.missing_terms: List[str] = []
        self.missing_pairs: List[tuple] = []

    def call_tool(self, system: str, user_prompt: str, tool: Dict[str, Any],
                  tool_name: str) -> Dict[str, Any]:
        if tool_name == "report_term_types":
            return {"results": self._classification_results(user_prompt)}
        return {"results": self._relation_results(user_prompt)}

    def _classification_results(self, prompt: str) -> List[dict]:
        results = []
        for match in _TERM_KEY_RE.finditer(prompt):
            term_key = match.group("term_key").strip()
            answer = self.classifications.get(term_key)
            if answer is None:
                self.missing_terms.append(term_key)
                continue
            results.append({"termKey": term_key, **answer})
        return results

    def _relation_results(self, prompt: str) -> List[dict]:
        results = []
        for match in _PAIR_RE.finditer(prompt):
            pair_id = match.group("pair_id").strip()
            key = (match.group("term_a").strip(), match.group("term_b").strip())
            answer = self.by_pair_id.get(pair_id) or self.relations.get(key)
            if answer is None:
                self.missing_pairs.append(key)
                continue
            results.append({"pairId": pair_id, **answer})
        return results


# ----------------------------------------------------------------------
def _build_services(settings, db, replay: LLMClient | None = None,
                    capture_dir: Path | None = None):
    contexts = SqliteContextRepository(db)
    terms = SqliteTermRepository(db)
    llm_conf = settings.get("llm_validation", {})

    classify_client = replay or CapturingLLMClient(capture_dir, "classify")
    relation_client = replay or CapturingLLMClient(capture_dir, "relations")

    classifier = TermClassifier(
        classify_client, contexts, terms,
        context_per_term=int(llm_conf.get("context_per_term", 4)),
        batch_size=int(llm_conf.get("batch_size", 12)) * 2,
        diversify_by_page=bool(llm_conf.get("diversify_by_page", True)),
        max_contexts_per_page=int(llm_conf.get("max_contexts_per_page", 2)),
    )
    validator = RelationValidator(
        relation_client, contexts,
        context_per_term=int(llm_conf.get("context_per_term", 4)),
        batch_size=int(llm_conf.get("batch_size", 12)),
        diversify_by_page=bool(llm_conf.get("diversify_by_page", True)),
        max_contexts_per_page=int(llm_conf.get("max_contexts_per_page", 2)),
        delimiter=str(settings.get("phrases.delimiter", "_")),
    )
    return classifier, validator, classify_client, relation_client


def _target_terms(terms_repo, candidates):
    involved = {c.term_a for c in candidates} | {c.term_b for c in candidates}
    return [t for t in terms_repo.list_all() if t.term_key in involved]


# ----------------------------------------------------------------------
def command_export(settings, limit: int = 0) -> int:
    prompt_dir = Path(settings.output_dir) / PROMPT_DIR
    answer_dir = Path(settings.output_dir) / ANSWER_DIR
    for directory in (prompt_dir, answer_dir):
        directory.mkdir(parents=True, exist_ok=True)
    for stale in prompt_dir.glob("*.txt"):
        stale.unlink()

    db = Database(settings.db_path)
    db.initialize()
    terms_repo = SqliteTermRepository(db)
    relations_repo = SqliteRelationRepository(db)

    candidates = relations_repo.list_candidates()
    if not candidates:
        print("후보가 없습니다. 먼저 `python -m app.main build --skip-llm` 을 실행하세요.")
        return 1
    if limit and limit < len(candidates):
        # list_candidates 는 priority_score 내림차순이다. 상위부터 검증한다.
        print(f"후보 {len(candidates)}쌍 중 우선순위 상위 {limit}쌍만 내보냅니다.")
        candidates = candidates[:limit]

    targets = _target_terms(terms_repo, candidates)
    classifier, validator, classify_client, relation_client = _build_services(
        settings, db, capture_dir=prompt_dir
    )

    classifier.classify(targets)
    validator.validate(candidates)

    # 후보 제안 프롬프트 (분포 통계가 놓치는 쌍을 LLM 이 직접 찾는 경로)
    propose_conf = settings.get("llm_candidates", {})
    propose_client = CapturingLLMClient(prompt_dir, "propose")
    if bool(propose_conf.get("enabled", False)):
        LLMCandidateProposer(
            propose_client, SqliteContextRepository(db),
            batch_size=int(propose_conf.get("batch_size", 100)),
            context_per_term=int(propose_conf.get("context_per_term", 1)),
            max_terms=int(propose_conf.get("max_terms", 2000)),
            min_frequency=int(propose_conf.get("min_frequency", 2)),
        ).propose(terms_repo.list_all())

    manifest = {
        "candidateCount": len(candidates),
        "termCount": len(targets),
        "classifyPrompts": classify_client.files,
        "relationPrompts": relation_client.files,
        "proposalPrompts": propose_client.files,
    }
    (prompt_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"후보 {len(candidates)}쌍 / 분류 대상 term {len(targets)}개")
    print(f"분류 프롬프트 {len(classify_client.files)}개, "
          f"관계 프롬프트 {len(relation_client.files)}개, "
          f"제안 프롬프트 {len(propose_client.files)}개")
    print(f"저장 위치: {prompt_dir}")
    print(f"응답은 {answer_dir} 에 같은 이름(.json)으로 저장하세요.")
    db.close()
    return 0


def _load_answers(answer_dir: Path) -> tuple[Dict[str, dict], Dict[tuple, dict], Dict[str, dict]]:
    classifications: Dict[str, dict] = {}
    relations: Dict[tuple, dict] = {}
    by_pair_id: Dict[str, dict] = {}

    for path in sorted(answer_dir.glob("classify_*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for item in payload.get("results", []):
            key = str(item.get("termKey", "")).strip()
            if key:
                classifications[key] = {
                    "termType": item.get("termType"),
                    "entityType": item.get("entityType"),
                    "reason": item.get("reason", ""),
                }

    for path in sorted(answer_dir.glob("relations_*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for item in payload.get("results", []):
            body = {key: value for key, value in item.items()
                    if key not in {"termAKey", "termBKey", "pairId"}}
            pair_id = str(item.get("pairId", "")).strip()
            if pair_id:
                by_pair_id[pair_id] = body
            term_a = str(item.get("termAKey") or item.get("termA", "")).strip()
            term_b = str(item.get("termBKey") or item.get("termB", "")).strip()
            if term_a and term_b:
                relations[(term_a, term_b)] = body
    return classifications, relations, by_pair_id


def command_apply(settings) -> int:
    answer_dir = Path(settings.output_dir) / ANSWER_DIR
    classifications, relations, by_pair_id = _load_answers(answer_dir)
    if not classifications and not relations and not by_pair_id:
        print(f"응답 파일이 없습니다: {answer_dir}")
        return 1
    print(f"응답 로드 - 용어 분류 {len(classifications)}건, "
          f"관계 판정 {len(by_pair_id) or len(relations)}건")

    db = Database(settings.db_path)
    db.initialize()
    terms_repo = SqliteTermRepository(db)
    relations_repo = SqliteRelationRepository(db)

    candidates = relations_repo.list_candidates()
    if by_pair_id:
        # 응답이 있는 후보만 검증 대상으로 삼는다. 일부만 검증한 경우
        # 나머지를 '실패'로 표시하지 않기 위함이다.
        answered = [c for c in candidates
                    if RelationValidator.pair_id_of(c, 0) in by_pair_id]
        if answered:
            candidates = answered
            print(f"응답이 있는 후보 {len(candidates)}쌍만 검증합니다.")
    targets = _target_terms(terms_repo, candidates)

    replay = ReplayLLMClient(classifications, relations, by_pair_id)
    classifier, validator, _, _ = _build_services(settings, db, replay=replay)

    classified = classifier.classify_and_save(targets)
    print(f"용어 분류 저장: {len(classified)}건")

    outcome = validator.validate(candidates)
    print(
        f"관계 판정: {len(outcome.validations)}건 "
        f"(실패 {len(outcome.failed_pairs)}, 문맥부족 스킵 {len(outcome.skipped_pairs)})"
    )

    for pair_key in outcome.failed_pairs:
        relations_repo.update_candidate_status(
            pair_key, CandidateStatus.VALIDATION_FAILED.value
        )
    for pair_key in outcome.skipped_pairs:
        relations_repo.update_candidate_status(pair_key, CandidateStatus.SKIPPED.value)

    builder = DictionaryBuilder(
        terms_repo, relations_repo,
        policy=RelationPolicy.from_settings(settings.get("relation_policy")),
        output_dir=settings.output_dir,
    )
    stats = builder.apply_validations(outcome.validations)

    for validation in outcome.validations:
        relations_repo.update_candidate_status(
            f"{validation.term_a}||{validation.term_b}", CandidateStatus.VALIDATED.value
        )

    path = builder.rebuild()
    review_path = builder.export_review_queue()

    # 최근 indexing run 통계를 검증 결과로 갱신한다.
    runs = SqliteRunRepository(db)
    recent = runs.list_recent(1)
    if recent:
        run = recent[0]
        run.validated_count = len(outcome.validations)
        run.active_relation_count = relations_repo.count_by_status(RelationStatus.ACTIVE.value)
        run.review_relation_count = relations_repo.count_by_status(RelationStatus.REVIEW.value)
        run.rejected_relation_count = relations_repo.count_by_status(
            RelationStatus.REJECTED.value
        )
        run.term_count = terms_repo.count()
        runs.update(run)

    print(f"\nACTIVE {stats.active} / REVIEW {stats.review} / REJECTED {stats.rejected}")
    print(f"Term Dictionary: {path}")
    print(f"REVIEW 큐: {review_path}")

    if replay.missing_terms:
        print(f"\n[경고] 응답 없는 term {len(replay.missing_terms)}개: "
              f"{replay.missing_terms[:5]}")
    if replay.missing_pairs:
        print(f"[경고] 응답 없는 쌍 {len(replay.missing_pairs)}개: "
              f"{replay.missing_pairs[:5]}")

    db.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="LLM 검증 단계 파일 중계")
    parser.add_argument("-c", "--config", default="config.yaml")
    parser.add_argument("command", choices=["export", "apply"])
    parser.add_argument("--limit", type=int, default=0,
                        help="export 시 우선순위 상위 N쌍만 내보낸다 (0 = 전체)")
    args = parser.parse_args()

    settings = load_settings(args.config, load_env=False)
    settings.ensure_dirs()
    configure_logging(settings)

    if args.command == "export":
        return command_export(settings, args.limit)
    return command_apply(settings)


if __name__ == "__main__":
    raise SystemExit(main())
