"""외부망에서 내부망 반입용 wheel 을 내려받는다.

내부망에는 인터넷이 없으므로 pip 이 패키지를 가져올 수 없다. 반입 전에
**내부망과 같은 OS/파이썬 버전**의 wheel 을 미리 모아 가야 한다.

중요: wheel 은 (OS, CPU, 파이썬 버전)마다 다르다. 내부망이 Windows/amd64 이고
Python 3.11 이면 그에 맞는 wheel 을 받아야 한다. macOS 에서 그냥 받으면
macOS 용이 받아져 내부망에서 설치되지 않는다.

사용(외부망에서)::

    python tools/download_wheels.py --python-version 311
    python tools/download_wheels.py --python-version 311 --optional
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="내부망 반입용 wheel 내려받기")
    parser.add_argument("--python-version", default="311",
                        help="내부망 파이썬 버전. 예: 39, 310, 311, 312, 313")
    parser.add_argument("--platform", default="win_amd64",
                        help="내부망 플랫폼 태그. 기본은 Windows 64bit")
    parser.add_argument("--dest", default="wheels", help="저장 폴더")
    parser.add_argument("--optional", action="store_true",
                        help="선택 의존성(gensim/numpy 등)까지 받는다")
    args = parser.parse_args()

    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)

    files = [ROOT / "requirements-internal.txt"]
    if args.optional:
        files.append(ROOT / "requirements-internal-optional.txt")

    for requirement in files:
        if not requirement.exists():
            print(f"[건너뜀] {requirement} 없음")
            continue
        command = [
            sys.executable, "-m", "pip", "download",
            "-r", str(requirement),
            "-d", str(dest),
            "--only-binary", ":all:",
            "--platform", args.platform,
            "--python-version", args.python_version,
        ]
        print("\n$ " + " ".join(command))
        result = subprocess.run(command)
        if result.returncode != 0:
            print(
                f"\n[실패] {requirement.name} 내려받기 실패.\n"
                "  - 순수 파이썬 패키지인데 --only-binary 로 막혔다면 해당 항목만\n"
                "    따로 `pip download --no-binary :all:` 로 sdist 를 받는다.\n"
                "  - 해당 파이썬 버전용 wheel 이 없으면 버전을 바꿔 본다."
            )
            return result.returncode

    wheels = sorted(dest.glob("*"))
    print(f"\n총 {len(wheels)}개 파일을 {dest} 에 받았다.")
    for path in wheels:
        print(f"  {path.name}")
    print(
        "\n내부망에서 설치::\n"
        "  pip install --no-index --find-links wheels -r requirements-internal.txt"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
