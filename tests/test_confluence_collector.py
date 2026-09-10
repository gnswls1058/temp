"""Confluence Client / Document Collector 테스트 (§7, §50)."""
import pytest
import requests

from app.collector.document_collector import DocumentCollector
from app.confluence.client import (
    ConfluenceAuthError,
    ConfluenceClient,
    ConfluenceError,
    ConfluenceNotFoundError,
)
from app.models.document import Document
from app.repository.database import Database
from app.repository.document_repository import SqliteDocumentRepository


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, headers=None, text=""):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}
        self.headers = headers or {}
        self.text = text
        self.content = b"x" if json_data is not None else b""

    def json(self):
        return self._json


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.headers = {}
        self.auth = None

    def request(self, method, url, params=None, timeout=None):
        self.requests.append({"url": url, "params": dict(params or {})})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def make_client(session, **kwargs):
    return ConfluenceClient(
        "https://example.atlassian.net/wiki", "a@b.c", "token",
        session=session, backoff_base=0.0, backoff_max=0.0, **kwargs
    )


def page(page_id, version=1, body="본문"):
    return {
        "id": page_id,
        "spaceId": "111",
        "title": f"페이지 {page_id}",
        "version": {"number": version, "createdAt": "2026-01-01T00:00:00Z"},
        "createdAt": "2026-01-01T00:00:00Z",
        "body": {"storage": {"value": f"<p>{body}</p>"}},
        "_links": {"webui": f"/spaces/X/pages/{page_id}"},
    }


# ----------------------------------------------------------------------
def test_pagination_follows_next_cursor():
    session = FakeSession([
        FakeResponse(json_data={
            "results": [page("1"), page("2")],
            "_links": {"next": "/wiki/api/v2/pages?cursor=abc&limit=2"},
        }),
        FakeResponse(json_data={"results": [page("3")], "_links": {}}),
    ])
    pages = list(make_client(session).iter_pages(["111"], limit=2))

    assert [p["id"] for p in pages] == ["1", "2", "3"]
    assert len(session.requests) == 2
    assert session.requests[1]["params"]["cursor"] == "abc"


def test_auth_error_is_not_retried():
    session = FakeSession([FakeResponse(status_code=401, text="unauthorized")])
    with pytest.raises(ConfluenceAuthError):
        list(make_client(session).iter_pages())
    assert len(session.requests) == 1


def test_not_found_error():
    session = FakeSession([FakeResponse(status_code=404, text="missing")])
    with pytest.raises(ConfluenceNotFoundError):
        make_client(session).get_page("999")


def test_rate_limit_is_retried_with_retry_after():
    session = FakeSession([
        FakeResponse(status_code=429, headers={"Retry-After": "0"}),
        FakeResponse(json_data={"results": [page("1")], "_links": {}}),
    ])
    assert len(list(make_client(session).iter_pages())) == 1
    assert len(session.requests) == 2


def test_server_error_is_retried_then_raises():
    session = FakeSession([FakeResponse(status_code=500) for _ in range(3)])
    with pytest.raises(ConfluenceError):
        list(make_client(session, max_retries=3).iter_pages())
    assert len(session.requests) == 3


def test_network_error_is_retried():
    session = FakeSession([
        requests.ConnectionError("boom"),
        FakeResponse(json_data={"results": [page("1")], "_links": {}}),
    ])
    assert len(list(make_client(session).iter_pages())) == 1


# ----------------------------------------------------------------------
@pytest.fixture
def repo(tmp_path):
    db = Database(tmp_path / "collector.db")
    db.initialize()
    yield SqliteDocumentRepository(db)
    db.close()


def test_unchanged_version_is_not_refetched(repo):
    session = FakeSession([
        FakeResponse(json_data={"results": [page("100", version=5)], "_links": {}}),
    ])
    collector = DocumentCollector(make_client(session), repo, space_ids=["111"])

    repo.upsert(Document(page_id="100", space_id="111", title="기존",
                         body="<p>기존</p>", version=5))
    result = collector.sync("run-1")

    assert result.unchanged == 1 and result.updated == 0
    assert repo.get("100").title == "기존"  # 재수집하지 않음


def test_changed_version_is_updated(repo):
    session = FakeSession([
        FakeResponse(json_data={"results": [page("100", version=6, body="새 본문")],
                                "_links": {}}),
    ])
    collector = DocumentCollector(make_client(session), repo, space_ids=["111"])

    repo.upsert(Document(page_id="100", space_id="111", title="기존",
                         body="<p>기존</p>", version=5))
    result = collector.sync("run-1")

    assert result.updated == 1
    document = repo.get("100")
    assert document.version == 6
    assert "새 본문" in document.body


def test_new_document_is_created_with_original_body(repo):
    session = FakeSession([
        FakeResponse(json_data={"results": [page("200")], "_links": {}}),
    ])
    result = DocumentCollector(make_client(session), repo, space_ids=["111"]).sync("run-1")

    assert result.created == 1
    assert repo.get("200").body == "<p>본문</p>"  # 원본 그대로 저장 (§7)


def test_single_document_failure_does_not_stop_sync(repo):
    broken = page("300")
    broken["version"] = "잘못된값"  # int 변환 실패 유도
    session = FakeSession([
        FakeResponse(json_data={"results": [broken, page("301")], "_links": {}}),
    ])
    result = DocumentCollector(make_client(session), repo, space_ids=["111"]).sync("run-1")

    assert result.failed == 1
    assert result.created == 1
    assert repo.get("301") is not None
    assert repo.count_errors("run-1") == 1
