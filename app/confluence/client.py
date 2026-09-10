"""Confluence REST API client (§7).

- Cloud(v2)와 Server/Data Center(v1)를 모두 지원한다.
  * v2: ``/api/v2/pages``, ``_links.next`` cursor 기반 pagination
  * v1: ``/rest/api/content``, ``start``/``limit`` offset 기반 pagination
  사내 Confluence 는 대부분 Server/DC 이므로 ``api_version: v1`` 이 필요하다.
- 401 / 403 / 404 / 429 / 5xx 를 구분하여 로그를 남긴다.
- 429 는 Retry-After 를 존중하고, 5xx 와 네트워크 오류는 지수 백오프로 재시도한다.
"""
from __future__ import annotations

import logging
import random
import time
from typing import Any, Dict, Iterator, List, Optional
from urllib.parse import urljoin, urlparse, parse_qs

import requests
from requests.auth import HTTPBasicAuth

logger = logging.getLogger(__name__)


class ConfluenceError(RuntimeError):
    """재시도로 해결되지 않는 Confluence API 오류."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class ConfluenceAuthError(ConfluenceError):
    """401 / 403."""


class ConfluenceNotFoundError(ConfluenceError):
    """404."""


class ConfluenceClient:
    def __init__(
        self,
        base_url: str,
        email: str,
        api_token: str,
        *,
        timeout: int = 30,
        max_retries: int = 5,
        backoff_base: float = 1.0,
        backoff_max: float = 60.0,
        session: Optional[requests.Session] = None,
        api_version: str = "v2",
        verify_ssl: bool = True,
        extra_headers: Optional[Dict[str, str]] = None,
    ):
        if not base_url:
            raise ValueError("confluence.base_url 이 설정되지 않았습니다.")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max

        version = str(api_version).lower().strip()
        if version not in ("v1", "v2"):
            raise ValueError(
                f"confluence.api_version 은 v1 또는 v2 여야 합니다: {api_version}"
            )
        self.api_version = version

        self.session = session or requests.Session()
        # 사내 인증서(사설 CA)를 쓰는 환경에서는 검증을 끄거나 CA 번들 경로를 준다.
        self.session.verify = verify_ssl
        if email and api_token:
            self.session.auth = HTTPBasicAuth(email, api_token)
        elif api_token:
            # PAT (Server/DC) 방식 지원
            self.session.headers["Authorization"] = f"Bearer {api_token}"
        self.session.headers.update({"Accept": "application/json"})
        if extra_headers:
            self.session.headers.update(extra_headers)

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------
    def _request(self, method: str, path_or_url: str,
                 params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = (
            path_or_url
            if path_or_url.startswith("http")
            else urljoin(self.base_url + "/", path_or_url.lstrip("/"))
        )

        last_error: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = self.session.request(
                    method, url, params=params, timeout=self.timeout
                )
            except requests.RequestException as exc:
                last_error = exc
                delay = self._backoff(attempt)
                logger.warning(
                    "Confluence 네트워크 오류 (%s/%s): %s — %.1fs 후 재시도",
                    attempt, self.max_retries, exc, delay,
                )
                time.sleep(delay)
                continue

            status = response.status_code

            if 200 <= status < 300:
                if not response.content:
                    return {}
                return response.json()

            if status in (401, 403):
                logger.error(
                    "Confluence 인증/권한 오류 %s: %s (%s)", status, url, response.text[:300]
                )
                raise ConfluenceAuthError(
                    f"인증 또는 권한 오류 ({status}). API token / 계정 권한을 확인하세요.", status
                )

            if status == 404:
                logger.error("Confluence 리소스 없음 404: %s", url)
                raise ConfluenceNotFoundError(f"리소스를 찾을 수 없습니다: {url}", status)

            if status == 429:
                retry_after = response.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.isdigit() \
                    else self._backoff(attempt)
                logger.warning(
                    "Confluence rate limit 429 (%s/%s): %.1fs 대기",
                    attempt, self.max_retries, delay,
                )
                time.sleep(delay)
                last_error = ConfluenceError("rate limited", status)
                continue

            if status >= 500:
                delay = self._backoff(attempt)
                logger.warning(
                    "Confluence 서버 오류 %s (%s/%s): %.1fs 후 재시도",
                    status, attempt, self.max_retries, delay,
                )
                time.sleep(delay)
                last_error = ConfluenceError(f"server error {status}", status)
                continue

            logger.error("Confluence 예상치 못한 응답 %s: %s", status, response.text[:300])
            raise ConfluenceError(f"예상치 못한 응답 {status}: {response.text[:300]}", status)

        raise ConfluenceError(
            f"재시도 {self.max_retries}회 실패: {url} ({last_error})"
        )

    def _backoff(self, attempt: int) -> float:
        delay = min(self.backoff_base * (2 ** (attempt - 1)), self.backoff_max)
        return delay + random.uniform(0, delay * 0.25)  # jitter

    # ------------------------------------------------------------------
    # API
    # ------------------------------------------------------------------
    @property
    def is_v1(self) -> bool:
        return self.api_version == "v1"

    def resolve_space_ids(self, space_keys: List[str]) -> List[str]:
        """v1 은 space key 를 그대로 쓴다. v2 만 숫자 id 로 바꾼다."""
        if self.is_v1:
            return [str(k) for k in space_keys if str(k).strip()]
        return self._resolve_space_ids_v2(space_keys)

    def _resolve_space_ids_v2(self, space_keys: List[str]) -> List[str]:
        """space key 목록을 space id 로 변환한다."""
        if not space_keys:
            return []
        ids: List[str] = []
        for key in space_keys:
            data = self._request("GET", "/api/v2/spaces", {"keys": key, "limit": 1})
            results = data.get("results") or []
            if not results:
                logger.warning("Space key 를 찾을 수 없습니다: %s", key)
                continue
            ids.append(str(results[0]["id"]))
            logger.info("Space %s -> id %s", key, results[0]["id"])
        return ids

    def iter_pages(
        self,
        space_ids: Optional[List[str]] = None,
        *,
        limit: int = 50,
        body_format: str = "storage",
    ) -> Iterator[Dict[str, Any]]:
        """대상 페이지를 pagination 끝까지 순회한다.

        Confluence Cloud v2 는 ``_links.next`` cursor 로 페이징한다.
        응답 한 번만 조회하고 끝내지 않는다 (§7).
        """
        if self.is_v1:
            for item in self._iter_pages_v1(space_ids, limit, body_format):
                yield item
            return

        params: Dict[str, Any] = {"limit": limit, "body-format": body_format}
        if space_ids:
            params["space-id"] = ",".join(str(s) for s in space_ids)

        path = "/api/v2/pages"
        page_no = 0
        while True:
            data = self._request("GET", path, params)
            results = data.get("results") or []
            page_no += 1
            logger.info("Confluence page batch %s: %s건", page_no, len(results))
            for item in results:
                yield item

            next_link = (data.get("_links") or {}).get("next")
            if not next_link:
                break
            # next 링크에는 cursor 가 포함되어 있으므로 params 를 cursor 로 교체한다.
            cursor = self._extract_cursor(next_link)
            if not cursor:
                break
            params = {"limit": limit, "body-format": body_format, "cursor": cursor}
            if space_ids:
                params["space-id"] = ",".join(str(s) for s in space_ids)

    def _iter_pages_v1(self, space_keys: Optional[List[str]], limit: int,
                       body_format: str) -> Iterator[Dict[str, Any]]:
        """Server/DC v1 은 space 별로 offset pagination 을 돈다.

        v1 에는 여러 space 를 한 번에 거르는 파라미터가 없어서 space 마다 돈다.
        space 를 지정하지 않으면 전체를 순회한다.
        """
        expand = f"body.{body_format},version,ancestors"
        targets: List[Optional[str]] = (
            [str(k) for k in space_keys] if space_keys else [None]
        )
        for space_key in targets:
            start, page_no = 0, 0
            while True:
                params: Dict[str, Any] = {
                    "type": "page", "expand": expand,
                    "start": start, "limit": limit,
                }
                if space_key:
                    params["spaceKey"] = space_key
                data = self._request("GET", "/rest/api/content", params)
                results = data.get("results") or []
                page_no += 1
                logger.info(
                    "Confluence(v1) %s batch %s: %s건",
                    space_key or "전체", page_no, len(results),
                )
                for item in results:
                    yield item

                # v1 은 _links.next 가 있으면 더 있고, 없으면 끝이다.
                if not (data.get("_links") or {}).get("next"):
                    break
                start += int(data.get("limit") or limit) or limit

    @staticmethod
    def _extract_cursor(next_link: str) -> Optional[str]:
        query = parse_qs(urlparse(next_link).query)
        values = query.get("cursor")
        return values[0] if values else None

    def get_page(self, page_id: str, body_format: str = "storage") -> Dict[str, Any]:
        if self.is_v1:
            return self._request(
                "GET", f"/rest/api/content/{page_id}",
                {"expand": f"body.{body_format},version,ancestors"},
            )
        return self._request(
            "GET", f"/api/v2/pages/{page_id}", {"body-format": body_format}
        )

    def get_labels(self, page_id: str) -> List[str]:
        try:
            path = (
                f"/rest/api/content/{page_id}/label" if self.is_v1
                else f"/api/v2/pages/{page_id}/labels"
            )
            data = self._request("GET", path, {"limit": 50})
        except ConfluenceError as exc:
            logger.debug("라벨 조회 실패 page=%s: %s", page_id, exc)
            return []
        return [item.get("name", "") for item in (data.get("results") or []) if item.get("name")]
