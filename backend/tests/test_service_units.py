"""
Unit tests for core service components.

Covers:
  - LanguageService: new manifest/config extensions
  - FileFilterService: lock file exclusions
  - CodeRetrievalService: context padding, helpers
  - QueryAnalyzerService: deterministic fallback
  - ArchitecturalFileService: filesystem discovery
  - EvidenceCollectorService: architectural bonus gating
"""
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open
import tempfile
import os


# ---------------------------------------------------------------------------
# LanguageService
# ---------------------------------------------------------------------------

class TestLanguageServiceExtensions:
    """Manifest and config file extensions are now detected."""

    def setup_method(self):
        from backend.app.services.language_service import LanguageService
        self.svc = LanguageService()

    def test_json_detected(self):
        assert self.svc.detect("package.json") == "json"

    def test_txt_detected(self):
        assert self.svc.detect("requirements.txt") == "text"

    def test_toml_detected(self):
        assert self.svc.detect("pyproject.toml") == "toml"

    def test_yaml_detected(self):
        assert self.svc.detect("config.yaml") == "yaml"

    def test_yml_detected(self):
        assert self.svc.detect(".github/workflows/ci.yml") == "yaml"

    def test_python_still_works(self):
        assert self.svc.detect("manage.py") == "python"

    def test_typescript_still_works(self):
        assert self.svc.detect("app.ts") == "typescript"

    def test_unknown_extension_returns_none(self):
        assert self.svc.detect("file.xyz") is None

    def test_case_insensitive_extension(self):
        # Extension detection is already lower-cased
        assert self.svc.detect("README.MD") is None  # .md not in list — correct
        assert self.svc.detect("data.JSON") == "json"


# ---------------------------------------------------------------------------
# FileFilterService
# ---------------------------------------------------------------------------

class TestFileFilterLockFiles:
    """Lock files must be excluded to prevent huge-file noise."""

    def setup_method(self):
        from backend.app.services.file_filter_service import should_include_file, EXCLUDED_NAMES
        self.should_include = should_include_file
        self.excluded_names = EXCLUDED_NAMES

    def test_package_lock_excluded(self):
        assert "package-lock.json" in self.excluded_names

    def test_yarn_lock_excluded(self):
        assert "yarn.lock" in self.excluded_names

    def test_package_json_not_excluded(self):
        assert "package.json" not in self.excluded_names

    def test_requirements_txt_not_excluded(self):
        assert "requirements.txt" not in self.excluded_names

    def test_should_include_package_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            f = root / "frontend" / "package.json"
            f.parent.mkdir()
            f.write_text('{"name":"test"}')
            assert self.should_include(root, f) is True

    def test_should_exclude_package_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            f = root / "frontend" / "package-lock.json"
            f.parent.mkdir()
            f.write_text("{}")
            assert self.should_include(root, f) is False

    def test_should_exclude_yarn_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            f = root / "yarn.lock"
            f.write_text("# yarn lockfile v1")
            assert self.should_include(root, f) is False


# ---------------------------------------------------------------------------
# CodeRetrievalService
# ---------------------------------------------------------------------------

class TestCodeRetrievalService:

    def _make_repo(self, content: str) -> tuple[str, str]:
        """Write content to a temp file and return (repo_dir, rel_path)."""
        tmp = tempfile.mkdtemp()
        rel = "src/example.py"
        full = Path(tmp) / rel
        full.parent.mkdir(parents=True)
        full.write_text(content, encoding="utf-8")
        return tmp, rel

    def test_exact_range_returned(self):
        from backend.app.services.code_retrieval_service import CodeRetrievalService
        lines = "\n".join(f"line {i}" for i in range(1, 21))
        repo, rel = self._make_repo(lines)
        svc = CodeRetrievalService()
        result = svc.get_source(repo, rel, 5, 10)
        assert result["source"].startswith("line 5")
        assert result["source"].rstrip().endswith("line 10")

    def test_short_snippet_padded_with_context(self):
        """A 1-line snippet should be expanded with surrounding lines."""
        from backend.app.services.code_retrieval_service import CodeRetrievalService
        lines = "\n".join(f"line {i}" for i in range(1, 21))
        repo, rel = self._make_repo(lines)
        svc = CodeRetrievalService()
        # Request just line 10 (1 line)
        result = svc.get_source(repo, rel, 10, 10)
        src = result["source"]
        # Should have been padded to include more context
        assert "line 10" in src
        n_lines = len(src.splitlines())
        assert n_lines > 1, "Short snippet should be padded with surrounding context"

    def test_file_exists_helper(self):
        from backend.app.services.code_retrieval_service import CodeRetrievalService
        repo, rel = self._make_repo("hello")
        svc = CodeRetrievalService()
        assert svc.file_exists(repo, rel) is True
        assert svc.file_exists(repo, "nonexistent.py") is False

    def test_get_file_line_count(self):
        from backend.app.services.code_retrieval_service import CodeRetrievalService
        content = "\n".join(f"line {i}" for i in range(1, 11))  # 10 lines
        repo, rel = self._make_repo(content)
        svc = CodeRetrievalService()
        assert svc.get_file_line_count(repo, rel) == 10

    def test_get_source_range_exact_no_padding(self):
        """get_source_range must NOT apply context padding."""
        from backend.app.services.code_retrieval_service import CodeRetrievalService
        lines = "\n".join(f"line {i}" for i in range(1, 21))
        repo, rel = self._make_repo(lines)
        svc = CodeRetrievalService()
        result = svc.get_source_range(repo, rel, 10, 10)
        assert result["source"] == "line 10"
        assert result["start_line"] == 10
        assert result["end_line"] == 10

    def test_path_traversal_blocked(self):
        from backend.app.services.code_retrieval_service import CodeRetrievalService
        repo, _ = self._make_repo("hello")
        svc = CodeRetrievalService()
        with pytest.raises(ValueError, match="Invalid"):
            svc.get_source(repo, "../../etc/passwd", 1, 1)

    def test_missing_file_raises(self):
        from backend.app.services.code_retrieval_service import CodeRetrievalService
        repo, _ = self._make_repo("hello")
        svc = CodeRetrievalService()
        with pytest.raises(FileNotFoundError):
            svc.get_source(repo, "missing.py", 1, 1)


# ---------------------------------------------------------------------------
# QueryAnalyzerService – deterministic fallback
# ---------------------------------------------------------------------------

class TestQueryAnalyzerFallback:

    def _svc(self):
        from backend.app.services.query_analyzer_service import QueryAnalyzerService
        from backend.app.models.query_analysis import QuestionType
        # LLM always fails
        llm = MagicMock()
        llm.generate_structured.side_effect = RuntimeError("LLM unavailable")
        return QueryAnalyzerService(llm), QuestionType

    def test_analyze_with_fallback_never_raises(self):
        svc, _ = self._svc()
        result = svc.analyze_with_fallback("How does the checkout flow work?")
        assert result is not None
        assert result.intent.startswith("[fallback]")

    def test_fallback_extracts_camel_case_identifiers(self):
        from backend.app.services.query_analyzer_service import QueryAnalyzerService
        from backend.app.models.query_analysis import QueryAnalysis
        result = QueryAnalyzerService._deterministic_fallback(
            "Where is CartSyncView defined?"
        )
        assert "CartSyncView" in result.symbol_queries

    def test_fallback_classifies_feature_flow(self):
        from backend.app.services.query_analyzer_service import QueryAnalyzerService
        from backend.app.models.query_analysis import QuestionType
        result = QueryAnalyzerService._deterministic_fallback(
            "How does the checkout process work from start to finish?"
        )
        assert result.question_type == QuestionType.FEATURE_FLOW

    def test_fallback_classifies_symbol_lookup(self):
        from backend.app.services.query_analyzer_service import QueryAnalyzerService
        from backend.app.models.query_analysis import QuestionType
        result = QueryAnalyzerService._deterministic_fallback(
            "Where is VerifyRazorpayPaymentView defined?"
        )
        assert result.question_type == QuestionType.SYMBOL_LOOKUP

    def test_fallback_classifies_dependency_analysis(self):
        from backend.app.services.query_analyzer_service import QueryAnalyzerService
        from backend.app.models.query_analysis import QuestionType
        result = QueryAnalyzerService._deterministic_fallback(
            "What are the Angular and Django package versions?"
        )
        assert result.question_type == QuestionType.DEPENDENCY_ANALYSIS

    def test_fallback_classifies_architectural_overview(self):
        from backend.app.services.query_analyzer_service import QueryAnalyzerService
        from backend.app.models.query_analysis import QuestionType
        result = QueryAnalyzerService._deterministic_fallback(
            "Explain the architecture and structure of this project"
        )
        assert result.question_type == QuestionType.ARCHITECTURAL_OVERVIEW

    def test_fallback_returns_valid_query_analysis(self):
        from backend.app.services.query_analyzer_service import QueryAnalyzerService
        from backend.app.models.query_analysis import QueryAnalysis
        result = QueryAnalyzerService._deterministic_fallback(
            "How is Celery configured for email tasks?"
        )
        assert isinstance(result, QueryAnalysis)
        assert len(result.intent) > 0
        assert isinstance(result.symbol_queries, list)
        assert isinstance(result.text_queries, list)
        assert isinstance(result.import_queries, list)
        assert isinstance(result.concepts, list)

    def test_fallback_indicator_in_intent(self):
        from backend.app.services.query_analyzer_service import QueryAnalyzerService
        result = QueryAnalyzerService._deterministic_fallback("some question")
        assert "[fallback]" in result.intent

    def test_analyze_with_fallback_retries_before_fallback(self):
        """analyze_with_fallback should try the LLM first."""
        from backend.app.services.query_analyzer_service import QueryAnalyzerService
        from backend.app.models.query_analysis import QueryAnalysis, QuestionType

        valid_response = {
            "intent": "Find checkout",
            "question_type": "end_to_end_flow",
            "concepts": ["checkout"],
            "symbol_queries": ["CheckoutView"],
            "text_queries": ["checkout"],
            "import_queries": [],
            "__llm_metadata__": {"was_truncated": False, "finish_reason": "stop"},
        }
        llm = MagicMock()
        llm.generate_structured.return_value = valid_response
        svc = QueryAnalyzerService(llm)
        result = svc.analyze_with_fallback("How does checkout work?")
        assert result.intent == "Find checkout"
        assert "[fallback]" not in result.intent


# ---------------------------------------------------------------------------
# ArchitecturalFileService – filesystem discovery
# ---------------------------------------------------------------------------

class TestArchitecturalFileServiceFsDiscovery:
    """When manifests are not in the DB, the service falls back to filesystem."""

    def test_discovers_manifests_from_fs(self):
        from backend.app.services.architectural_file_service import ArchitecturalFileService

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Create a package.json in a subdirectory
            fe = root / "frontend"
            fe.mkdir()
            (fe / "package.json").write_text('{"dependencies":{"@angular/core":"^18.0.0"}}')

            # Also create requirements.txt
            be = root / "backend"
            be.mkdir()
            (be / "requirements.txt").write_text("Django==5.0\ncelery==5.4.0\n")

            db_mock = MagicMock()
            db_mock.scalars.return_value.all.return_value = []  # empty DB

            svc = ArchitecturalFileService()
            result = svc._discover_manifests_from_fs(db_mock, 1, str(root))

            paths = [r.path for r in result]
            assert any("package.json" in p for p in paths), f"package.json not in {paths}"
            assert any("requirements.txt" in p for p in paths), f"requirements.txt not in {paths}"

    def test_synthetic_file_records_have_none_id(self):
        from backend.app.services.architectural_file_service import ArchitecturalFileService

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "requirements.txt").write_text("Django==5.0\n")

            svc = ArchitecturalFileService()
            result = svc._discover_manifests_from_fs(MagicMock(), 1, str(tmp))

            assert len(result) >= 1
            assert result[0].id is None  # SimpleNamespace, not an ORM model

    def test_lock_files_not_discovered(self):
        from backend.app.services.architectural_file_service import ArchitecturalFileService

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text('{"name":"test"}')
            (root / "package-lock.json").write_text("{}")

            svc = ArchitecturalFileService()
            result = svc._discover_manifests_from_fs(MagicMock(), 1, str(tmp))

            paths = [r.path for r in result]
            assert not any("package-lock.json" in p for p in paths)

    def test_discover_key_files_uses_fs_fallback_when_db_empty(self):
        """discover_key_files with repository_path falls back to filesystem."""
        from backend.app.services.architectural_file_service import ArchitecturalFileService

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "requirements.txt").write_text("Django==5.0\n")

            db_mock = MagicMock()
            db_mock.scalars.return_value.all.return_value = []

            svc = ArchitecturalFileService()
            result = svc.discover_key_files(db_mock, 1, repository_path=str(root))

            assert len(result["dependencies"]) >= 1


# ---------------------------------------------------------------------------
# EvidenceCollectorService – architectural bonus gating
# ---------------------------------------------------------------------------

class TestEvidenceCollectorArchitecturalBonus:

    def _svc(self):
        from backend.app.services.evidence_collector_service import EvidenceCollectorService
        return EvidenceCollectorService()

    def test_no_bonus_for_symbol_lookup(self):
        from backend.app.models.query_analysis import QuestionType
        svc = self._svc()
        bonus = svc._get_architectural_bonus("readme.md", QuestionType.SYMBOL_LOOKUP)
        assert bonus == 0.0

    def test_readme_bonus_for_architectural_overview(self):
        from backend.app.models.query_analysis import QuestionType
        svc = self._svc()
        bonus = svc._get_architectural_bonus("readme.md", QuestionType.ARCHITECTURAL_OVERVIEW)
        assert bonus > 0.0

    def test_no_bonus_for_unknown_file(self):
        from backend.app.models.query_analysis import QuestionType
        svc = self._svc()
        bonus = svc._get_architectural_bonus("random_util.py", QuestionType.ARCHITECTURAL_OVERVIEW)
        assert bonus == 0.0


# ---------------------------------------------------------------------------
# CodeChunkService – manifest files use fallback chunker
# ---------------------------------------------------------------------------

class TestCodeChunkServiceManifests:
    """JSON/TXT files (no indexed symbols) use the fallback chunker."""

    def test_fallback_chunks_created_for_json_file(self):
        from backend.app.services.code_chunk_service import CodeChunkService

        svc = CodeChunkService()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pkg = root / "package.json"
            pkg.write_text('{"name":"test","dependencies":{"@angular/core":"^18.0.0"}}')

            db_mock = MagicMock()

            # Simulate a RepositoryFile with no symbols
            rf = MagicMock()
            rf.id = 1
            rf.path = "package.json"
            rf.language = "json"

            db_mock.scalars.return_value.all.side_effect = [
                [rf],   # first call → repository files
                [],     # second call → symbols for this file (empty)
            ]

            # _create_fallback_chunks should produce at least one chunk
            chunks = svc._create_fallback_chunks(
                repository_id=1,
                file_id=1,
                file_path=pkg,
            )
            assert len(chunks) >= 1
            assert "@angular/core" in chunks[0].content
