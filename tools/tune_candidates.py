"""Candidate 임계값 튜닝 보조 도구.

FastText 유사도 분포는 corpus 규모에 따라 크게 달라진다.
`candidate.min_similarity` 를 감으로 정하지 말고 이 도구로 측정한 뒤 정한다.

사용::

    python tools/tune_candidates.py                       # 분포 + 기본 조합 비교
    python tools/tune_candidates.py --min-similarity 0.94 --min-frequency 10
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config.settings import load_settings  # noqa: E402
from app.embedding.candidate_generator import CandidateGenerator  # noqa: E402
from app.embedding.fasttext_trainer import FastTextTrainer  # noqa: E402
from app.repository.database import Database  # noqa: E402
from app.repository.term_repository import SqliteTermRepository  # noqa: E402

DEFAULT_COMBINATIONS = [
    (0.90, 5, 5),
    (0.92, 8, 3),
    (0.93, 10, 3),
    (0.94, 10, 3),
    (0.95, 10, 3),
    (0.96, 15, 3),
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Candidate 임계값 측정")
    parser.add_argument("-c", "--config", default="config.yaml")
    parser.add_argument("--min-similarity", type=float, default=None)
    parser.add_argument("--min-frequency", type=int, default=None)
    parser.add_argument("--per-term", type=int, default=None)
    parser.add_argument("--sample", type=int, default=400, help="분포 측정 표본 term 수")
    args = parser.parse_args()

    settings = load_settings(args.config, load_env=False)
    stopwords = settings.stopwords()
    delimiter = str(settings.get("phrases.delimiter", "_"))

    db = Database(settings.db_path)
    db.initialize()
    terms = SqliteTermRepository(db).list_all()

    trainer = FastTextTrainer(
        model_filename=str(settings.get("fasttext.model_filename", "fasttext.model"))
    )
    trainer.load(settings.model_dir)

    print(f"term {len(terms)}개 / FastText vocabulary {trainer.vocabulary_size}개\n")

    # --- 유사도 분포 ---
    probe = CandidateGenerator(
        top_n=10, min_similarity=0.0,
        min_term_frequency=int(settings.get("candidate.min_term_frequency", 10)),
        max_candidates_per_term=5, max_total_candidates=10 ** 9,
        stopwords=stopwords, delimiter=delimiter,
    )
    term_map = {t.term_key: t for t in terms}
    eligible = [t for t in terms if probe.is_eligible(t)]
    eligible.sort(key=lambda t: t.frequency, reverse=True)

    similarities = []
    for term in eligible[: args.sample]:
        if not trainer.has_term(term.term_key):
            continue
        for other, score in trainer.most_similar(term.term_key, 10):
            if probe._is_valid_counterpart(term.term_key, other, term_map):
                similarities.append(score)

    print(f"[유사도 분포] 표본 {len(similarities)}건 (상위 빈도 term {args.sample}개 기준)")
    for threshold in (0.96, 0.95, 0.94, 0.92, 0.90, 0.85, 0.80, 0.70):
        count = sum(1 for s in similarities if s >= threshold)
        share = count / len(similarities) * 100 if similarities else 0
        print(f"  >= {threshold:.2f} : {count:6d}건 ({share:5.1f}%)")

    # --- 조합별 후보 수 ---
    if args.min_similarity is not None:
        combinations = [(
            args.min_similarity,
            args.min_frequency or int(settings.get("candidate.min_term_frequency", 10)),
            args.per_term or int(settings.get("candidate.max_candidates_per_term", 3)),
        )]
    else:
        combinations = DEFAULT_COMBINATIONS

    print("\n[조합별 최종 후보 수]")
    print(f"  {'min_sim':>8} {'min_freq':>9} {'per_term':>9} {'후보 쌍':>8}")
    for min_similarity, min_frequency, per_term in combinations:
        generator = CandidateGenerator(
            top_n=int(settings.get("candidate.top_n", 10)),
            min_similarity=min_similarity,
            min_term_frequency=min_frequency,
            max_candidates_per_term=per_term,
            max_total_candidates=10 ** 9,
            stopwords=stopwords,
            delimiter=delimiter,
        )
        count = len(generator.generate(trainer, terms))
        print(f"  {min_similarity:>8.2f} {min_frequency:>9d} {per_term:>9d} {count:>8d}")

    print(
        "\nLLM 검증 비용은 후보 수에 비례한다. "
        "후보가 수천 건이면 임계값을 올려 먼저 줄이는 편이 낫다."
    )
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
