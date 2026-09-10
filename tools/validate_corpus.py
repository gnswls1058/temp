"""코퍼스 품질 검사 (§24).

카테고리 분포, ID 유일성, 제목 중복, 인물/부서/시스템 표기 오류,
사람 이름 등장 비율, 문서 길이 분포, 연도 분포를 점검한다.
"""
from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from corpus.registry import (  # noqa: E402
    CATEGORY_TARGETS, DEPARTMENTS, PEOPLE, PROJECTS, SYSTEMS, TOTAL_TARGET,
    load_documents,
)


def main() -> int:
    documents = load_documents()
    problems: list[str] = []

    print(f"총 문서 수: {len(documents)} / 목표 {TOTAL_TARGET}")

    # ID
    ids = [d["id"] for d in documents]
    duplicates = [i for i, c in Counter(ids).items() if c > 1]
    if duplicates:
        problems.append(f"중복 ID: {duplicates}")
    bad_format = [i for i in ids if not re.fullmatch(r"DOC-\d{3}", i)]
    if bad_format:
        problems.append(f"잘못된 ID 형식: {bad_format}")
    missing = [f"DOC-{n:03d}" for n in range(1, TOTAL_TARGET + 1)
               if f"DOC-{n:03d}" not in set(ids)]
    if missing:
        print(f"  미작성 ID {len(missing)}개: {missing[:8]}{' ...' if len(missing) > 8 else ''}")

    # 제목
    titles = [d["title"] for d in documents]
    duplicate_titles = [t for t, c in Counter(titles).items() if c > 1]
    if duplicate_titles:
        problems.append(f"중복 제목(Confluence 는 스페이스 내 제목 유일): {duplicate_titles}")

    # 카테고리 분포
    print("\n[카테고리 분포]")
    counts = Counter(d["type"] for d in documents)
    for category, target in CATEGORY_TARGETS.items():
        current = counts.get(category, 0)
        mark = "OK " if current == target else "   "
        print(f"  {mark}{category:18} {current:3d} / {target}")
    unknown = set(counts) - set(CATEGORY_TARGETS)
    if unknown:
        problems.append(f"알 수 없는 문서 유형: {unknown}")

    # 부서 / 프로젝트 / 시스템 / 인물
    for field, allowed, label in (
        ("department", DEPARTMENTS, "부서"),
        ("project", PROJECTS | {None}, "프로젝트"),
    ):
        bad = {d[field] for d in documents if d.get(field) not in allowed}
        if bad:
            problems.append(f"알 수 없는 {label}: {bad}")

    bad_systems = {s for d in documents for s in d.get("systems", []) if s not in SYSTEMS}
    if bad_systems:
        problems.append(f"알 수 없는 시스템: {bad_systems}")
    bad_people = {p for d in documents for p in d.get("participants", []) if p not in PEOPLE}
    if bad_people:
        problems.append(f"명단에 없는 인물: {bad_people}")

    # 본문에 등장하지만 participants 에 없는 인물
    for document in documents:
        listed = set(document.get("participants", []))
        mentioned = {name for name in PEOPLE if name in document["body"]}
        if mentioned - listed:
            problems.append(
                f"{document['id']}: 본문에 등장하지만 participants 누락 {sorted(mentioned - listed)}"
            )

    # 사람 이름 등장 비율 (40~60%)
    with_people = sum(1 for d in documents if d.get("participants"))
    ratio = with_people / len(documents) * 100 if documents else 0
    print(f"\n[인물 등장] {with_people}/{len(documents)} = {ratio:.1f}% (목표 40~60%)")
    if documents and not 38 <= ratio <= 62:
        problems.append(f"인물 등장 비율 이탈: {ratio:.1f}%")

    over_crowded = [d["id"] for d in documents if len(d.get("participants", [])) > 3]
    if over_crowded:
        problems.append(f"한 문서 인물 3명 초과: {over_crowded}")

    print("\n[인물별 등장 횟수]")
    people_counter = Counter(p for d in documents for p in d.get("participants", []))
    for name, count in people_counter.most_common():
        print(f"  {name} {count}")

    # 길이 분포
    lengths = [len(d["body"]) for d in documents]
    if lengths:
        buckets = Counter(
            "짧음(<600)" if n < 600 else "보통(600~1200)" if n < 1200
            else "김(1200~2500)" if n < 2500 else "매우 김(2500+)"
            for n in lengths
        )
        print(f"\n[본문 길이] 최소 {min(lengths)} / 평균 {sum(lengths)//len(lengths)} / 최대 {max(lengths)}")
        for bucket, count in buckets.most_common():
            print(f"  {bucket:16} {count}")
        too_short = [d["id"] for d in documents if len(d["body"]) < 280]
        if too_short:
            problems.append(f"본문이 지나치게 짧음(<280자): {too_short}")

    # 연도 분포
    print("\n[작성 연도]")
    for year, count in sorted(Counter(d["date"][:4] for d in documents).items()):
        print(f"  {year} {count}")

    # 프로젝트 분포
    print("\n[프로젝트 분포]")
    for project, count in Counter(d.get("project") or "(공통)" for d in documents).most_common():
        print(f"  {project:16} {count}")

    print()
    if problems:
        print(f"발견된 문제 {len(problems)}건:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("문제 없음")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
