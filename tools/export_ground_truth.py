"""Ground Truth 를 별도 평가 데이터로 내보낸다 (§19).

Confluence 본문에는 포함하지 않는다. 게시 이후 실행하면 pageId 가 함께 기록된다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from corpus.registry import (  # noqa: E402
    SYNONYM_GROUND_TRUTH, ground_truth, load_documents,
)

STATE_PATH = ROOT / "corpus" / "publish_state.json"
OUTPUT_PATH = ROOT / "corpus" / "ground_truth.json"
SYNONYM_PATH = ROOT / "corpus" / "synonym_ground_truth.json"


def main() -> int:
    state = (
        json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if STATE_PATH.exists() else {}
    )
    pages = state.get("pages", {})

    rows = []
    for document in load_documents():
        row = ground_truth(document)
        published = pages.get(document["id"])
        row["pageId"] = published["pageId"] if published else None
        row["bodyLength"] = len(document["body"])
        rows.append(row)

    OUTPUT_PATH.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    SYNONYM_PATH.write_text(
        json.dumps(SYNONYM_GROUND_TRUTH, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"유의어 정답 {len(SYNONYM_GROUND_TRUTH)}쌍: {SYNONYM_PATH}")
    linked = sum(1 for r in rows if r["pageId"])
    print(f"Ground Truth {len(rows)}건 저장: {OUTPUT_PATH} (pageId 연결 {linked}건)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
