"""설정 로딩 (§56).

- YAML 파일을 읽고 ``${ENV_VAR}`` 형태를 환경변수로 치환한다.
- 모든 parameter 는 코드가 아니라 설정 파일에서 관리한다.
- 값 접근은 dot access(``settings.fasttext.window``) 또는
  ``settings.get("fasttext.window", 5)`` 로 한다.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Iterator, Mapping

import yaml

try:  # python-dotenv 는 선택 의존성
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")

DEFAULT_CONFIG_PATH = Path("config.yaml")


class Section(Mapping):
    """dict 를 dot access 로 감싼 읽기 전용 뷰."""

    def __init__(self, data: Mapping[str, Any], path: str = ""):
        self._data = dict(data)
        self._path = path

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            value = self._data[name]
        except KeyError as exc:
            full = f"{self._path}.{name}".lstrip(".")
            raise AttributeError(f"설정값이 없습니다: {full}") from exc
        return _wrap(value, f"{self._path}.{name}".lstrip("."))

    def __getitem__(self, key: str) -> Any:
        return _wrap(self._data[key], f"{self._path}.{key}".lstrip("."))

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __repr__(self) -> str:  # pragma: no cover
        return f"Section({self._path or 'root'}: {sorted(self._data)})"

    def get(self, key: str, default: Any = None) -> Any:
        """``a.b.c`` 형태의 경로 조회를 지원한다."""
        node: Any = self._data
        for part in key.split("."):
            if isinstance(node, Section):
                node = node._data
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return _wrap(node, key)

    def to_dict(self) -> dict:
        return _unwrap(self._data)


def _wrap(value: Any, path: str) -> Any:
    if isinstance(value, dict):
        return Section(value, path)
    if isinstance(value, list):
        return [_wrap(v, path) for v in value]
    return value


def _unwrap(value: Any) -> Any:
    if isinstance(value, Section):
        return _unwrap(value._data)
    if isinstance(value, dict):
        return {k: _unwrap(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_unwrap(v) for v in value]
    return value


def _expand_env(value: Any) -> Any:
    """``${VAR}`` / ``${VAR:-default}`` 치환. 미정의 변수는 빈 문자열이 된다."""
    if isinstance(value, str):
        def repl(match: re.Match) -> str:
            name, fallback = match.group(1), match.group(2)
            return os.environ.get(name, fallback if fallback is not None else "")

        return _ENV_PATTERN.sub(repl, value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


class Settings(Section):
    """루트 설정 객체."""

    def __init__(self, data: Mapping[str, Any], source: Path | None = None):
        super().__init__(data, "")
        self.__dict__["_source"] = source

    @property
    def source(self) -> Path | None:
        return self.__dict__.get("_source")

    # --- 자주 쓰는 경로 헬퍼 ---
    @property
    def db_path(self) -> Path:
        return Path(self.get("storage.db_path", "./data/term_dictionary.db"))

    @property
    def model_dir(self) -> Path:
        return Path(self.get("storage.model_dir", "./data/models"))

    @property
    def output_dir(self) -> Path:
        return Path(self.get("storage.output_dir", "./data/output"))

    def ensure_dirs(self) -> None:
        for path in (self.db_path.parent, self.model_dir, self.output_dir):
            path.mkdir(parents=True, exist_ok=True)

    def stopwords(self) -> list[str]:
        """설정에 적힌 불용어와 ``stopwords_file`` 의 내용을 합쳐 돌려준다.

        검수에서 제거한 용어를 파일로 관리하면 다음 실행부터 후보에서 빠진다.
        파일이 없으면 설정 목록만 사용한다.
        """
        words: list[str] = [str(w) for w in (self.get("stopwords", []) or [])]

        file_path = self.get("stopwords_file")
        if file_path:
            path = Path(file_path)
            if path.exists():
                for line in path.read_text(encoding="utf-8").splitlines():
                    line = line.split("#", 1)[0].strip()
                    if line:
                        words.append(line)

        return list(dict.fromkeys(words))

    def excluded_pairs(self) -> list[str]:
        """검수에서 제거한 용어 쌍(``termA||termB``) 목록.

        ``excluded_pairs_file`` 이 없으면 빈 목록을 돌려준다.
        """
        file_path = self.get("excluded_pairs_file")
        if not file_path:
            return []
        path = Path(file_path)
        if not path.exists():
            return []
        pairs = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                pairs.append(line)
        return list(dict.fromkeys(pairs))


def load_settings(path: str | os.PathLike | None = None, *, load_env: bool = True) -> Settings:
    """설정 파일을 읽어 :class:`Settings` 를 만든다."""
    if load_env and load_dotenv is not None:
        load_dotenv(override=False)

    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not config_path.exists():
        raise FileNotFoundError(f"설정 파일을 찾을 수 없습니다: {config_path}")

    with config_path.open("r", encoding="utf-8") as fp:
        raw = yaml.safe_load(fp) or {}

    if not isinstance(raw, dict):
        raise ValueError(f"설정 파일 최상위는 매핑이어야 합니다: {config_path}")

    return Settings(_expand_env(raw), config_path)


def configure_logging(settings: Settings) -> logging.Logger:
    """파일 + 콘솔 로깅 설정 (§49)."""
    level_name = str(settings.get("logging.level", "INFO")).upper()
    level = getattr(logging, level_name, logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"
    )

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)

    log_file = settings.get("logging.file")
    if log_file:
        file_path = Path(log_file)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(file_path, encoding="utf-8")
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)

    # 외부 라이브러리 로그 억제
    for noisy in ("urllib3", "gensim", "smart_open", "httpx", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    return logging.getLogger("term_dictionary")
