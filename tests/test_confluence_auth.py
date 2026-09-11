"""Confluence 인증 방식 테스트.

email 유무로 인증 방식을 추측하면, PAT 를 쓰면서 email 을 채워 둔 것만으로
조용히 Basic 으로 바뀌어 401 이 난다. 실제 내부망 첫 연결에서 이 일이 났다.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.confluence.client import ConfluenceAuthError, ConfluenceClient  # noqa: E402

URL = "https://confluence.company.co.kr/confluence"


def bearer_header(client):
    return client.session.headers.get("Authorization")


# ----------------------------------------------------------------------
def test_bearer_is_used_even_when_email_is_set():
    """PAT 를 쓰는데 email 이 남아 있어도 Bearer 로 보내야 한다."""
    client = ConfluenceClient(URL, "hong@company.co.kr", "PAT", auth_type="bearer")
    assert client.auth_type == "bearer"
    assert bearer_header(client) == "Bearer PAT"
    assert client.session.auth is None


def test_basic_requires_email():
    with pytest.raises(ValueError, match="email"):
        ConfluenceClient(URL, "", "TOKEN", auth_type="basic")


def test_basic_sets_http_basic_auth():
    client = ConfluenceClient(URL, "hong@company.co.kr", "TOKEN", auth_type="basic")
    assert client.auth_type == "basic"
    assert client.session.auth is not None
    assert bearer_header(client) is None


@pytest.mark.parametrize("email,expected", [
    ("", "bearer"),
    ("hong@company.co.kr", "basic"),
])
def test_auto_keeps_previous_behaviour(email, expected):
    client = ConfluenceClient(URL, email, "TOKEN", auth_type="auto")
    assert client.auth_type == expected


def test_invalid_auth_type_is_rejected():
    with pytest.raises(ValueError, match="auth_type"):
        ConfluenceClient(URL, "", "TOKEN", auth_type="oauth")


# ----------------------------------------------------------------------
class StubResponse:
    status_code = 401
    text = "Unauthorized"
    headers = {}

    def json(self):
        return {}


class StubSession:
    def __init__(self):
        self.headers = {}
        self.auth = None
        self.verify = True

    def request(self, *args, **kwargs):
        return StubResponse()


def test_401_message_tells_which_auth_mode_was_used():
    """401 일 때 어떤 방식으로 보냈는지 알려줘야 원인을 찾을 수 있다."""
    client = ConfluenceClient(URL, "hong@company.co.kr", "TOKEN",
                              auth_type="basic", session=StubSession(), max_retries=1)
    with pytest.raises(ConfluenceAuthError) as exc:
        client.get_page("1")
    message = str(exc.value)
    assert "basic" in message
    assert "bearer" in message      # PAT 를 쓴다면 bearer 로 바꾸라는 안내


def test_401_message_for_bearer_mode():
    client = ConfluenceClient(URL, "", "TOKEN",
                              auth_type="bearer", session=StubSession(), max_retries=1)
    with pytest.raises(ConfluenceAuthError) as exc:
        client.get_page("1")
    assert "Bearer" in str(exc.value)
