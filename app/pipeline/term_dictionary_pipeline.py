"""Term Dictionary Pipeline Orchestrator (§55).

이 계층은 NLP 로직을 직접 구현하지 않는다. 각 Service 를 호출하고 실행 순서만 관리한다.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Sequence

from app.collector.document_collector import DocumentCollector, SyncResult
from app.confluence.client import ConfluenceClient
from app.config.settings import Settings
from app.corpus.context_builder import ContextBuilder
from app.corpus.corpus_builder import CorpusBuilder
from app.dictionary.dictionary_builder import DictionaryBuilder, RelationPolicy
from app.embedding.candidate_generator import CandidateGenerator
from app.embedding.context_profile import ContextProfileIndex
from app.embedding.llm_candidates import LLMCandidateProposer, load_proposals
from app.embedding.lexical_candidates import LexicalCandidateFinder
from app.embedding.fasttext_trainer import FastTextTrainer, NullFastTextTrainer
from app.models.context import Sentence
from app.models.enums import CandidateStatus, RelationStatus, RunStatus
from app.models.relation import IndexingRun
from app.models.term import Term
from app.preprocessing.komoran_processor import KomoranProcessor
from app.preprocessing.pattern_normalizer import PatternNormalizer
from app.preprocessing.phrase_processor import PhraseProcessor
from app.preprocessing.sentence_processor import SentenceProcessor
from app.preprocessing.text_cleaner import TextCleaner
from app.repository.context_repository import SqliteContextRepository
from app.repository.database import Database
from app.repository.document_repository import SqliteDocumentRepository
from app.repository.relation_repository import SqliteRelationRepository
from app.repository.run_repository import SqliteRunRepository
from app.repository.term_repository import SqliteTermRepository
from app.validation.llm_client import create_llm_client
from app.validation.relation_validator import RelationValidator
from app.validation.term_classifier import TermClassifier

logger = logging.getLogger(__name__)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class PipelineOptions:
    skip_collect: bool = False        # 수집 없이 로컬 캐시로만 재빌드
    skip_llm: bool = False            # LLM 검증 생략 (후보까지만 생성)
    skip_classification: bool = False
    config_path: Optional[str] = None


class TermDictionaryPipeline:
    def __init__(self, settings: Settings, options: Optional[PipelineOptions] = None):
        self.settings = settings
        self.options = options or PipelineOptions()

        settings.ensure_dirs()
        self.db = Database(settings.db_path)
        self.db.initialize()

        self.documents = SqliteDocumentRepository(self.db)
        self.contexts = SqliteContextRepository(self.db)
        self.terms = SqliteTermRepository(self.db)
        self.relations = SqliteRelationRepository(self.db)
        self.runs = SqliteRunRepository(self.db)

        self.stopwords = settings.stopwords()

    # ==================================================================
    def build_term_dictionary(self) -> IndexingRun:
        started = time.time()
        run = self._start_indexing_run()

        try:
            sync_result = self.collect_documents(run)
            run.changed_document_count = sync_result.changed

            documents = self.documents.list_all()
            run.document_count = len(documents)
            if not documents:
                raise RuntimeError("처리할 문서가 없습니다. Confluence 수집 설정을 확인하세요.")

            sentences = self._preprocess(documents, run)
            corpus = self._build_corpus(sentences, run)
            terms = self._build_contexts(sentences, run)

            trainer = self._train_fasttext(corpus, run)
            self.profile_index = self._build_context_profile(corpus)
            candidates = self._generate_candidates(trainer, terms, run)

            if not self.options.skip_llm:
                self._validate_and_store(candidates, terms, run)
            else:
                logger.warning("skip_llm=true — LLM 검증을 건너뜁니다.")

            self._rebuild_dictionary(run)

            run.status = RunStatus.COMPLETED.value
            run.message = "정상 완료"
        except Exception as exc:
            run.status = RunStatus.FAILED.value
            run.message = f"{type(exc).__name__}: {exc}"
            logger.exception("파이프라인 실패: %s", exc)
            raise
        finally:
            run.completed_at = _utcnow()
            run.duration_seconds = round(time.time() - started, 2)
            run.error_count = self.documents.count_errors(run.run_id)
            self.runs.update(run)
            self._log_summary(run)

        return run

    # ==================================================================
    # STEP 1. 수집
    # ------------------------------------------------------------------
    def collect_documents(self, run: IndexingRun) -> SyncResult:
        if self.options.skip_collect:
            logger.info("skip_collect=true — 로컬 캐시 문서로 진행합니다.")
            return SyncResult()

        conf = self.settings.confluence
        client = ConfluenceClient(
            base_url=str(conf.get("base_url", "")),
            email=str(conf.get("email", "")),
            api_token=str(conf.get("api_token", "")),
            timeout=int(conf.get("timeout_seconds", 30)),
            max_retries=int(conf.get("max_retries", 5)),
            backoff_base=float(conf.get("backoff_base_seconds", 1.0)),
            backoff_max=float(conf.get("backoff_max_seconds", 60.0)),
            api_version=str(conf.get("api_version", "v2")),
            auth_type=str(conf.get("auth_type", "auto")),
            verify_ssl=self._verify_ssl(conf.get("verify_ssl", True)),
            extra_headers=dict(conf.get("extra_headers", {}) or {}),
        )
        collector = DocumentCollector(
            client,
            self.documents,
            space_ids=list(conf.get("space_ids", []) or []),
            space_keys=list(conf.get("space_keys", []) or []),
            page_limit=int(conf.get("page_limit", 50)),
            body_format=str(conf.get("body_format", "storage")),
        )
        return collector.sync(run.run_id)

    # ==================================================================
    # STEP 2~6. 전처리
    # ------------------------------------------------------------------
    def _preprocess(self, documents, run: IndexingRun) -> List[Sentence]:
        cleaning = self.settings.get("cleaning", {})
        cleaner = TextCleaner(
            drop_tags=list(cleaning.get("drop_tags", [])),
            block_tags=list(cleaning.get("block_tags", [])),
            keep_code_blocks=bool(cleaning.get("keep_code_blocks", False)),
            min_text_length=int(cleaning.get("min_text_length", 10)),
            output_format=str(cleaning.get("output_format", "text")),
        )
        cleaned = []
        for document in documents:
            try:
                cleaned.extend(cleaner.clean([document]))
                if document.clean_text:
                    self.documents.update_clean_text(document.page_id, document.clean_text)
            except Exception as exc:  # 문서 1건 실패로 전체 중단하지 않는다 (§50)
                self.documents.log_error(run.run_id, document.page_id, "clean", repr(exc))
        logger.info("정제 완료 문서 수: %s", len(cleaned))

        sentence_conf = self.settings.get("sentence", {})
        splitter = SentenceProcessor(
            min_length=int(sentence_conf.get("min_length", 4)),
            max_length=int(sentence_conf.get("max_length", 400)),
            abbreviation_guards=list(sentence_conf.get("abbreviation_guards", [])),
        )
        sentences = splitter.split(cleaned)
        run.sentence_count = len(sentences)

        normalizer = self._create_normalizer()
        for sentence in sentences:
            sentence.normalized_sentence = normalizer.normalize_text(sentence.original_sentence)

        komoran = self._create_komoran()
        sentences = komoran.process(sentences)
        self.verb_stems = komoran.verb_only_stems

        phrase_conf = self.settings.get("phrases", {})
        phrases = PhraseProcessor(
            min_count=int(phrase_conf.get("min_count", 3)),
            threshold=float(phrase_conf.get("threshold", 10.0)),
            enable_trigram=bool(phrase_conf.get("enable_trigram", True)),
            delimiter=str(phrase_conf.get("delimiter", "_")),
            max_vocab_size=int(phrase_conf.get("max_vocab_size", 40_000_000)),
            forbidden_tokens=list(phrase_conf.get("forbidden_tokens", [])),
            allow_fallback=bool(phrase_conf.get("allow_fallback", False)),
        )
        phrases.train(sentences)
        phrases.transform(sentences)
        phrases.save(self.settings.model_dir)
        run.phrase_count = len(phrases.learned_phrases())

        self._export_user_dictionary_candidates(phrases)
        return sentences

    def _create_normalizer(self) -> PatternNormalizer:
        conf = self.settings.get("normalization", {})
        return PatternNormalizer(
            date=bool(conf.get("date", True)),
            time=bool(conf.get("time", True)),
            number=bool(conf.get("number", True)),
            url=bool(conf.get("url", True)),
            email=bool(conf.get("email", True)),
            number_min_digits=int(conf.get("number_min_digits", 2)),
            date_year_min=int(conf.get("date_year_min", 1990)),
            date_year_max=int(conf.get("date_year_max", 2100)),
            date_context_hints=list(conf.get("date_context_hints", [])),
            require_context_hint_for_yyyymmdd=bool(
                conf.get("require_context_hint_for_yyyymmdd", False)
            ),
            protected_patterns=list(conf.get("protected_patterns", [])),
        )

    @staticmethod
    def _verify_ssl(value):
        """사설 CA 환경 대응. true/false 또는 CA 번들 경로를 받는다."""
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in ("true", "yes", "1"):
                return True
            if lowered in ("false", "no", "0"):
                return False
            return value          # CA 번들 파일 경로
        return bool(value)

    def _create_komoran(self) -> KomoranProcessor:
        conf = self.settings.get("komoran", {})
        return KomoranProcessor(
            keep_pos=list(conf.get("keep_pos", [])),
            stem_pos=list(conf.get("stem_pos", ["VV", "VA"])),
            user_dictionary_path=conf.get("user_dictionary_path"),
            min_token_length=int(conf.get("min_token_length", 1)),
            min_verb_stem_length=int(conf.get("min_verb_stem_length", 2)),
            skip_on_error=bool(conf.get("skip_on_error", True)),
            stopwords=self.stopwords,
            allow_fallback=bool(conf.get("allow_fallback", False)),
            token_delimiter=str(self.settings.get("phrases.delimiter", "_")),
        )

    def _export_user_dictionary_candidates(self, phrases: PhraseProcessor) -> None:
        """다음 회차 품질 향상을 위한 Komoran User Dictionary 후보 (§14)."""
        learned = phrases.learned_phrases()
        if not learned:
            return
        path = Path(self.settings.output_dir) / "userdic_candidates.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fp:
            for phrase in learned:
                fp.write(f"{phrase.replace('_', ' ')}\tNNP\n")
        logger.info("User Dictionary 후보 %s개 저장: %s", len(learned), path)

    # ==================================================================
    # STEP 7~8. corpus / context
    # ------------------------------------------------------------------
    def _build_corpus(self, sentences: Sequence[Sentence], run: IndexingRun) -> List[List[str]]:
        builder = CorpusBuilder()
        corpus = builder.build(sentences)
        builder.save(corpus, Path(self.settings.output_dir) / "training_corpus.txt")
        return corpus

    def _build_contexts(self, sentences: Sequence[Sentence], run: IndexingRun) -> List[Term]:
        # 전체 재빌드 정책: Context 는 매 실행마다 다시 만든다 (§7 V1 권장 정책).
        self.contexts.clear()
        builder = ContextBuilder(
            self.contexts,
            delimiter=str(self.settings.get("phrases.delimiter", "_")),
            stopwords=self.stopwords,
        )
        _, terms = builder.build(sentences)
        self.terms.upsert_many(terms)
        # 이번 corpus 에 없는 과거 term 은 후보 생성을 오염시키므로 정리한다.
        self.terms.retain_only(t.term_key for t in terms)
        run.term_count = len(terms)
        return terms

    # ==================================================================
    # STEP 9~10. FastText / Candidate
    # ------------------------------------------------------------------
    def _train_fasttext(self, corpus: Sequence[Sequence[str]], run: IndexingRun):
        conf = self.settings.get("fasttext", {})
        if not bool(conf.get("enabled", True)):
            # gensim 을 설치할 수 없는 환경(내부망)에서 임베딩 경로만 끈다.
            return NullFastTextTrainer()
        trainer = FastTextTrainer(
            vector_size=int(conf.get("vector_size", 100)),
            window=int(conf.get("window", 5)),
            min_count=int(conf.get("min_count", 2)),
            sg=int(conf.get("sg", 1)),
            epochs=int(conf.get("epochs", 20)),
            min_n=int(conf.get("min_n", 2)),
            max_n=int(conf.get("max_n", 5)),
            workers=int(conf.get("workers", 4)),
            seed=int(conf.get("seed", 42)),
            model_filename=str(conf.get("model_filename", "fasttext.model")),
        )
        trainer.train(corpus)
        trainer.save(self.settings.model_dir)
        run.vocabulary_size = trainer.vocabulary_size
        return trainer

    def _build_context_profile(self, corpus: Sequence[Sequence[str]]):
        """주변 단어 분포 기반 후보 경로 (§26 확장). 끄면 None 을 돌려준다."""
        conf = self.settings.get("context_profile", {})
        if not bool(conf.get("enabled", True)):
            return None
        return ContextProfileIndex(
            window=int(conf.get("window", 5)),
            min_count=int(conf.get("min_count", 5)),
        ).build(corpus)

    def _propose_llm_pairs(self, terms: Sequence[Term]) -> dict:
        """LLM 이 용어 목록을 읽고 직접 제안하는 후보 경로 (§26 확장).

        분포 통계는 두 표기가 서로 다른 문서에 갈려 있으면 잡지 못한다.
        skip_llm 이면 건너뛴다.
        """
        conf = self.settings.get("llm_candidates", {})
        if not bool(conf.get("enabled", False)):
            return {}

        # 파일로 받은 제안이 있으면 그것을 쓴다 (API 키를 쓸 수 없는 환경).
        proposals_file = conf.get("proposals_file")
        if proposals_file:
            allowed = {t.term_key for t in terms if t.candidate_eligible}
            found = load_proposals(proposals_file, allowed)
            if found:
                return found

        if self.options.skip_llm:
            return {}
        llm_conf = self.settings.get("llm_validation", {})
        proposer = LLMCandidateProposer(
            create_llm_client(llm_conf),
            self.contexts,
            batch_size=int(conf.get("batch_size", 100)),
            context_per_term=int(conf.get("context_per_term", 1)),
            max_terms=int(conf.get("max_terms", 2000)),
            min_frequency=int(conf.get("min_frequency", 2)),
        )
        return proposer.propose(terms)

    def _generate_candidates(self, trainer: FastTextTrainer, terms: Sequence[Term],
                             run: IndexingRun):
        conf = self.settings.get("candidate", {})
        profile_conf = self.settings.get("context_profile", {})
        generator = CandidateGenerator(
            top_n=int(conf.get("top_n", 20)),
            min_similarity=float(conf.get("min_similarity", 0.60)),
            min_term_frequency=int(conf.get("min_term_frequency", 3)),
            min_term_length=int(conf.get("min_term_length", 2)),
            max_candidates_per_term=int(conf.get("max_candidates_per_term", 10)),
            max_total_candidates=int(conf.get("max_total_candidates", 5000)),
            stopwords=self.stopwords,
            delimiter=str(self.settings.get("phrases.delimiter", "_")),
            excluded_pairs=self.settings.excluded_pairs(),
            min_sentence_ratio=float(conf.get("min_sentence_ratio", 0.0)),
            verb_stems=getattr(self, "verb_stems", ()),
            profile_index=getattr(self, "profile_index", None),
            profile_top_n=int(profile_conf.get("top_n", 20)),
            profile_min_similarity=float(profile_conf.get("min_similarity", 0.15)),
            llm_pairs=self._propose_llm_pairs(terms),
            require_corroboration=bool(conf.get("require_corroboration", False)),
            mutual_top_k=int(conf.get("mutual_top_k", 0)),
            strong_rank_k=int(conf.get("strong_rank_k", 3)),
            lexical_pairs=LexicalCandidateFinder(
                delimiter=str(self.settings.get("phrases.delimiter", "_"))
            ).find(terms),
        )
        self.relations.clear_candidates()
        candidates = generator.generate(trainer, terms, run.run_id)
        # 자격 flag 가 갱신되었으므로 catalog 를 다시 저장한다 (비파괴 보존).
        self.terms.upsert_many(terms)
        saved = self.relations.save_candidates(candidates)
        if saved != len(candidates):
            logger.warning("후보 저장 수 불일치: 생성 %s / 저장 %s", len(candidates), saved)
        # candidate_id 가 붙은 상태로 다시 읽는다. LLM 응답을 이 id 로 되짚는다.
        candidates = self.relations.list_candidates()
        run.candidate_count = len(candidates)
        return candidates

    # ==================================================================
    # STEP 11~13. LLM 검증 / 저장
    # ------------------------------------------------------------------
    def _validate_and_store(self, candidates, terms: Sequence[Term], run: IndexingRun) -> None:
        if not candidates:
            logger.warning("검증할 후보가 없습니다.")
            return

        llm_conf = self.settings.get("llm_validation", {})
        llm_client = create_llm_client(llm_conf)

        # 1) 후보에 등장하는 term 유형 분류 (§32)
        if not self.options.skip_classification:
            involved = {c.term_a for c in candidates} | {c.term_b for c in candidates}
            target_terms = [t for t in terms if t.term_key in involved]
            classifier = TermClassifier(
                llm_client,
                self.contexts,
                self.terms,
                context_per_term=int(llm_conf.get("context_per_term", 5)),
                batch_size=int(llm_conf.get("batch_size", 5)) * 2,
                diversify_by_page=bool(llm_conf.get("diversify_by_page", True)),
                max_contexts_per_page=int(llm_conf.get("max_contexts_per_page", 2)),
            )
            classifier.classify_and_save(target_terms)

        # 2) 관계 판정 (§35)
        validator = RelationValidator(
            llm_client,
            self.contexts,
            context_per_term=int(llm_conf.get("context_per_term", 5)),
            batch_size=int(llm_conf.get("batch_size", 5)),
            diversify_by_page=bool(llm_conf.get("diversify_by_page", True)),
            max_contexts_per_page=int(llm_conf.get("max_contexts_per_page", 2)),
            delimiter=str(self.settings.get("phrases.delimiter", "_")),
        )
        outcome = validator.validate(candidates)
        run.validated_count = len(outcome.validations)

        for pair_key in outcome.failed_pairs:
            self.relations.update_candidate_status(
                pair_key, CandidateStatus.VALIDATION_FAILED.value
            )
        for pair_key in outcome.skipped_pairs:
            self.relations.update_candidate_status(pair_key, CandidateStatus.SKIPPED.value)

        # 3) 정책 적용 후 저장 (§39)
        builder = self._dictionary_builder()
        self.relations.clear_relations()
        builder.apply_validations(outcome.validations)

        for validation in outcome.validations:
            self.relations.update_candidate_status(
                f"{validation.term_a}||{validation.term_b}",
                CandidateStatus.VALIDATED.value,
            )

    def _dictionary_builder(self) -> DictionaryBuilder:
        return DictionaryBuilder(
            self.terms,
            self.relations,
            policy=RelationPolicy.from_settings(self.settings.get("relation_policy")),
            output_dir=self.settings.output_dir,
        )

    # ==================================================================
    # STEP 14. Dictionary
    # ------------------------------------------------------------------
    def _rebuild_dictionary(self, run: IndexingRun) -> None:
        builder = self._dictionary_builder()
        builder.rebuild()
        builder.export_review_queue()
        run.active_relation_count = self.relations.count_by_status(RelationStatus.ACTIVE.value)
        run.review_relation_count = self.relations.count_by_status(RelationStatus.REVIEW.value)
        run.rejected_relation_count = self.relations.count_by_status(
            RelationStatus.REJECTED.value
        )

    # ==================================================================
    def _start_indexing_run(self) -> IndexingRun:
        run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
        run = IndexingRun(run_id=run_id, started_at=_utcnow(), status=RunStatus.RUNNING.value)
        self.runs.start(run)
        logger.info("indexing run 시작: %s", run_id)
        return run

    def _log_summary(self, run: IndexingRun) -> None:
        logger.info(
            "\n=== Indexing Run %s (%s) ===\n"
            "  수집 문서       : %s (변경 %s)\n"
            "  문장            : %s\n"
            "  Phrase          : %s\n"
            "  FastText vocab  : %s\n"
            "  Term            : %s\n"
            "  Candidate       : %s\n"
            "  LLM 검증        : %s\n"
            "  ACTIVE 관계     : %s\n"
            "  REVIEW 관계     : %s\n"
            "  REJECTED 관계   : %s\n"
            "  오류            : %s\n"
            "  총 실행 시간    : %.2fs",
            run.run_id, run.status,
            run.document_count, run.changed_document_count,
            run.sentence_count, run.phrase_count, run.vocabulary_size,
            run.term_count, run.candidate_count, run.validated_count,
            run.active_relation_count, run.review_relation_count,
            run.rejected_relation_count, run.error_count, run.duration_seconds,
        )

    def close(self) -> None:
        self.db.close()
