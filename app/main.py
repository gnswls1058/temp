"""CLI 진입점.

사용 예::

    python -m app.main build                  # 전체 파이프라인 실행
    python -m app.main build --skip-collect   # 로컬 캐시로 재빌드
    python -m app.main build --skip-llm       # 후보 생성까지만
    python -m app.main collect                # Confluence 수집만
    python -m app.main rebuild                # DB 상태로 사전 JSON 재생성
    python -m app.main runs                   # 최근 실행 이력
"""
from __future__ import annotations

import argparse
import json
import sys


def _force_utf8_console() -> None:
    """Windows 콘솔 기본 인코딩(cp949)에서 한글 로그가 깨지는 것을 막는다.

    UnicodeEncodeError 로 파이프라인이 중단되는 사고가 실제로 자주 난다.
    Python 3.7+ 의 reconfigure 를 쓰고, 실패하면 조용히 넘어간다.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # pragma: no cover - 리다이렉트된 스트림 등
            pass


_force_utf8_console()

from app.config.settings import configure_logging, load_settings  # noqa: E402
from app.pipeline.term_dictionary_pipeline import (  # noqa: E402
    PipelineOptions,
    TermDictionaryPipeline,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="term-dictionary",
        description="Confluence 기반 사내 Term Dictionary 생성 파이프라인 (V1)",
    )
    parser.add_argument("-c", "--config", default="config.yaml", help="설정 파일 경로")

    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="전체 파이프라인 실행")
    build.add_argument("--skip-collect", action="store_true",
                       help="Confluence 수집 없이 로컬 캐시로 재빌드")
    build.add_argument("--skip-llm", action="store_true",
                       help="LLM 검증 없이 Candidate 생성까지만 수행")
    build.add_argument("--skip-classification", action="store_true",
                       help="Term 유형 분류 단계를 생략")

    sub.add_parser("collect", help="Confluence 문서 수집만 수행")
    sub.add_parser("rebuild", help="DB 상태로 Term Dictionary JSON 재생성")
    sub.add_parser("runs", help="최근 indexing run 이력 조회")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    settings = load_settings(args.config)
    logger = configure_logging(settings)

    options = PipelineOptions(
        skip_collect=getattr(args, "skip_collect", False),
        skip_llm=getattr(args, "skip_llm", False),
        skip_classification=getattr(args, "skip_classification", False),
        config_path=args.config,
    )
    pipeline = TermDictionaryPipeline(settings, options)

    try:
        if args.command == "build":
            run = pipeline.build_term_dictionary()
            return 0 if run.status == "COMPLETED" else 1

        if args.command == "collect":
            from app.models.relation import IndexingRun
            from datetime import datetime, timezone

            run = IndexingRun(
                run_id=datetime.now().strftime("%Y%m%d-%H%M%S-collect"),
                started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            )
            pipeline.runs.start(run)
            result = pipeline.collect_documents(run)
            logger.info(
                "수집 결과 - 조회 %s, 신규 %s, 갱신 %s, 변경없음 %s, 실패 %s",
                result.total_seen, result.created, result.updated,
                result.unchanged, result.failed,
            )
            return 0

        if args.command == "rebuild":
            builder = pipeline._dictionary_builder()
            path = builder.rebuild()
            builder.export_review_queue()
            logger.info("사전 재생성 완료: %s", path)
            return 0

        if args.command == "runs":
            runs = pipeline.runs.list_recent(10)
            print(json.dumps([r.__dict__ for r in runs], ensure_ascii=False, indent=2))
            return 0

        return 1
    except KeyboardInterrupt:
        logger.warning("사용자에 의해 중단되었습니다.")
        return 130
    except Exception as exc:
        logger.error("실행 실패: %s", exc)
        return 1
    finally:
        pipeline.close()


if __name__ == "__main__":
    sys.exit(main())
