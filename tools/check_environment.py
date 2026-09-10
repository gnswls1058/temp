"""내부망 반입 후 가장 먼저 실행하는 환경 점검 도구.

무엇이 없는지 한 번에 알려준다. 파이프라인을 돌려 보며 하나씩 실패를 만나는
것보다 빠르다. 외부 통신을 하지 않으며, --connect 를 줘야 실제 서버에 붙는다.

사용::

    python tools/check_environment.py                       # 로컬 점검만
    python tools/check_environment.py -c config.internal.yaml --connect
"""
from __future__ import annotations

import argparse
import importlib
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OK, WARN, FAIL = "[ OK ]", "[경고]", "[실패]"

# (import 이름, 설치 이름, 필수 여부, 없을 때 무슨 일이 생기는가)
PACKAGES = [
    ("yaml", "PyYAML", True, "설정 파일을 읽을 수 없어 실행 자체가 안 된다"),
    ("requests", "requests", True, "Confluence / LLM 서버에 붙을 수 없다"),
    ("konlpy", "konlpy", True, "형태소 분석 불가. 한국어 용어 추출이 사실상 불가능하다"),
    ("jpype", "JPype1", True, "konlpy 가 Komoran(Java)을 띄우지 못한다"),
    ("pytest", "pytest", False, "반입 후 자체 테스트를 돌릴 수 없다"),
    ("lxml", "lxml", True, "konlpy 가 요구하는 필수 의존성이다"),
    ("numpy", "numpy", True, "konlpy 가 요구하는 필수 의존성이다"),
    ("gensim", "gensim", False, "fasttext.enabled=false 로 두면 없어도 된다"),
    ("bs4", "beautifulsoup4", False, "cleaning.output_format=markdown 이면 없어도 된다"),
    ("dotenv", "python-dotenv", False, ".env 대신 환경변수를 직접 설정하면 된다"),
    ("anthropic", "anthropic", False, "사내 LLM(provider: http)만 쓰면 필요 없다"),
]


class Report:
    def __init__(self) -> None:
        self.failures = 0
        self.warnings = 0

    def line(self, mark: str, title: str, detail: str = "") -> None:
        if mark == FAIL:
            self.failures += 1
        elif mark == WARN:
            self.warnings += 1
        print(f"  {mark} {title}")
        if detail:
            for row in detail.splitlines():
                print(f"         {row}")


def check_python(report: Report) -> None:
    version = sys.version_info
    text = f"Python {version.major}.{version.minor}.{version.micro} ({platform.machine()})"
    if version < (3, 9):
        report.line(FAIL, text, "3.9 이상이 필요하다.")
    elif version >= (3, 14):
        report.line(WARN, text, "3.9~3.13 에서 검증했다. 라이브러리 휠이 없을 수 있다.")
    else:
        report.line(OK, text)


def check_packages(report: Report) -> None:
    for module, package, required, impact in PACKAGES:
        try:
            loaded = importlib.import_module(module)
        except Exception as exc:
            mark = FAIL if required else WARN
            report.line(mark, f"{package} 없음", f"{impact}\n설치: pip install {package}\n({exc})")
            continue
        version = getattr(loaded, "__version__", "")
        report.line(OK, f"{package} {version}".strip())


def check_jvm_library(report: Report) -> None:
    """JPype 가 실제로 로드할 JVM 라이브러리를 확인한다.

    Windows 에서 자주 나는 두 가지 사고를 여기서 잡는다.
      1) 32bit JDK + 64bit Python  -> 아키텍처 불일치로 JVM 로드 실패
      2) JAVA_HOME 이 JRE 나 상위 폴더를 가리켜 jvm.dll 을 못 찾는 경우
    """
    try:
        import jpype
    except ImportError:
        return  # 패키지 점검에서 이미 보고했다

    try:
        jvm_path = jpype.getDefaultJVMPath()
    except Exception as exc:
        report.line(
            FAIL, "JVM 라이브러리를 찾지 못했다",
            f"{exc}\n"
            "JAVA_HOME 이 JDK 최상위 폴더를 가리키는지 확인한다.\n"
            "Windows 기준으로 %JAVA_HOME%\\bin\\server\\jvm.dll 이 있어야 한다.",
        )
        return

    report.line(OK, f"JVM 라이브러리: {jvm_path}")

    if os.name != "nt":
        return
    # Windows 에서 비트수가 어긋나면 실행 순간에야 깨진다. 미리 잡는다.
    python_bits = 64 if sys.maxsize > 2 ** 32 else 32
    lowered = jvm_path.lower()
    jvm_bits = 32 if ("\\jre\\bin\\client\\" in lowered or "\\x86\\" in lowered) else 64
    if python_bits != jvm_bits:
        report.line(
            FAIL, f"비트수 불일치: Python {python_bits}bit vs JVM {jvm_bits}bit",
            "둘 다 64bit 여야 한다. 64bit JDK 를 설치하고 JAVA_HOME 을 그쪽으로 바꾼다.",
        )
    else:
        report.line(OK, f"비트수 일치: Python {python_bits}bit / JVM {jvm_bits}bit")


def check_java(report: Report) -> None:
    """Komoran 은 JVM 위에서 돈다. JAVA_HOME 또는 PATH 의 java 가 필요하다.

    JDK 8 이상이면 되고, 이 프로젝트는 JDK 17(LTS)에서 검증했다.
    """
    java_home = os.environ.get("JAVA_HOME", "")
    candidate = None
    if java_home:
        exe = "java.exe" if os.name == "nt" else "java"
        path = Path(java_home) / "bin" / exe
        candidate = str(path) if path.exists() else None
        if candidate is None:
            report.line(WARN, f"JAVA_HOME 이 가리키는 곳에 java 가 없다: {java_home}")
    if candidate is None:
        candidate = shutil.which("java")

    if not candidate:
        report.line(
            FAIL, "Java 를 찾을 수 없다",
            "Komoran 실행에 JDK 8 이상이 필요하다.\n"
            "JDK 설치 후 JAVA_HOME 을 설정하거나 PATH 에 java 를 넣어야 한다.",
        )
        return
    try:
        proc = subprocess.run([candidate, "-version"], capture_output=True, text=True, timeout=20)
        output = (proc.stderr or proc.stdout)
        first = output.splitlines()[0].strip()

        major = _java_major(first)
        if major is not None and major < 8:
            report.line(FAIL, f"Java {major} 는 너무 낮다: {first}",
                        "JDK 8 이상이 필요하다. 17(LTS) 을 권장한다.")
            return
        if "64-bit" not in output and os.name == "nt":
            report.line(WARN, f"Java: {first}",
                        f"{candidate}\n64bit JVM 인지 확인이 필요하다.")
            return
        report.line(OK, f"Java: {first}", candidate)
    except Exception as exc:
        report.line(WARN, f"Java 버전 확인 실패: {exc}", candidate)


def _java_major(version_line: str) -> Optional[int]:
    """`openjdk version "17.0.4.1"` 같은 줄에서 메이저 버전을 뽑는다."""
    import re

    match = re.search(r'"(\d+)(?:\.(\d+))?', version_line)
    if not match:
        return None
    first = int(match.group(1))
    # 1.8.0_xxx 형태는 8 이 메이저다
    if first == 1 and match.group(2):
        return int(match.group(2))
    return first


def check_komoran(report: Report) -> None:
    try:
        from app.preprocessing.komoran_processor import KomoranProcessor
    except Exception as exc:
        report.line(FAIL, "형태소 분석기 모듈 로드 실패", str(exc))
        return
    try:
        processor = KomoranProcessor(keep_pos=["NNG", "NNP", "SL"])
        tokens = processor.tokenize("결제 취소 정책을 확인합니다")
    except Exception as exc:
        report.line(
            FAIL, "Komoran 실행 실패",
            f"{exc}\nJDK 설치와 JAVA_HOME 을 확인한다.",
        )
        return
    if tokens:
        report.line(OK, f"Komoran 동작 확인: {tokens}")
    else:
        report.line(WARN, "Komoran 이 토큰을 반환하지 않았다")


def check_config(report: Report, config_path: str) -> object:
    path = Path(config_path)
    if not path.exists():
        report.line(FAIL, f"설정 파일이 없다: {path}")
        return None
    try:
        from app.config.settings import load_settings
        settings = load_settings(path)
    except Exception as exc:
        report.line(FAIL, f"설정 파일을 읽지 못했다: {path}", str(exc))
        return None

    report.line(OK, f"설정 로드: {path}")
    checks = [
        ("confluence.base_url", True),
        ("confluence.api_token", True),
        ("llm_validation.base_url", False),
        ("llm_validation.model", False),
    ]
    for key, required in checks:
        value = settings.get(key, "")
        if value:
            shown = value if "token" not in key and "key" not in key else "(설정됨)"
            report.line(OK, f"{key} = {shown}")
        else:
            mark = FAIL if required else WARN
            report.line(mark, f"{key} 가 비어 있다",
                        "환경변수 또는 설정 파일에 값을 넣는다.")

    space_keys = list(settings.get("confluence.space_keys", []) or [])
    space_ids = list(settings.get("confluence.space_ids", []) or [])
    if not space_keys and not space_ids:
        report.line(WARN, "대상 space 가 지정되지 않았다",
                    "접근 가능한 전체 페이지를 수집하게 된다.")
    else:
        report.line(OK, f"대상 space: {space_keys or space_ids}")

    if str(settings.get("confluence.api_version", "v2")) == "v2" and space_ids == []:
        pass
    return settings


def check_writable(report: Report, settings) -> None:
    if settings is None:
        return
    for key in ("storage.db_path", "storage.output_dir", "storage.model_dir"):
        raw = settings.get(key, "")
        if not raw:
            continue
        target = Path(raw)
        directory = target.parent if key.endswith("db_path") else target
        try:
            directory.mkdir(parents=True, exist_ok=True)
            probe = directory / ".write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            report.line(OK, f"쓰기 가능: {directory}")
        except Exception as exc:
            report.line(FAIL, f"쓰기 불가: {directory}", str(exc))


def check_connections(report: Report, settings) -> None:
    if settings is None:
        return
    try:
        import requests  # noqa: F401
    except ImportError:
        report.line(FAIL, "requests 가 없어 연결 점검을 건너뛴다")
        return

    from app.confluence.client import ConfluenceClient, ConfluenceError

    try:
        client = ConfluenceClient(
            base_url=str(settings.get("confluence.base_url", "")),
            email=str(settings.get("confluence.email", "") or ""),
            api_token=str(settings.get("confluence.api_token", "") or ""),
            api_version=str(settings.get("confluence.api_version", "v2")),
            timeout=int(settings.get("confluence.timeout_seconds", 30)),
            max_retries=1,
        )
        keys = list(settings.get("confluence.space_keys", []) or [])
        pages = list(_take(client.iter_pages(keys or None, limit=1), 1))
        if pages:
            report.line(OK, f"Confluence 연결 성공 (예시 페이지: {pages[0].get('title', '')})")
        else:
            report.line(WARN, "Confluence 에 연결했지만 페이지가 0건이다",
                        "space key 와 계정 권한을 확인한다.")
    except ConfluenceError as exc:
        report.line(FAIL, f"Confluence 연결 실패: {exc}")
    except Exception as exc:
        report.line(FAIL, f"Confluence 연결 실패: {type(exc).__name__}: {exc}")

    section = settings.get("llm_validation", {})
    if str(section.get("provider", "")).lower() not in ("http", "internal", "openai_compatible"):
        report.line(WARN, "llm_validation.provider 가 사내 LLM(http)이 아니다")
        return
    try:
        from app.validation.llm_client import create_llm_client

        client = create_llm_client(section)
        tool = {"name": "ping", "input_schema": {
            "type": "object", "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"]}}
        result = client.call_tool(
            "너는 연결 점검용 응답기다.",
            '{"ok": true} 라는 JSON 만 출력한다.', tool, "ping",
        )
        report.line(OK, f"사내 LLM 응답 확인: {result}")
    except Exception as exc:
        report.line(FAIL, f"사내 LLM 호출 실패: {type(exc).__name__}: {exc}")


def _take(iterable, count):
    for index, item in enumerate(iterable):
        if index >= count:
            return
        yield item


def main() -> int:
    parser = argparse.ArgumentParser(description="내부망 실행 환경 점검")
    parser.add_argument("-c", "--config", default="config.internal.yaml")
    parser.add_argument("--connect", action="store_true",
                        help="Confluence / 사내 LLM 에 실제로 연결해 본다")
    args = parser.parse_args()

    report = Report()
    print("\n=== 1. 실행 환경 ===")
    check_python(report)
    print(f"  [정보] OS: {platform.system()} {platform.release()}")

    print("\n=== 2. 파이썬 패키지 ===")
    check_packages(report)

    print("\n=== 3. Java / 형태소 분석기 ===")
    check_java(report)
    check_jvm_library(report)
    check_komoran(report)

    print("\n=== 4. 설정 ===")
    settings = check_config(report, args.config)

    print("\n=== 5. 저장 경로 ===")
    check_writable(report, settings)

    if args.connect:
        print("\n=== 6. 서버 연결 ===")
        check_connections(report, settings)
    else:
        print("\n=== 6. 서버 연결 ===")
        print("  [건너뜀] --connect 를 주면 실제 연결을 확인한다.")

    print("\n" + "=" * 60)
    if report.failures:
        print(f"실패 {report.failures}건, 경고 {report.warnings}건 — 실패 항목을 먼저 해결한다.")
        return 1
    if report.warnings:
        print(f"실패 없음, 경고 {report.warnings}건 — 경고는 설정에 따라 무시해도 된다.")
        return 0
    print("모든 점검을 통과했다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
