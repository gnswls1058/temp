"""테스트 코퍼스 도구용 공통 Confluence 세션 헬퍼."""
from __future__ import annotations

import os
from pathlib import Path

import requests
from requests.auth import HTTPBasicAuth

ROOT = Path(__file__).resolve().parents[1]


def load_env(path: Path = ROOT / ".env") -> None:
    if not path.exists():
        raise FileNotFoundError(f".env 파일이 없습니다: {path}")
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def make_session() -> tuple[requests.Session, str]:
    load_env()
    base = os.environ["CONFLUENCE_BASE_URL"].rstrip("/")
    session = requests.Session()
    session.auth = HTTPBasicAuth(
        os.environ["CONFLUENCE_EMAIL"], os.environ["CONFLUENCE_API_TOKEN"]
    )
    session.headers.update({"Accept": "application/json"})
    return session, base
