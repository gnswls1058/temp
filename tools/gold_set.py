"""고정 Gold Set 도구.

"후보가 몇 개 나왔다"는 품질 지표가 아니다. 파라미터를 바꿀 때마다 같은 기준으로
비교할 수 있어야 튜닝이 가능하다.

사용::

    python tools/gold_set.py sample --size 250   # 층화 표본을 뽑아 라벨 시트 생성
    python tools/gold_set.py evaluate            # 라벨 대비 현재 설정 평가

라벨(정답) 값은 다음 네 가지만 쓴다.
    SYNONYM     - 양방향 치환 가능. 자동 사전에 들어가도 되는 관계
    DIRECTIONAL - 한 방향만 안전 (상위어/하위어)
    RELATED     - 관련은 있으나 치환 불가
    UNRELATED   - 무관
"""
from __future__ import annotations

import argparse
import csv
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config.settings import load_settings  # noqa: E402
from app.models.enums import RelationStatus  # noqa: E402
from app.repository.database import Database  # noqa: E402
from app.repository.relation_repository import SqliteRelationRepository  # noqa: E402
from app.repository.term_repository import SqliteTermRepository  # noqa: E402

GOLD_CSV = "gold_set.csv"
LABELS = ("SYNONYM", "DIRECTIONAL", "RELATED", "UNRELATED")
# 자동 사전에 실려도 되는 라벨
POSITIVE_LABELS = ("SYNONYM",)

COLUMNS = [
    "라벨", "기준용어", "상대용어", "층", "유사도", "순위A", "순위B",
    "경로", "우선순위", "빈도A", "빈도B", "termKeyA", "termKeyB",
]


def _stratum(candidate, term_a, term_b) -> str:
    """표본을 나누는 기준은 '어느 경로가 이 쌍을 만들었는가'다.

    지금 답해야 할 질문이 "각 후보 경로가 실제로 쓸모 있는가"이기 때문이다.
    유사도 구간으로 나누면 이 corpus 에서는 거의 한 칸에 몰려 의미가 없다.
    """
    sources = set(candidate.sources)
    if not sources:
        return "미분류"
    if len(sources) > 1:
        return "복수경로"
    only = next(iter(sources))
    return {
        "FASTTEXT": "FastText단독",
        "CONTEXT_PROFILE": "문맥프로파일단독",
        "CONTAINMENT": "포함관계단독",
        "LEXICAL": "문자열단독",
        "LLM_PROPOSED": "LLM제안단독",
    }.get(only, only)


def command_sample(settings, size: int, seed: int) -> int:
    db = Database(settings.db_path)
    db.initialize()
    candidates = SqliteRelationRepository(db).list_candidates()
    terms = {t.term_key: t for t in SqliteTermRepository(db).list_all()}
    if not candidates:
        print("후보가 없습니다. 먼저 파이프라인을 실행하세요.")
        return 1

    buckets: Dict[str, List] = defaultdict(list)
    for candidate in candidates:
        buckets[_stratum(candidate, terms.get(candidate.term_a),
                         terms.get(candidate.term_b))].append(candidate)

    rng = random.Random(seed)
    per_bucket = max(1, size // len(buckets))
    picked = []
    for name, items in sorted(buckets.items()):
        take = min(per_bucket, len(items))
        picked.extend(rng.sample(items, take))
    # 남은 자리는 전체에서 무작위로 채운다
    remaining = [c for c in candidates if c not in picked]
    rng.shuffle(remaining)
    picked.extend(remaining[: max(0, size - len(picked))])

    review_dir = Path(settings.get("storage.review_dir", "./data/review"))
    review_dir.mkdir(parents=True, exist_ok=True)
    path = review_dir / GOLD_CSV

    if path.exists():
        print(f"이미 존재합니다: {path}\n"
              f"gold set 은 고정이어야 비교가 가능합니다. 새로 만들려면 먼저 옮기세요.")
        return 1

    with path.open("w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=COLUMNS)
        writer.writeheader()
        for candidate in picked:
            term_a = terms.get(candidate.term_a)
            term_b = terms.get(candidate.term_b)
            writer.writerow({
                "라벨": "",
                "기준용어": term_a.display_term if term_a else candidate.term_a,
                "상대용어": term_b.display_term if term_b else candidate.term_b,
                "층": _stratum(candidate, term_a, term_b),
                "유사도": f"{candidate.fasttext_similarity:.4f}",
                "순위A": candidate.rank_a_to_b or "",
                "순위B": candidate.rank_b_to_a or "",
                "경로": "+".join(candidate.sources),
                "우선순위": f"{candidate.priority_score:.4f}",
                "빈도A": term_a.frequency if term_a else 0,
                "빈도B": term_b.frequency if term_b else 0,
                "termKeyA": candidate.term_a,
                "termKeyB": candidate.term_b,
            })

    print(f"Gold Set 생성: {path}  ({len(picked)}쌍)")
    print(f"층 분포: {dict(Counter(_stratum(c, terms.get(c.term_a), terms.get(c.term_b)) for c in picked))}")
    print(f"\n'라벨' 열에 {' / '.join(LABELS)} 중 하나를 적어 저장하세요.")
    print("이후 파라미터를 바꿀 때마다 `python tools/gold_set.py evaluate` 로 같은 기준에서 비교합니다.")
    db.close()
    return 0


def _read_gold(path: Path) -> List[dict]:
    with path.open("r", encoding="utf-8-sig") as fp:
        rows = [r for r in csv.DictReader(fp) if (r.get("라벨") or "").strip()]
    for row in rows:
        row["라벨"] = row["라벨"].strip().upper()
    return rows


def command_evaluate(settings) -> int:
    review_dir = Path(settings.get("storage.review_dir", "./data/review"))
    path = review_dir / GOLD_CSV
    if not path.exists():
        print(f"gold set 이 없습니다: {path}\n먼저 `python tools/gold_set.py sample` 을 실행하세요.")
        return 1

    labeled = _read_gold(path)
    if not labeled:
        print("라벨이 하나도 채워지지 않았습니다.")
        return 1

    db = Database(settings.db_path)
    db.initialize()
    repo = SqliteRelationRepository(db)
    candidate_pairs = {
        tuple(sorted((c.term_a, c.term_b))) for c in repo.list_candidates()
    }
    active_pairs, review_pairs = set(), set()
    for row in db.execute(
        """SELECT ta.term_key a, tb.term_key b, r.status FROM term_relations r
           JOIN terms ta ON ta.id = r.source_term_id
           JOIN terms tb ON tb.id = r.target_term_id"""
    ).fetchall():
        pair = tuple(sorted((row["a"], row["b"])))
        if row["status"] == RelationStatus.ACTIVE.value:
            active_pairs.add(pair)
        elif row["status"] == RelationStatus.REVIEW.value:
            review_pairs.add(pair)

    positives = [r for r in labeled
                 if r["라벨"] in POSITIVE_LABELS]
    print(f"라벨된 {len(labeled)}쌍 (그중 SYNONYM {len(positives)}쌍)")
    print(f"라벨 분포: {dict(Counter(r['라벨'] for r in labeled))}\n")

    def pair_of(row):
        return tuple(sorted((row["termKeyA"], row["termKeyB"])))

    # 후보 생성 recall - 정답을 후보 단계에서 놓치지 않았는가
    if positives:
        kept = sum(1 for r in positives if pair_of(r) in candidate_pairs)
        print(f"[후보 recall]   SYNONYM {len(positives)}쌍 중 후보에 존재: "
              f"{kept}쌍 ({kept / len(positives) * 100:.1f}%)")

    # ACTIVE precision - 자동 사전에 실린 것 중 실제로 옳은 비율
    active_labeled = [r for r in labeled if pair_of(r) in active_pairs]
    if active_labeled:
        correct = sum(1 for r in active_labeled if r["라벨"] in POSITIVE_LABELS)
        print(f"[ACTIVE 정밀도] 자동 승인 {len(active_labeled)}쌍 중 정답: "
              f"{correct}쌍 ({correct / len(active_labeled) * 100:.1f}%)")
    else:
        print("[ACTIVE 정밀도] 라벨된 쌍 중 ACTIVE 가 없습니다.")

    review_labeled = [r for r in labeled if pair_of(r) in review_pairs]
    if positives and review_labeled:
        recovered = sum(1 for r in review_labeled if r["라벨"] in POSITIVE_LABELS)
        print(f"[REVIEW 회수]   REVIEW 로 넘어간 정답: {recovered}쌍 "
              f"(사람 검수로 회수 가능한 몫)")

    print("\n[층별 정답 비율] 어떤 경로가 실제로 쓸모 있는지 본다")
    by_stratum: Dict[str, Counter] = defaultdict(Counter)
    for row in labeled:
        by_stratum[row["층"]][row["라벨"]] += 1
    for name, counter in sorted(by_stratum.items()):
        total = sum(counter.values())
        good = counter["SYNONYM"] + counter["DIRECTIONAL"]
        print(f"    {name:<8} {total:>4}쌍 중 SYNONYM+DIRECTIONAL {good:>3}쌍 "
              f"({good / total * 100:5.1f}%)")

    db.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Gold Set 샘플링 / 평가")
    parser.add_argument("-c", "--config", default="config.yaml")

    # 서브커맨드 뒤에 -c 를 써도 받아준다.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-c", "--config", default=argparse.SUPPRESS)

    sub = parser.add_subparsers(dest="command", required=True)

    sample = sub.add_parser("sample", help="층화 표본을 뽑아 라벨 시트를 만든다",
                            parents=[common])
    sample.add_argument("--size", type=int, default=250)
    sample.add_argument("--seed", type=int, default=42)

    sub.add_parser("evaluate", help="라벨 대비 현재 설정을 평가한다", parents=[common])

    args = parser.parse_args()
    settings = load_settings(args.config, load_env=False)

    if args.command == "sample":
        return command_sample(settings, args.size, args.seed)
    return command_evaluate(settings)


if __name__ == "__main__":
    raise SystemExit(main())
