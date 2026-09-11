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


# ----------------------------------------------------------------------
# Anthropic Messages API 규격 (vLLM 의 /v1/messages 등)
#
# 이 규격은 system 을 messages 배열이 아니라 최상위 필드로 받는다.
# messages 의 role 은 user / assistant 만 허용한다. OpenAI 형식으로 보내면
# 400 "Input should be 'user' or 'assistant'" 로 거부당한다.
def anthropic_response(text):
    return StubResponse({"content": [{"type": "text", "text": text}]})


def test_anthropic_format_puts_system_at_top_level():
    session = StubSession([anthropic_response('{"results": []}')])
    client = HttpLLMClient("http://llm.internal/v1/messages", "gemma4-31b",
                           session=session, request_format="anthropic")

    assert client.call_tool("시스템", "사용자", TOOL, "report") == {"results": []}

    body = session.calls[0]["body"]
    assert "system" in body                       # 최상위 필드
    assert body["system"].startswith("시스템")
    assert [m["role"] for m in body["messages"]] == ["user"]   # system role 없음
    assert body["max_tokens"] > 0


def test_anthropic_default_response_path():
    """규격마다 응답 위치가 다르다. 지정하지 않으면 기본값을 쓴다."""
    client = HttpLLMClient("http://x/v1/messages", "m", request_format="anthropic",
                           session=StubSession([]))
    assert client.response_path == "content.0.text"

    client = HttpLLMClient("http://x/v1/chat/completions", "m",
                           request_format="openai", session=StubSession([]))
    assert client.response_path == "choices.0.message.content"


def test_merge_system_into_user_for_servers_without_system_role():
    session = StubSession([openai_response('{"results": []}')])
    client = HttpLLMClient("http://llm.internal/v1/chat/completions", "m",
                           session=session, merge_system_into_user=True)
    client.call_tool("시스템지시", "사용자입력", TOOL, "report")

    messages = session.calls[0]["body"]["messages"]
    assert [m["role"] for m in messages] == ["user"]
    assert messages[0]["content"].startswith("시스템지시")
    assert "사용자입력" in messages[0]["content"]


def test_unknown_request_format_lists_valid_values():
    with pytest.raises(LLMError) as exc:
        HttpLLMClient("http://x", "m", request_format="messages")
    assert "anthropic" in str(exc.value)


def test_unknown_provider_lists_valid_values():
    with pytest.raises(LLMError) as exc:
        create_llm_client({"provider": "mycompany-llm"})
    message = str(exc.value)
    assert "http" in message and "openai" in message


@pytest.mark.parametrize("provider", ["http", "internal", "openai", "vllm", "custom"])
def test_common_provider_aliases_are_accepted(provider):
    client = create_llm_client({
        "provider": provider,
        "base_url": "http://llm.internal/v1/messages",
        "model": "m",
    })
    assert isinstance(client, HttpLLMClient)
