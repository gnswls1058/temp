"""CLI 인자 위치 테스트.

`-c` 를 서브커맨드 앞에만 쓸 수 있으면 사용자가 반드시 한 번은 틀린다.
실제로 내부망 첫 실행에서 `python -m app.main collect -c config.yaml` 이
"unrecognized arguments" 로 실패했다. 양쪽 위치를 모두 받아야 한다.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.main import _build_parser  # noqa: E402


@pytest.mark.parametrize("argv", [
    ["-c", "my.yaml", "collect"],
    ["collect", "-c", "my.yaml"],
    ["-c", "my.yaml", "build"],
    ["build", "-c", "my.yaml"],
    ["-c", "my.yaml", "rebuild"],
    ["rebuild", "-c", "my.yaml"],
    ["-c", "my.yaml", "runs"],
    ["runs", "-c", "my.yaml"],
])
def test_config_option_works_in_both_positions(argv):
    args = _build_parser().parse_args(argv)
    assert args.config == "my.yaml"


def test_default_config_is_used_when_omitted():
    args = _build_parser().parse_args(["collect"])
    assert args.config == "config.yaml"


def test_subcommand_option_overrides_leading_one():
    """둘 다 주면 뒤에 준 값이 이긴다. 사용자가 마지막에 쓴 것을 의도로 본다."""
    args = _build_parser().parse_args(["-c", "first.yaml", "collect", "-c", "second.yaml"])
    assert args.config == "second.yaml"


def test_build_flags_still_work_with_trailing_config():
    args = _build_parser().parse_args(["build", "--skip-llm", "-c", "my.yaml"])
    assert args.config == "my.yaml"
    assert args.skip_llm is True


# ----------------------------------------------------------------------
def test_gold_set_accepts_both_positions():
    import argparse
    import importlib

    module = importlib.import_module("tools.gold_set")
    # gold_set 은 main() 안에서 parser 를 만든다. 같은 구조인지만 확인한다.
    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "parents=[common]" in source, "서브커맨드에 -c 를 붙이지 않았다"
    assert "default=argparse.SUPPRESS" in source
