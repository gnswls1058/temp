"""사내 LLM 서버용 REST Client (§36, §51).

외부 인터넷이 없는 내부망에서는 Anthropic SDK 를 쓸 수 없다. 사내 LLM 서버에
HTTP 로 질의하고 구조화된 JSON 을 돌려받는 경로를 따로 둔다.

전제:
- 서버가 tool-use(function calling)를 지원하지 않을 수 있다. 그래서 도구 스키마를
  프롬프트에 넣고 "JSON 만 반환하라"고 지시한 뒤 응답 본문에서 JSON 을 파싱한다.
- 응답에 설명 문장이 섞여 나올 수 있으므로 코드펜스와 앞뒤 군더더기를 걷어낸다.
- 파싱에 실패하면 LLMError 를 던진다. 파이프라인은 배치 단위로 실패를 흡수한다 (§51).

요청 형식은 두 가지를 지원한다.
- ``openai``  : ``{"model", "messages":[{"role","content"}], "temperature", "max_tokens"}``
                응답은 ``choices[0].message.content``
- ``anthropic``: ``{"model", "max_tokens", "system", "messages":[{"role":"user"}]}``
                Anthropic Messages API 규격. system 을 messages 가 아니라 최상위
                필드로 받는다. vLLM 의 /v1/messages 엔드포인트가 이 형식이다.
                응답은 ``content[0].text``
- ``plain``   : ``{"model", "system", "prompt", ...}`` 처럼 단순한 사내 규격.
                요청 키와 응답 경로를 설정으로 지정한다.

system 역할 자체를 받지 않는 서버(Gemma 계열 등)를 위해 ``merge_system_into_user``
옵션을 둔다. 켜면 system 지시문을 사용자 메시지 앞에 붙여 하나로 보낸다.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Sequence

from app.validation.llm_client import LLMClient, LLMError

logger = logging.getLogger(__name__)

# ```json ... ``` 코드펜스
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)

_JSON_INSTRUCTION = """
[출력 형식 - 반드시 지킬 것]
아래 JSON Schema 를 만족하는 JSON 객체 **하나만** 출력한다.
설명 문장, 머리말, 코드펜스 표기를 붙이지 않는다.

{schema}
""".strip()


def extract_json(text: str) -> Dict[str, Any]:
    """모델 응답에서 JSON 객체를 꺼낸다.

    코드펜스로 감싸거나 앞뒤에 설명을 붙이는 경우가 흔하므로 관대하게 처리한다.
    """
    if not text or not text.strip():
        raise LLMError("LLM 응답이 비어 있습니다.")

    candidates: List[str] = []
    fence = _FENCE_RE.search(text)
    if fence:
        candidates.append(fence.group(1))
    candidates.append(text)

    for candidate in candidates:
        candidate = candidate.strip()
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
        # 앞뒤에 군더더기가 붙은 경우 가장 바깥 중괄호만 잘라 다시 시도한다.
        start, end = candidate.find("{"), candidate.rfind("}")
        if start != -1 and end > start:
            try:
                parsed = json.loads(candidate[start:end + 1])
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                continue

    preview = text.strip()[:200].replace("\n", " ")
    raise LLMError(f"LLM 응답에서 JSON 을 찾지 못했습니다: {preview}")


def dig(payload: Any, path: str) -> Any:
    """``choices.0.message.content`` 같은 경로로 응답에서 값을 꺼낸다."""
    node = payload
    for part in path.split("."):
        if part == "":
            continue
        if isinstance(node, list):
            try:
                node = node[int(part)]
            except (ValueError, IndexError) as exc:
                raise LLMError(f"응답 경로 '{path}' 를 찾을 수 없습니다.") from exc
        elif isinstance(node, dict):
            if part not in node:
                raise LLMError(f"응답 경로 '{path}' 를 찾을 수 없습니다. 키: {sorted(node)}")
            node = node[part]
        else:
            raise LLMError(f"응답 경로 '{path}' 를 찾을 수 없습니다.")
    return node


class HttpLLMClient(LLMClient):
    """사내 LLM 서버에 HTTP 로 질의한다."""

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        api_key: str = "",
        auth_header: str = "Authorization",
        auth_prefix: str = "Bearer ",
        request_format: str = "openai",
        response_path: str = "",
        merge_system_into_user: bool = False,
        system_key: str = "system",
        prompt_key: str = "prompt",
        max_tokens: int = 4096,
        temperature: float = 0.0,
        timeout: int = 120,
        max_retries: int = 3,
        retry_backoff_seconds: float = 2.0,
        verify_ssl: Any = True,
        extra_headers: Optional[Dict[str, str]] = None,
        extra_body: Optional[Dict[str, Any]] = None,
        session: Any = None,
    ):
        if not base_url:
            raise LLMError("llm_validation.base_url 이 설정되지 않았습니다.")
        try:
            import requests
        except ImportError as exc:  # pragma: no cover
            raise LLMError("requests 가 설치되지 않았습니다.") from exc

        self.base_url = base_url
        self.model = model
        self.request_format = str(request_format).lower()
        if self.request_format not in ("openai", "anthropic", "plain"):
            raise LLMError(
                "llm_validation.request_format 은 openai / anthropic / plain 중 "
                f"하나여야 합니다: {request_format}"
            )
        # 규격마다 응답에서 본문을 꺼내는 위치가 다르다. 지정하지 않으면 기본값을 쓴다.
        self.response_path = response_path or {
            "openai": "choices.0.message.content",
            "anthropic": "content.0.text",
            "plain": "choices.0.message.content",
        }[self.request_format]
        self.merge_system_into_user = merge_system_into_user
        self.system_key = system_key
        self.prompt_key = prompt_key
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.timeout = timeout
        self.max_retries = max(1, max_retries)
        self.retry_backoff_seconds = retry_backoff_seconds
        self.extra_body = dict(extra_body or {})

        self.session = session or requests.Session()
        self.session.verify = verify_ssl
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if api_key:
            headers[auth_header] = f"{auth_prefix}{api_key}"
        headers.update(extra_headers or {})
        self.session.headers.update(headers)

    # ------------------------------------------------------------------
    def call_tool(self, system: str, user_prompt: str, tool: Dict[str, Any],
                  tool_name: str) -> Dict[str, Any]:
        schema = json.dumps(tool.get("input_schema", {}), ensure_ascii=False, indent=2)
        instruction = _JSON_INSTRUCTION.format(schema=schema)
        full_system = f"{system}\n\n{instruction}"

        body = self._build_body(full_system, user_prompt)
        text = self._post(body)
        return extract_json(text)

    def _build_body(self, system: str, user_prompt: str) -> Dict[str, Any]:
        if self.merge_system_into_user:
            # system 역할을 아예 받지 않는 서버용. 지시문을 사용자 메시지에 붙인다.
            user_prompt = f"{system}\n\n---\n\n{user_prompt}"
            system = ""

        if self.request_format == "anthropic":
            # Anthropic Messages API: system 은 최상위 필드다.
            # messages 의 role 은 user / assistant 만 허용된다.
            body: Dict[str, Any] = {
                "model": self.model,
                "max_tokens": self.max_tokens,
                "temperature": self.temperature,
                "messages": [{"role": "user", "content": user_prompt}],
            }
            if system:
                body["system"] = system
            body.update(self.extra_body)
            return body

        if self.request_format == "openai":
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": user_prompt})
            body = {
                "model": self.model,
                "messages": messages,
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
            }
        else:
            body = {
                "model": self.model,
                self.system_key: system,
                self.prompt_key: user_prompt,
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
            }
        body.update(self.extra_body)
        return body

    def _post(self, body: Dict[str, Any]) -> str:
        import requests

        last_error: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = self.session.post(
                    self.base_url, json=body, timeout=self.timeout
                )
            except requests.RequestException as exc:
                last_error = exc
                logger.warning("LLM 서버 통신 실패 (%s/%s): %s",
                               attempt, self.max_retries, exc)
            else:
                if response.status_code == 200:
                    payload = response.json()
                    value = dig(payload, self.response_path)
                    if not isinstance(value, str):
                        value = json.dumps(value, ensure_ascii=False)
                    return value
                if response.status_code in (429,) or response.status_code >= 500:
                    last_error = LLMError(
                        f"LLM 서버 오류 {response.status_code}: {response.text[:200]}"
                    )
                    logger.warning("LLM 서버 %s (%s/%s)",
                                   response.status_code, attempt, self.max_retries)
                else:
                    # 4xx 는 재시도해도 같은 결과다.
                    raise LLMError(
                        f"LLM 서버 오류 {response.status_code}: {response.text[:300]}"
                    )
            if attempt < self.max_retries:
                time.sleep(self.retry_backoff_seconds * attempt)

        raise LLMError(f"LLM 서버 호출이 {self.max_retries}회 모두 실패했습니다: {last_error}")
