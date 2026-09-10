"""사내 LLM 서버 REST Client 테스트."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.validation.http_llm_client import HttpLLMClient, dig, extract_json  # noqa: E402
from app.validation.llm_client import LLMError, create_llm_client  # noqa: E402


class StubResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload, ensure_ascii=False)

    def json(self):
        return self._payload


class StubSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.headers = {}
        self.verify = True
        self.calls = []

    def post(self, url, json=None, timeout=None):
        self.calls.append({"url": url, "body": json})
        return self.responses.pop(0)


def openai_response(text):
    return StubResponse({"choices": [{"message": {"content": text}}]})


TOOL = {"name": "report", "input_schema": {"type": "object",
                                           "properties": {"results": {"type": "array"}}}}


# ----------------------------------------------------------------------
@pytest.mark.parametrize("text", [
    '{"results": [1]}',
    '```json\n{"results": [1]}\n```',
    '알겠습니다. 결과는 다음과 같습니다.\n{"results": [1]}\n이상입니다.',
    '```\n{"results": [1]}\n```',
])
def test_extract_json_handles_wrapped_output(text):
    """사내 모델은 코드펜스나 설명을 붙이는 경우가 흔하다."""
    assert extract_json(text) == {"results": [1]}


def test_extract_json_raises_on_garbage():
    with pytest.raises(LLMError):
        extract_json("JSON 을 만들 수 없습니다.")


def test_dig_walks_lists_and_dicts():
    payload = {"choices": [{"message": {"content": "hi"}}]}
    assert dig(payload, "choices.0.message.content") == "hi"
    with pytest.raises(LLMError):
        dig(payload, "choices.0.missing")


# ----------------------------------------------------------------------
def test_openai_format_request_and_parse():
    session = StubSession([openai_response('{"results": [{"pairId": "c1"}]}')])
    client = HttpLLMClient("http://llm.internal/v1/chat/completions", "internal-model",
                           session=session)

    result = client.call_tool("시스템", "사용자", TOOL, "report")

    assert result == {"results": [{"pairId": "c1"}]}
    body = session.calls[0]["body"]
    assert body["model"] == "internal-model"
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    # 도구 스키마를 프롬프트에 실어 보낸다(tool-use 미지원 서버 대응)
    assert "input_schema" in body["messages"][0]["content"] or \
           "properties" in body["messages"][0]["content"]


def test_plain_format_uses_configured_keys():
    session = StubSession([StubResponse({"data": {"text": '{"results": []}'}})])
    client = HttpLLMClient(
        "http://llm.internal/generate", "m", session=session,
        request_format="plain", response_path="data.text",
        system_key="instruction", prompt_key="input",
    )
    client.call_tool("시스템", "사용자", TOOL, "report")

    body = session.calls[0]["body"]
    assert body["instruction"].startswith("시스템")
    assert body["input"] == "사용자"


def test_server_error_is_retried_then_succeeds():
    session = StubSession([
        StubResponse({"error": "busy"}, status_code=503),
        openai_response('{"results": []}'),
    ])
    client = HttpLLMClient("http://llm.internal/v1/chat/completions", "m",
                           session=session, retry_backoff_seconds=0)
    assert client.call_tool("s", "u", TOOL, "report") == {"results": []}
    assert len(session.calls) == 2


def test_client_error_is_not_retried():
    session = StubSession([StubResponse({"error": "bad request"}, status_code=400)])
    client = HttpLLMClient("http://llm.internal/v1/chat/completions", "m",
                           session=session, retry_backoff_seconds=0)
    with pytest.raises(LLMError):
        client.call_tool("s", "u", TOOL, "report")
    assert len(session.calls) == 1


# ----------------------------------------------------------------------
def test_factory_builds_http_client():
    section = {
        "provider": "http",
        "base_url": "http://llm.internal/v1/chat/completions",
        "model": "internal-model",
        "verify_ssl": "false",
    }
    client = create_llm_client(section)
    assert isinstance(client, HttpLLMClient)
    assert client.session.verify is False
