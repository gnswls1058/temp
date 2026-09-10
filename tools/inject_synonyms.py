"""테스트 코퍼스에 유의어 변이를 주입한다.

배경: 처음 만든 300개 문서는 한 개념을 처음부터 끝까지 한 가지 표기로만 썼다.
그래서 유의어 사전 파이프라인을 평가할 수 없었다(찾아야 할 현상 자체가 없음).

여기서는 실제 조직에서 표기가 갈리는 방식 그대로 변이를 넣는다.
  - 시기축   : 정책 통합 전후로 용어가 바뀐다
  - 부서축   : 기획팀과 개발팀이 다른 말을 쓴다
  - 문서종류축: 회의록에서는 줄여 부르고 명세서에서는 정식 명칭을 쓴다

치환은 문서 단위로 일관되게 적용한다. 한 문서 안에서 두 표기가 섞이면
사람이 봐도 부자연스럽고, 파이프라인 입장에서도 잘못된 신호가 된다.

사용::

    python tools/inject_synonyms.py --dry-run   # 무엇이 바뀌는지만 확인
    python tools/inject_synonyms.py             # corpus/docs/*.py 수정
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Callable, Dict, List, NamedTuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DOCS_DIR = ROOT / "corpus" / "docs"

# 코드 펜스와 인라인 코드는 식별자(reward_balance 등)라서 절대 건드리지 않는다.
_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_RE = re.compile(r"`[^`\n]+`")
_SENTINEL_OPEN, _SENTINEL_CLOSE = "", ""


# 받침 유무에 따라 달라지는 조사. '포인트는' -> '적립금은' 처럼 교정한다.
_PARTICLES_AFTER_BATCHIM = {"는": "은", "가": "이", "를": "을", "와": "과", "로": "으로"}
_PARTICLES_AFTER_VOWEL = {"은": "는", "이": "가", "을": "를", "과": "와", "으로": "로"}


def _has_batchim(syllable: str) -> bool | None:
    """마지막 글자에 받침이 있는가. 한글이 아니면 None."""
    if not syllable or not ("가" <= syllable <= "힣"):
        return None
    return (ord(syllable) - 0xAC00) % 28 != 0


def fix_particles(text: str, word: str) -> str:
    """치환으로 받침이 달라진 자리의 조사를 바로잡는다.

    조사 뒤가 다시 한글이면 단어의 일부일 수 있으므로('적립금이력') 건드리지 않는다.
    """
    batchim = _has_batchim(word[-1:])
    if batchim is None:
        return text
    table = _PARTICLES_AFTER_BATCHIM if batchim else _PARTICLES_AFTER_VOWEL
    for wrong, right in table.items():
        text = re.sub(
            rf"{re.escape(word)}{wrong}(?![가-힣])", f"{word}{right}", text
        )
    return text


class Rule(NamedTuple):
    name: str            # 정답 쌍 이름
    canonical: str       # 원래 표기 (그대로 두는 쪽)
    variant: str         # 새로 넣는 표기
    axis: str            # 왜 갈리는가
    selects: Callable[[dict], bool]
    substitutions: List[tuple]   # (정규식, 치환문자열)


def _word(term: str, replacement: str, *, not_before: str = "") -> tuple:
    """단어 시작 위치에서만 바꾼다.

    한국어는 조사가 단어에 그대로 붙으므로('포인트를', '탈퇴한') 뒤쪽까지
    경계를 요구하면 대부분이 치환되지 않는다. 앞쪽 경계만 본다.
    ``not_before`` 로 붙여 쓰면 안 되는 접미사('회원가입')를 막는다.
    """
    tail = rf"(?!{not_before})" if not_before else ""
    return (re.compile(rf"(?<![0-9A-Za-z가-힣]){re.escape(term)}{tail}"), replacement)


RULES: List[Rule] = [
    # --- 시기축: 리워드 통합 이전에는 '적립금'이라 불렀다 ---
    Rule(
        name="적립금 ↔ 포인트", canonical="포인트", variant="적립금",
        axis="시기(2026년 리워드 통합 이전 문서)",
        # 통합 논의 문서는 '적립금'과 '포인트'를 일부러 구분해서 쓴다.
        # 여기까지 바꾸면 "구매 적립금, 이벤트 적립금"처럼 문장이 망가진다.
        selects=lambda d: d["date"] < "2026-01-01" and "적립금" not in d["body"],
        substitutions=[_word("포인트", "적립금")],
    ),
    # --- 부서축: 기획·고객지원은 사용자 쪽 용어를 쓴다 ---
    Rule(
        name="고객 ↔ 회원", canonical="회원", variant="고객",
        axis="부서(서비스기획팀·고객지원팀)",
        selects=lambda d: d["department"] in ("서비스기획팀", "고객지원팀"),
        # '회원가입'은 고유 기능명이므로 유지한다.
        substitutions=[_word("회원", "고객", not_before="가입")],
    ),
    # --- 문서종류축: 회의록·운영 문서에서는 다른 말을 쓴다 ---
    Rule(
        name="중복 방지 키 ↔ 멱등키", canonical="멱등키", variant="중복 방지 키",
        axis="문서 종류(회의록·진행상황·이슈·매뉴얼)",
        selects=lambda d: d["type"] in (
            "기획자와의 회의록", "개발자 회의록", "프로젝트 진행상황",
            "이슈 정리", "매뉴얼 / 정의서",
        ),
        substitutions=[_word("멱등키", "중복 방지 키")],
    ),
    Rule(
        name="정합성 대조 ↔ 대사", canonical="대사", variant="정합성 대조",
        axis="문서 종류(진행상황·DB 설계·매뉴얼)",
        selects=lambda d: d["type"] in (
            "프로젝트 진행상황", "DB 테이블 설계안", "매뉴얼 / 정의서",
        ),
        substitutions=[_word("대사", "정합성 대조")],
    ),
    Rule(
        name="카트 ↔ 장바구니", canonical="장바구니", variant="카트",
        axis="문서 종류(기획 회의록·이슈·매뉴얼·진행상황·코드리뷰)",
        selects=lambda d: d["type"] in (
            "기획자와의 회의록", "이슈 정리", "매뉴얼 / 정의서",
            "프로젝트 진행상황", "코드리뷰",
        ),
        substitutions=[_word("장바구니", "카트")],
    ),
    Rule(
        name="수령지 ↔ 배송지", canonical="배송지", variant="수령지",
        axis="문서 종류(이슈·진행상황·기획 회의록·매뉴얼)",
        selects=lambda d: d["type"] in (
            "이슈 정리", "프로젝트 진행상황", "기획자와의 회의록", "매뉴얼 / 정의서",
        ),
        substitutions=[_word("배송지", "수령지")],
    ),
    Rule(
        name="감사 이력 ↔ 감사 로그", canonical="감사 로그", variant="감사 이력",
        axis="문서 종류(회의록·진행상황·이슈)",
        selects=lambda d: d["type"] in (
            "기획자와의 회의록", "개발자 회의록", "프로젝트 진행상황", "이슈 정리",
        ),
        substitutions=[_word("감사 로그", "감사 이력")],
    ),
    Rule(
        name="해지 ↔ 탈퇴", canonical="탈퇴", variant="해지",
        axis="문서 종류(기획 회의록·매뉴얼·진행상황)",
        selects=lambda d: d["type"] in (
            "기획자와의 회의록", "매뉴얼 / 정의서", "프로젝트 진행상황",
        ),
        substitutions=[_word("탈퇴", "해지")],
    ),
]


def protect_code(text: str) -> tuple[str, List[str]]:
    """코드 구간을 사전토큰으로 치환해 보호한다."""
    saved: List[str] = []

    def stash(match: re.Match) -> str:
        saved.append(match.group(0))
        return f"{_SENTINEL_OPEN}{len(saved) - 1}{_SENTINEL_CLOSE}"

    text = _FENCE_RE.sub(stash, text)
    text = _INLINE_RE.sub(stash, text)
    return text, saved


def restore_code(text: str, saved: List[str]) -> str:
    for index, original in enumerate(saved):
        text = text.replace(f"{_SENTINEL_OPEN}{index}{_SENTINEL_CLOSE}", original)
    return text


def apply_rules(text: str, rules: List[Rule]) -> tuple[str, Counter]:
    text, saved = protect_code(text)
    counts: Counter = Counter()
    for rule in rules:
        for pattern, replacement in rule.substitutions:
            text, n = pattern.subn(replacement, text)
            if n:
                text = fix_particles(text, replacement)
            counts[rule.name] += n
    return restore_code(text, saved), counts


# ----------------------------------------------------------------------
_DOC_ID_RE = re.compile(r'"id":\s*"(DOC-\d+)"')
_TITLE_RE = re.compile(r'"title":\s*"((?:[^"\\]|\\.)*)"')


def transform_file(path: Path, meta: Dict[str, dict],
                   dry_run: bool) -> tuple[Counter, int]:
    source = path.read_text(encoding="utf-8")
    counts: Counter = Counter()
    touched = 0
    pieces: List[str] = []
    cursor = 0

    for match in _DOC_ID_RE.finditer(source):
        doc_id = match.group(1)
        document = meta.get(doc_id)
        if document is None:
            continue
        rules = [r for r in RULES if r.selects(document)]
        if not rules:
            continue

        # 이 문서의 body 문자열 구간을 찾는다.
        body_start = source.find('"body": """', match.end())
        if body_start == -1:
            continue
        open_at = body_start + len('"body": """')
        close_at = source.find('"""', open_at)
        if close_at == -1:
            continue

        title_match = _TITLE_RE.search(source, match.end(), body_start)
        new_body, body_counts = apply_rules(source[open_at:close_at], rules)
        counts += body_counts

        segments = [(open_at, close_at, new_body)]
        if title_match:
            new_title, title_counts = apply_rules(title_match.group(1), rules)
            counts += title_counts
            if new_title != title_match.group(1):
                segments.insert(0, (title_match.start(1), title_match.end(1), new_title))

        if all(source[s:e] == v for s, e, v in segments):
            continue
        touched += 1
        for start, end, value in segments:
            pieces.append(source[cursor:start])
            pieces.append(value)
            cursor = end

    if not dry_run and pieces:
        pieces.append(source[cursor:])
        path.write_text("".join(pieces), encoding="utf-8")
    return counts, touched


def main() -> int:
    parser = argparse.ArgumentParser(description="테스트 코퍼스에 유의어 변이 주입")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from corpus import registry
    meta = {d["id"]: d for d in registry.load_documents()}

    total: Counter = Counter()
    touched = 0
    for path in sorted(DOCS_DIR.glob("batch_*.py")):
        counts, n = transform_file(path, meta, args.dry_run)
        total += counts
        touched += n

    print(f"{'[dry-run] ' if args.dry_run else ''}수정 대상 문서 {touched}개\n")
    print(f"  {'정답 쌍':<28} {'치환':>6}  변형 축")
    print("  " + "-" * 72)
    for rule in RULES:
        print(f"  {rule.name:<28} {total[rule.name]:>6}회  {rule.axis}")
    print(f"\n  합계 {sum(total.values())}회 치환")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
