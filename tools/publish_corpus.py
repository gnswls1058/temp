"""테스트 코퍼스를 Confluence 에 게시한다.

- 대상 스페이스가 없으면 생성한다 (기본 key: NEXBRIDGE).
- 프로젝트별 부모 페이지를 만들고 그 아래에 문서를 배치한다.
- 이미 게시된 문서는 본문 해시를 비교해 변경된 경우에만 갱신한다.
- 429 / 5xx 는 백오프 재시도, 그 외 실패는 기록 후 계속 진행한다.

사용::

    python tools/publish_corpus.py --dry-run
    python tools/publish_corpus.py --limit 3
    python tools/publish_corpus.py
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from corpus.registry import PARENT_PAGES, load_documents, parent_for  # noqa: E402
from tools._confluence_env import make_session  # noqa: E402
from tools.markdown_to_storage import convert  # noqa: E402

STATE_PATH = ROOT / "corpus" / "publish_state.json"
DEFAULT_SPACE_KEY = "NEXBRIDGE"
DEFAULT_SPACE_NAME = "NexBridge Corporation"

PARENT_INTRO = {
    "NexBridge 공통 문서": (
        "## 공통 문서\n"
        "프로젝트에 종속되지 않는 팀 규칙, 매뉴얼, 공통 시스템 문서를 모아둡니다.\n"
    ),
    "Project Aurora": "## Project Aurora\n회원가입 개편 프로젝트(NEXUS) 문서 모음입니다.\n",
    "Project Falcon": "## Project Falcon\n결제 시스템 고도화 프로젝트(ORBIT) 문서 모음입니다.\n",
    "Project Nova": "## Project Nova\n리워드 플랫폼 개선 프로젝트(MARS) 문서 모음입니다.\n",
    "Project Atlas": "## Project Atlas\nAdmin(AIMS) 개편 프로젝트 문서 모음입니다.\n",
    "Project Echo": "## Project Echo\n인증 시스템 개선 프로젝트(BlueGate) 문서 모음입니다.\n",
}


def slugify(value: str) -> str:
    value = re.sub(r"[^0-9A-Za-z가-힣]+", "-", value).strip("-").lower()
    return value[:60]


def make_labels(document: Dict[str, Any]) -> list[str]:
    labels = [slugify(document["type"])]
    if document.get("project"):
        labels.append(slugify(document["project"]))
    labels.extend(slugify(system) for system in document.get("systems", [])[:2])
    labels.append(document["date"][:4])
    if document.get("deprecated"):
        labels.append("deprecated")
    return [label for label in dict.fromkeys(labels) if label]


class Publisher:
    def __init__(self, space_key: str, space_name: str, *, dry_run: bool = False):
        self.session, self.base = make_session()
        self.space_key = space_key
        self.space_name = space_name
        self.dry_run = dry_run
        self.state: Dict[str, Any] = self._load_state()

    # ------------------------------------------------------------------
    def _load_state(self) -> Dict[str, Any]:
        if STATE_PATH.exists():
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return {"spaceKey": self.space_key, "spaceId": None, "parents": {}, "pages": {}}

    def _save_state(self) -> None:
        if self.dry_run:
            return
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(
            json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ------------------------------------------------------------------
    def request(self, method: str, path: str, *, params=None, json_body=None,
                max_retries: int = 5) -> Any:
        url = path if path.startswith("http") else f"{self.base}{path}"
        for attempt in range(1, max_retries + 1):
            response = self.session.request(
                method, url, params=params, json=json_body, timeout=60,
                headers={"Content-Type": "application/json"} if json_body else None,
            )
            if 200 <= response.status_code < 300:
                return response.json() if response.content else {}
            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                delay = (
                    float(retry_after) if retry_after and retry_after.isdigit()
                    else float(2 ** attempt)
                )
                print(f"    429 rate limit - {delay:.0f}s 대기")
                time.sleep(delay)
                continue
            if response.status_code >= 500:
                delay = min(2 ** attempt, 30) + random.uniform(0, 1)
                print(f"    {response.status_code} 서버 오류 - {delay:.1f}s 후 재시도")
                time.sleep(delay)
                continue
            raise RuntimeError(
                f"{method} {url} -> {response.status_code}: {response.text[:300]}"
            )
        raise RuntimeError(f"{method} {url} 재시도 초과")

    # ------------------------------------------------------------------
    def ensure_space(self) -> str:
        data = self.request(
            "GET", "/api/v2/spaces", params={"keys": self.space_key, "limit": 1}
        )
        results = data.get("results") or []
        if results:
            space_id = str(results[0]["id"])
            print(f"스페이스 확인: {self.space_key} (id={space_id})")
        else:
            if self.dry_run:
                print(f"[dry-run] 스페이스 생성 예정: {self.space_key}")
                return "DRYRUN"
            print(f"스페이스 생성: {self.space_key} / {self.space_name}")
            try:
                created = self.request(
                    "POST", "/api/v2/spaces",
                    json_body={"key": self.space_key, "name": self.space_name},
                )
            except RuntimeError as exc:
                print(f"  v2 생성 실패({exc}) - v1 API 로 재시도")
                created = self.request(
                    "POST", "/rest/api/space",
                    json_body={
                        "key": self.space_key,
                        "name": self.space_name,
                        "description": {
                            "plain": {
                                "value": "검색 평가용 테스트 코퍼스 (가상 회사)",
                                "representation": "plain",
                            }
                        },
                    },
                )
            space_id = str(created.get("id") or created.get("spaceId"))
        self.state["spaceId"] = space_id
        self.state["spaceKey"] = self.space_key
        self._save_state()
        return space_id

    def find_page(self, title: str) -> Optional[Dict[str, Any]]:
        space_id = self.state["spaceId"]
        data = self.request(
            "GET", f"/api/v2/spaces/{space_id}/pages",
            params={"title": title, "limit": 5},
        )
        for page in data.get("results") or []:
            if page.get("title") == title:
                return page
        return None

    def create_page(self, title: str, storage: str,
                    parent_id: Optional[str]) -> Dict[str, Any]:
        body = {
            "spaceId": self.state["spaceId"],
            "status": "current",
            "title": title,
            "body": {"representation": "storage", "value": storage},
        }
        if parent_id:
            body["parentId"] = parent_id
        return self.request("POST", "/api/v2/pages", json_body=body)

    def update_page(self, page_id: str, title: str, storage: str,
                    version: int) -> Dict[str, Any]:
        return self.request(
            "PUT", f"/api/v2/pages/{page_id}",
            json_body={
                "id": page_id,
                "status": "current",
                "title": title,
                "body": {"representation": "storage", "value": storage},
                "version": {"number": version + 1, "message": "corpus sync"},
            },
        )

    def add_labels(self, page_id: str, labels: list[str]) -> None:
        if not labels:
            return
        try:
            self.request(
                "POST", f"/rest/api/content/{page_id}/label",
                json_body=[{"prefix": "global", "name": label} for label in labels],
            )
        except RuntimeError as exc:
            print(f"    라벨 부여 실패(무시): {exc}")

    # ------------------------------------------------------------------
    def ensure_parents(self) -> Dict[str, str]:
        parents: Dict[str, str] = self.state.get("parents", {})
        for title in PARENT_PAGES:
            if title in parents:
                continue
            if self.dry_run:
                print(f"[dry-run] 부모 페이지 생성 예정: {title}")
                parents[title] = "DRYRUN"
                continue
            existing = self.find_page(title)
            if existing:
                parents[title] = str(existing["id"])
                print(f"부모 페이지 확인: {title} (id={existing['id']})")
                continue
            created = self.create_page(title, convert(PARENT_INTRO[title]), None)
            parents[title] = str(created["id"])
            print(f"부모 페이지 생성: {title} (id={created['id']})")
        self.state["parents"] = parents
        self._save_state()
        return parents

    # ------------------------------------------------------------------
    def publish(self, limit: Optional[int] = None,
                only: Optional[set] = None) -> None:
        documents = load_documents()
        if only:
            documents = [d for d in documents if d["id"] in only]
        if limit:
            documents = documents[:limit]
        if not documents:
            print("게시할 문서가 없습니다.")
            return

        self.ensure_space()
        parents = self.ensure_parents()

        created = updated = skipped = failed = 0
        for index, document in enumerate(documents, start=1):
            doc_id = document["id"]
            title = document["title"]
            storage = convert(document["body"])
            digest = hashlib.sha256(
                (title + " " + storage).encode("utf-8")
            ).hexdigest()[:16]
            record = self.state["pages"].get(doc_id)
            prefix = f"[{index}/{len(documents)}] {doc_id}"

            if record and record.get("hash") == digest:
                skipped += 1
                continue

            if self.dry_run:
                print(f"{prefix} [dry-run] {title} ({len(storage)}자)")
                continue

            parent_id = parents[parent_for(document)]
            try:
                if record:
                    page = self.update_page(
                        record["pageId"], title, storage, int(record["version"])
                    )
                    updated += 1
                    action = "갱신"
                else:
                    existing = self.find_page(title)
                    if existing:
                        page = self.update_page(
                            str(existing["id"]), title, storage,
                            int(existing["version"]["number"]),
                        )
                        updated += 1
                        action = "갱신"
                    else:
                        page = self.create_page(title, storage, parent_id)
                        created += 1
                        action = "생성"

                page_id = str(page["id"])
                self.state["pages"][doc_id] = {
                    "pageId": page_id,
                    "title": title,
                    "hash": digest,
                    "version": int(page["version"]["number"]),
                    "parent": parent_for(document),
                }
                self.add_labels(page_id, make_labels(document))
                print(f"{prefix} {action}: {title}")
            except Exception as exc:
                failed += 1
                print(f"{prefix} 실패: {title} - {exc}")

            if index % 10 == 0:
                self._save_state()
            time.sleep(0.15)

        self._save_state()
        print(f"\n완료 - 생성 {created}, 갱신 {updated}, 변경없음 {skipped}, 실패 {failed}")


def main() -> int:
    parser = argparse.ArgumentParser(description="테스트 코퍼스를 Confluence 에 게시")
    parser.add_argument("--space-key", default=DEFAULT_SPACE_KEY)
    parser.add_argument("--space-name", default=DEFAULT_SPACE_NAME)
    parser.add_argument("--limit", type=int, default=None, help="앞에서 N개만 게시")
    parser.add_argument("--only", nargs="*", default=None, help="특정 DOC-ID 만 게시")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    publisher = Publisher(args.space_key, args.space_name, dry_run=args.dry_run)
    publisher.publish(limit=args.limit, only=set(args.only) if args.only else None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
