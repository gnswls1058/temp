"""LLM Client (§37, §51, §52).

- Anthropic Messages API 의 tool use 를 이용해 Structured JSON 출력을 강제한다.
- timeout / rate limit / invalid schema 를 제한 횟수만 재시도한다.
- dry_run 모드에서는 API 호출 없이 빈 결과를 돌려주어 파이프라인 점검이 가능하다.
"""
from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


class LLMClient(ABC):
    @abstractmethod
    def call_tool(self, system: str, user_prompt: str, tool: Dict[str, Any],
                  tool_name: str) -> Dict[str, Any]:
        """tool 입력(dict)을 반환한다. 실패 시 :class:`LLMError`."""


class DryRunLLMClient(LLMClient):
    """API 호출 없이 빈 결과를 반환한다 (§51 파이프라인 점검용)."""

    def call_tool(self, system: str, user_prompt: str, tool: Dict[str, Any],
                  tool_name: str) -> Dict[str, Any]:
        logger.info("[dry-run] LLM 호출 생략 (tool=%s, prompt %s자)", tool_name, len(user_prompt))
        return {"results": []}


class AnthropicLLMClient(LLMClient):
    def __init__(
        self,
        api_key: str,
        *,
        model: str = "claude-sonnet-5",
        max_tokens: int = 4096,
        temperature: float = 0.0,
        max_retries: int = 3,
        retry_backoff_seconds: float = 2.0,
        timeout: float = 120.0,
    ):
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY 가 설정되지 않았습니다.")
        try:
            from anthropic import Anthropic
        except ImportError as exc:  # pragma: no cover
            raise LLMError("anthropic SDK 가 설치되지 않았습니다. `pip install anthropic`") from exc

        self._client = Anthropic(api_key=api_key, timeout=timeout)
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.max_retries = max_retries
        self.retry_backoff_seconds = retry_backoff_seconds

    def call_tool(self, system: str, user_prompt: str, tool: Dict[str, Any],
                  tool_name: str) -> Dict[str, Any]:
        last_error: Optional[Exception] = None

        for attempt in range(1, self.max_retries + 1):
            try:
                response = self._client.messages.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    temperature=self.temperature,
                    system=system,
                    messages=[{"role": "user", "content": user_prompt}],
                    tools=[tool],
                    tool_choice={"type": "tool", "name": tool_name},
                )
            except Exception as exc:
                last_error = exc
                delay = self.retry_backoff_seconds * (2 ** (attempt - 1))
                logger.warning(
                    "LLM 호출 실패 (%s/%s): %s — %.1fs 후 재시도",
                    attempt, self.max_retries, exc, delay,
                )
                time.sleep(delay)
                continue

            payload = self._extract_tool_input(response, tool_name)
            if payload is not None:
                return payload

            last_error = LLMError("tool_use 블록을 찾지 못했습니다.")
            logger.warning(
                "LLM 응답에 tool_use 가 없습니다 (%s/%s). stop_reason=%s",
                attempt, self.max_retries, getattr(response, "stop_reason", None),
            )
            time.sleep(self.retry_backoff_seconds)

        raise LLMError(f"LLM 호출 {self.max_retries}회 실패: {last_error}")

    @staticmethod
    def _extract_tool_input(response, tool_name: str) -> Optional[Dict[str, Any]]:
        for block in getattr(response, "content", []) or []:
            if getattr(block, "type", None) == "tool_use" and block.name == tool_name:
                value = block.input
                return value if isinstance(value, dict) else None
        return None


def _verify_ssl(value):
    """사설 CA 환경 대응. true/false 또는 CA 번들 경로를 받는다."""
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "yes", "1"):
            return True
        if lowered in ("false", "no", "0"):
            return False
        return value
    return bool(value)


def create_llm_client(settings_section) -> LLMClient:
    """설정에 따라 LLM Client 를 생성한다."""
    if bool(settings_section.get("dry_run", False)):
        logger.warning("llm_validation.dry_run=true — LLM 검증을 수행하지 않습니다.")
        return DryRunLLMClient()

    provider = str(settings_section.get("provider", "anthropic")).lower()

    if provider in ("http", "internal", "openai_compatible"):
        # 사내 LLM 서버. 외부 인터넷이 없는 내부망에서 쓰는 경로다.
        from app.validation.http_llm_client import HttpLLMClient

        return HttpLLMClient(
            base_url=str(settings_section.get("base_url", "")),
            model=str(settings_section.get("model", "")),
            api_key=str(settings_section.get("api_key", "") or ""),
            auth_header=str(settings_section.get("auth_header", "Authorization")),
            auth_prefix=str(settings_section.get("auth_prefix", "Bearer ")),
            request_format=str(settings_section.get("request_format", "openai")),
            response_path=str(
                settings_section.get("response_path", "choices.0.message.content")
            ),
            system_key=str(settings_section.get("system_key", "system")),
            prompt_key=str(settings_section.get("prompt_key", "prompt")),
            max_tokens=int(settings_section.get("max_tokens", 4096)),
            temperature=float(settings_section.get("temperature", 0.0)),
            timeout=int(settings_section.get("timeout_seconds", 120)),
            max_retries=int(settings_section.get("max_retries", 3)),
            retry_backoff_seconds=float(
                settings_section.get("retry_backoff_seconds", 2.0)
            ),
            verify_ssl=_verify_ssl(settings_section.get("verify_ssl", True)),
            extra_headers=dict(settings_section.get("extra_headers", {}) or {}),
            extra_body=dict(settings_section.get("extra_body", {}) or {}),
        )

    if provider != "anthropic":
        raise LLMError(f"지원하지 않는 LLM provider: {provider}")

    return AnthropicLLMClient(
        api_key=str(settings_section.get("api_key", "")),
        model=str(settings_section.get("model", "claude-sonnet-5")),
        max_tokens=int(settings_section.get("max_tokens", 4096)),
        temperature=float(settings_section.get("temperature", 0.0)),
        max_retries=int(settings_section.get("max_retries", 3)),
        retry_backoff_seconds=float(settings_section.get("retry_backoff_seconds", 2.0)),
    )
