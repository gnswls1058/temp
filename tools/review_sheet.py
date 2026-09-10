"""사람 검수용 시트 내보내기 / 되돌리기.

후보를 넓게 모은 뒤 사람이 훑어보며 제거하는 운영을 지원한다. 제거는 두 층위다.

1. **용어 제거** — 잡음 용어를 통째로 뺀다. 그 용어가 낀 후보 쌍이 한꺼번에 사라져
   가장 효율이 좋다. 결과는 ``removed_terms.txt`` 에 쌓이고 다음 실행부터 불용어로 쓰인다.
2. **관계 제거** — 개별 쌍만 뺀다. 결과는 ``removed_relations.txt`` 에 쌓이고
   다음 실행에서 후보로 다시 올라오지 않는다.

사용::

    python tools/review_sheet.py export          # 두 시트 모두 생성
    python tools/review_sheet.py export --only terms
    #   -> data/review/*.csv 를 엑셀/스프레드시트에서 열어 '제거' 열에 X 표시
    python tools/review_sheet.py apply           # 표시한 내용을 반영

CSV 는 Excel 이 한글을 깨지 않도록 UTF-8 BOM 으로 저장한다.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path
from typing import Dict, List, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config.settings import configure_logging, load_settings  # noqa: E402
from app.dictionary.dictionary_builder import DictionaryBuilder, RelationPolicy  # noqa: E402
from app.models.enums import PLACEHOLDER_TOKENS, RelationStatus, TermType  # noqa: E402
from app.repository.context_repository import SqliteContextRepository  # noqa: E402
from app.repository.database import Database  # noqa: E402
from app.repository.relation_repository import SqliteRelationRepository  # noqa: E402
from app.repository.term_repository import SqliteTermRepository  # noqa: E402

REVIEW_DIR = Path("data/review")
TERMS_CSV = "terms.csv"
RELATIONS_CSV = "relations.csv"
REMOVED_TERMS = "removed_terms.txt"
REMOVED_RELATIONS = "removed_relations.txt"

REMOVE_MARKS = {"x", "X", "o", "O", "v", "V", "1", "y", "Y", "제거", "삭제", "true", "TRUE"}

# 영문 한 단어 토큰 판정용
_ASCII_WORD = re.compile(r"^[A-Za-z][A-Za-z0-9]*$")
_HANGUL = re.compile(r"[가-힣]")


def _is_removal_mark(value: str) -> bool:
    return value.strip() in REMOVE_MARKS


def suggest_term_removal(term, pattern_count: int) -> str:
    """제거 후보를 자동으로 짚어준다. 판단은 사람이 한다.

    LLM 분류를 받지 않은 용어까지 UNKNOWN 으로 몰리면 신호가 되지 않으므로
    용어 유형은 쓰지 않고 표기 형태와 문맥 다양성만 본다.

    영문 토큰은 표기로 구분한다.
      - 소문자 한 단어(product, amount) -> DB 컬럼명일 가능성이 높다
      - 대문자 한 단어(NOT, VARCHAR, BASIC) -> SQL 키워드나 열거형 값이 섞인다
      - 대소문자가 섞이거나 두 단어 이상(Feature Flag, DevOps, MySQL) -> 실제 용어일 가능성이 높아
        추천하지 않는다
    """
    key = term.term_key
    display = term.display_term

    if key in PLACEHOLDER_TOKENS:
        return "placeholder"
    if len(display.replace(" ", "")) <= 1:
        return "한 글자"

    if not _HANGUL.search(display) and " " not in display and _ASCII_WORD.match(display):
        if display.islower():
            return "필드명 의심"
        if display.isupper():
            return "코드값/키워드 의심"

    if pattern_count <= 1 and term.frequency >= 5:
        # 같은 문장 패턴만 반복 등장 = 소제목이나 표 머리글일 가능성이 높다
        return "반복 문구 의심"
    return ""


def _load_contexts(contexts_repo, term_key: str, limit: int = 2) -> str:
    rows = contexts_repo.find_by_term(term_key, limit=limit)
    return " / ".join(r.original_sentence.strip()[:70] for r in rows)


# ----------------------------------------------------------------------
def command_export(settings, only: str | None) -> int:
    review_dir = ROOT / REVIEW_DIR
    review_dir.mkdir(parents=True, exist_ok=True)

    db = Database(settings.db_path)
    db.initialize()
    terms_repo = SqliteTermRepository(db)
    relations_repo = SqliteRelationRepository(db)
    contexts_repo = SqliteContextRepository(db)

    candidates = relations_repo.list_candidates()
    validated = {
        (r.source_term_key, r.target_term_key): r
        for r in relations_repo.list_relations()
    }

    # 후보에 등장하는 용어만 검수 대상으로 삼는다.
    involved: Dict[str, int] = {}
    for candidate in candidates:
        involved[candidate.term_a] = involved.get(candidate.term_a, 0) + 1
        involved[candidate.term_b] = involved.get(candidate.term_b, 0) + 1

    all_terms = {t.term_key: t for t in terms_repo.list_all()}
    already_removed_terms = set(settings.stopwords())
    already_removed_pairs = set(settings.excluded_pairs())

    # --- 용어 시트 ---
    if only in (None, "terms"):
        path = review_dir / TERMS_CSV
        rows = []
        for term_key, count in sorted(
            involved.items(), key=lambda kv: -all_terms[kv[0]].frequency
            if kv[0] in all_terms else 0
        ):
            term = all_terms.get(term_key)
            if term is None:
                continue
            patterns = contexts_repo.distinct_pattern_count(term.term_key)
            rows.append({
                "제거": "",
                "용어": term.display_term,
                "termKey": term.term_key,
                "유형": term.term_type.value,
                "세부유형": term.entity_type.value if term.entity_type else "",
                "빈도": term.frequency,
                "문서수": term.document_frequency,
                "문맥패턴수": patterns,
                "문장다움": f"{term.sentence_ratio:.2f}",
                "후보쌍수": count,
                "자동추천": suggest_term_removal(term, patterns),
                "예시문맥": _load_contexts(contexts_repo, term.term_key),
            })
        with path.open("w", encoding="utf-8-sig", newline="") as fp:
            writer = csv.DictWriter(fp, fieldnames=list(rows[0].keys()) if rows else ["제거"])
            writer.writeheader()
            writer.writerows(rows)
        flagged = sum(1 for r in rows if r["자동추천"])
        print(f"용어 시트: {path}  ({len(rows)}행, 자동추천 {flagged}행)")

    # --- 관계 시트 ---
    if only in (None, "relations"):
        path = review_dir / RELATIONS_CSV
        rows = []
        for candidate in sorted(
            candidates, key=lambda c: (-c.priority_score, c.term_a)
        ):
            relation = validated.get((candidate.term_a, candidate.term_b))
            term_a = all_terms.get(candidate.term_a)
            term_b = all_terms.get(candidate.term_b)
            rows.append({
                "제거": "",
                "기준용어": term_a.display_term if term_a else candidate.term_a,
                "상대용어": term_b.display_term if term_b else candidate.term_b,
                "유사도": f"{candidate.fasttext_similarity:.4f}",
                "경로": "+".join(candidate.sources),
                "순위A": candidate.rank_a_to_b or "",
                "순위B": candidate.rank_b_to_a or "",
                "우선순위": f"{candidate.priority_score:.4f}",
                "관계타입": relation.relation_type.value if relation else "",
                "확신도": relation.llm_confidence.value if relation else "",
                "상태": relation.status.value if relation else "미검증",
                "확장가능": "O" if relation and relation.safe_expansion else "",
                "자동추천": (
                    "LLM UNRELATED" if relation and relation.status is RelationStatus.REJECTED
                    else ""
                ),
                "근거": (relation.reason if relation else "")[:160],
                "termKeyA": candidate.term_a,
                "termKeyB": candidate.term_b,
            })
        with path.open("w", encoding="utf-8-sig", newline="") as fp:
            writer = csv.DictWriter(fp, fieldnames=list(rows[0].keys()) if rows else ["제거"])
            writer.writeheader()
            writer.writerows(rows)
        unvalidated = sum(1 for r in rows if r["상태"] == "미검증")
        print(f"관계 시트: {path}  ({len(rows)}행, 미검증 {unvalidated}행)")

    if already_removed_terms or already_removed_pairs:
        print(
            f"\n이미 제외 중: 용어 {len(already_removed_terms)}개 / "
            f"쌍 {len(already_removed_pairs)}개 (시트에는 나타나지 않습니다)"
        )
    print(
        "\n'제거' 열에 X 를 넣고 저장한 뒤 `python tools/review_sheet.py apply` 를 실행하세요.\n"
        "용어를 제거하면 그 용어가 낀 쌍이 함께 사라집니다."
    )
    db.close()
    return 0


# ----------------------------------------------------------------------
def _read_marked(path: Path, key_fields: Sequence[str]) -> List[tuple]:
    if not path.exists():
        return []
    marked = []
    with path.open("r", encoding="utf-8-sig", newline="") as fp:
        for row in csv.DictReader(fp):
            if _is_removal_mark(row.get("제거", "")):
                marked.append(tuple(row[f].strip() for f in key_fields))
    return marked


def _append_lines(path: Path, lines: Sequence[str], header: str) -> int:
    existing = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                existing.add(line)

    added = [line for line in lines if line not in existing]
    if not path.exists():
        path.write_text(f"# {header}\n", encoding="utf-8")
    if added:
        with path.open("a", encoding="utf-8") as fp:
            for line in added:
                fp.write(line + "\n")
    return len(added)


def command_apply(settings, dry_run: bool = False) -> int:
    review_dir = ROOT / REVIEW_DIR
    terms_path = review_dir / TERMS_CSV
    relations_path = review_dir / RELATIONS_CSV

    removed_terms = [key for (key,) in _read_marked(terms_path, ["termKey"]) if key]
    removed_pairs_raw = _read_marked(relations_path, ["termKeyA", "termKeyB"])

    if not removed_terms and not removed_pairs_raw:
        print("제거 표시된 행이 없습니다. '제거' 열에 X 를 넣었는지 확인하세요.")
        return 1

    if dry_run:
        pairs = {f"{min(a,b)}||{max(a,b)}" for a, b in removed_pairs_raw if a and b}
        removed_set = set(removed_terms)
        db = Database(settings.db_path)
        db.initialize()
        candidates = SqliteRelationRepository(db).list_candidates()
        by_term = sum(
            1 for c in candidates
            if c.term_a in removed_set or c.term_b in removed_set
        )
        by_pair = sum(1 for c in candidates if c.pair_key in pairs)
        remaining = sum(
            1 for c in candidates
            if c.term_a not in removed_set and c.term_b not in removed_set
            and c.pair_key not in pairs
        )
        print(f"[dry-run] 제거 표시 - 용어 {len(removed_set)}개, 쌍 {len(pairs)}개")
        print(f"[dry-run] 사라지는 후보: 용어 제거로 {by_term}쌍, 쌍 직접 제거로 {by_pair}쌍")
        print(f"[dry-run] 남는 후보: {remaining} / {len(candidates)}쌍")
        print("[dry-run] 실제로 반영하려면 --dry-run 없이 다시 실행하세요.")
        db.close()
        return 0

    review_dir.mkdir(parents=True, exist_ok=True)
    added_terms = _append_lines(
        review_dir / REMOVED_TERMS, removed_terms, "검수에서 제거한 용어"
    )
    pair_keys = [f"{a}||{b}" for a, b in removed_pairs_raw if a and b]
    added_pairs = _append_lines(
        review_dir / REMOVED_RELATIONS, pair_keys, "검수에서 제거한 용어 쌍"
    )
    print(f"제거 등록 - 용어 {added_terms}개, 쌍 {added_pairs}개")

    # --- 현재 사전에서도 즉시 반영 ---
    db = Database(settings.db_path)
    db.initialize()
    terms_repo = SqliteTermRepository(db)
    relations_repo = SqliteRelationRepository(db)

    removed_term_set = set(settings.stopwords())
    removed_pair_set = set(settings.excluded_pairs())

    dropped = 0
    for relation in relations_repo.list_relations():
        source, target = relation.source_term_key, relation.target_term_key
        pair_key = f"{min(source, target)}||{max(source, target)}"
        if (source in removed_term_set or target in removed_term_set
                or pair_key in removed_pair_set):
            relations_repo.delete_relation(relation.relation_id)
            dropped += 1

    for term_key in removed_term_set:
        terms_repo.deactivate(term_key)

    builder = DictionaryBuilder(
        terms_repo, relations_repo,
        policy=RelationPolicy.from_settings(settings.get("relation_policy")),
        output_dir=settings.output_dir,
    )
    path = builder.rebuild()
    builder.export_review_queue()

    print(f"관계 레코드 {dropped}건 제거 후 사전 재생성: {path}")
    print(
        "\n다음 실행(`python -m app.main build`)부터는 제거한 용어와 쌍이 "
        "후보 생성 단계에서 아예 빠집니다."
    )
    db.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="사람 검수 시트 내보내기/반영")
    parser.add_argument("-c", "--config", default="config.yaml")
    parser.add_argument("command", choices=["export", "apply"])
    parser.add_argument("--only", choices=["terms", "relations"], default=None)
    parser.add_argument("--dry-run", action="store_true",
                        help="apply 시 반영하지 않고 영향 범위만 계산")
    args = parser.parse_args()

    settings = load_settings(args.config, load_env=False)
    settings.ensure_dirs()
    configure_logging(settings)

    if args.command == "export":
        return command_export(settings, args.only)
    return command_apply(settings, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
