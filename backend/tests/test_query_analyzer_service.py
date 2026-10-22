"""
Regression tests for QueryAnalyzerService.

Covers:
  - Normal (valid) JSON responses
  - Truncated JSON (finish_reason == "length") with salvage path
  - Truncated JSON that cannot be salvaged (should raise after retries)
  - Invalid JSON (JSONDecodeError propagated as RuntimeError)
  - Missing required fields in the response
  - Retry behaviour: second attempt succeeds
  - Empty query rejection
  - Compact schema is used instead of verbose Pydantic schema
"""

import pytest
from unittest.mock import MagicMock

from backend.app.models.query_analysis import QueryAnalysis, QuestionType
from backend.app.services.query_analyzer_service import QueryAnalyzerService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_llm(responses):
    """Return a mock LLMService that yields *responses* in order.

    Each element of *responses* is either:
      - a dict  → returned as-is (simulate a good parse)
      - an Exception instance → raised
    """
    service = MagicMock()
    service.generate_structured.side_effect = responses
    return service


def _valid_raw(question_type: str = "symbol_lookup") -> dict:
    return {
        "intent": "Locate the symbol",
        "question_type": question_type,
        "concepts": ["symbol"],
        "symbol_queries": ["myFunc"],
        "text_queries": ["function definition"],
        "import_queries": [],
        "__llm_metadata__": {"was_truncated": False, "finish_reason": "stop"},
    }


# "only metadata" payload – nothing useful survived truncation
_METADATA_ONLY = {
    "__llm_metadata__": {"was_truncated": True, "finish_reason": "length"},
}


# ---------------------------------------------------------------------------
# Normal path
# ---------------------------------------------------------------------------

class TestQueryAnalyzerNormal:
    def test_valid_response_returns_query_analysis(self):
        llm = _make_llm([_valid_raw()])
        svc = QueryAnalyzerService(llm)
        result = svc.analyze("Where is myFunc defined?")
        assert isinstance(result, QueryAnalysis)
        assert result.question_type == QuestionType.SYMBOL_LOOKUP
        assert "myFunc" in result.symbol_queries

    def test_all_question_types_accepted(self):
        for qt in QuestionType:
            llm = _make_llm([_valid_raw(qt.value)])
            svc = QueryAnalyzerService(llm)
            result = svc.analyze("test query")
            assert result.question_type == qt

    def test_metadata_stripped_before_validation(self):
        """__llm_metadata__ must not reach Pydantic validation."""
        raw = _valid_raw()
        raw["__llm_metadata__"] = {"was_truncated": False}
        llm = _make_llm([raw])
        svc = QueryAnalyzerService(llm)
        result = svc.analyze("test")
        assert isinstance(result, QueryAnalysis)

    def test_empty_list_fields_accepted(self):
        raw = _valid_raw()
        raw["symbol_queries"] = []
        raw["text_queries"] = []
        raw["import_queries"] = []
        llm = _make_llm([raw])
        svc = QueryAnalyzerService(llm)
        result = svc.analyze("Explain the whole project")
        assert result.symbol_queries == []

    def test_uses_compact_schema_not_pydantic_schema(self):
        """The schema sent to the LLM must not contain $defs (Pydantic artefact)."""
        schema = QueryAnalyzerService.RESPONSE_SCHEMA
        assert "$defs" not in str(schema), (
            "Compact schema must not contain Pydantic $defs."
        )
        for field in ("intent", "question_type", "concepts",
                      "symbol_queries", "text_queries", "import_queries"):
            assert field in schema["properties"]

    def test_token_budget_is_below_answer_generation(self):
        """Query analysis must use fewer tokens than answer generation."""
        assert QueryAnalyzerService._PRIMARY_MAX_TOKENS < 1200, (
            "Query analysis token budget should be less than the smallest "
            "answer-generation budget to preserve rate-limit headroom."
        )

    def test_retry_count_is_bounded(self):
        assert QueryAnalyzerService._MAX_RETRIES <= 2, (
            "_MAX_RETRIES > 2 risks triggering provider rate limits in bulk mode."
        )


# ---------------------------------------------------------------------------
# Truncated JSON (finish_reason == "length")
# ---------------------------------------------------------------------------

class TestQueryAnalyzerTruncated:
    def test_truncated_response_salvaged_with_partial_fields(self):
        """A truncated response that has all required fields except import_queries is salvaged."""
        raw = {
            "intent": "Find the symbol",
            "question_type": "symbol_lookup",
            "concepts": ["symbol"],
            "symbol_queries": ["foo"],
            "text_queries": [],
            # import_queries deliberately absent – truncated before it was written
            "__llm_metadata__": {"was_truncated": True, "finish_reason": "length"},
        }
        llm = _make_llm([raw])
        svc = QueryAnalyzerService(llm)
        result = svc.analyze("Where is foo defined?")
        assert isinstance(result, QueryAnalysis)
        assert result.import_queries == []

    def test_truncated_metadata_only_triggers_retry(self):
        """A truncated response whose payload is empty triggers one retry."""
        good_raw = _valid_raw()
        llm = _make_llm([_METADATA_ONLY, good_raw])
        svc = QueryAnalyzerService(llm)
        result = svc.analyze("some question")
        assert isinstance(result, QueryAnalysis)
        assert llm.generate_structured.call_count == 2

    def test_truncated_metadata_only_raises_after_max_retries(self):
        """Repeated metadata-only responses raise RuntimeError after all retries."""
        attempts = QueryAnalyzerService._MAX_RETRIES + 1
        llm = _make_llm([_METADATA_ONLY] * attempts)
        svc = QueryAnalyzerService(llm)
        # Error may say "truncated" (unsalvageable path) or "invalid data" (validation path)
        with pytest.raises(RuntimeError):
            svc.analyze("some question")
        assert llm.generate_structured.call_count == attempts

    def test_retry_uses_smaller_token_budget(self):
        """Second attempt must request fewer tokens than the first."""
        good_raw = _valid_raw()
        llm = _make_llm([_METADATA_ONLY, good_raw])
        svc = QueryAnalyzerService(llm)
        svc.analyze("some question")

        calls = llm.generate_structured.call_args_list
        assert len(calls) == 2, "Expected 2 LLM calls (original + retry)"

        first_tokens = calls[0].kwargs.get("max_tokens") or calls[0].args[3]
        retry_tokens = calls[1].kwargs.get("max_tokens") or calls[1].args[3]
        assert retry_tokens < first_tokens, (
            "Retry should use fewer max_tokens to avoid repeated truncation."
        )


# ---------------------------------------------------------------------------
# Invalid JSON / LLM errors
# ---------------------------------------------------------------------------

class TestQueryAnalyzerInvalidJSON:
    def test_invalid_json_after_all_retries_raises(self):
        """RuntimeError from JSON parse propagates after all retries exhausted."""
        attempts = QueryAnalyzerService._MAX_RETRIES + 1
        llm = _make_llm(
            [RuntimeError("Failed to parse LLM response as JSON")] * attempts
        )
        svc = QueryAnalyzerService(llm)
        with pytest.raises(RuntimeError, match="Query analysis failed"):
            svc.analyze("Where is foo?")
        assert llm.generate_structured.call_count == attempts

    def test_invalid_json_then_success_on_retry(self):
        """If first call fails with JSON error but second succeeds, result is returned."""
        llm = _make_llm([
            RuntimeError("Failed to parse LLM response as JSON"),
            _valid_raw(),
        ])
        svc = QueryAnalyzerService(llm)
        result = svc.analyze("Where is foo?")
        assert isinstance(result, QueryAnalysis)
        assert llm.generate_structured.call_count == 2


# ---------------------------------------------------------------------------
# Missing required fields
# ---------------------------------------------------------------------------

class TestQueryAnalyzerMissingFields:
    def _repeated(self, raw: dict):
        return [raw] * (QueryAnalyzerService._MAX_RETRIES + 1)

    def test_missing_question_type_fails_validation(self):
        raw = {
            "intent": "Locate something",
            # question_type missing
            "concepts": [],
            "symbol_queries": [],
            "text_queries": [],
            "import_queries": [],
            "__llm_metadata__": {"was_truncated": False},
        }
        llm = _make_llm(self._repeated(raw))
        svc = QueryAnalyzerService(llm)
        with pytest.raises(RuntimeError, match="invalid data"):
            svc.analyze("some query")

    def test_invalid_question_type_value_fails_validation(self):
        raw = _valid_raw()
        raw["question_type"] = "not_a_valid_type"
        llm = _make_llm(self._repeated(raw))
        svc = QueryAnalyzerService(llm)
        with pytest.raises(RuntimeError):
            svc.analyze("some query")

    def test_missing_intent_fails_validation(self):
        raw = _valid_raw()
        del raw["intent"]
        llm = _make_llm(self._repeated(raw))
        svc = QueryAnalyzerService(llm)
        with pytest.raises(RuntimeError):
            svc.analyze("some query")


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

class TestQueryAnalyzerInputValidation:
    def test_empty_query_raises_value_error(self):
        llm = MagicMock()
        svc = QueryAnalyzerService(llm)
        with pytest.raises(ValueError, match="empty"):
            svc.analyze("")

    def test_whitespace_only_query_raises_value_error(self):
        llm = MagicMock()
        svc = QueryAnalyzerService(llm)
        with pytest.raises(ValueError, match="empty"):
            svc.analyze("   ")

    def test_llm_not_called_for_empty_query(self):
        llm = MagicMock()
        svc = QueryAnalyzerService(llm)
        try:
            svc.analyze("")
        except ValueError:
            pass
        llm.generate_structured.assert_not_called()


# ---------------------------------------------------------------------------
# Salvage helper
# ---------------------------------------------------------------------------

class TestSalvagePartial:
    def test_returns_none_for_metadata_only(self):
        """A dict with only __llm_metadata__ should not be salvageable."""
        result = QueryAnalyzerService._salvage_partial(_METADATA_ONLY)
        assert result is None

    def test_salvage_fills_missing_lists(self):
        partial = {
            "intent": "Find something",
            "question_type": "symbol_lookup",
            # all list fields missing
        }
        result = QueryAnalyzerService._salvage_partial(partial)
        assert result is not None
        assert result["symbol_queries"] == []
        assert result["text_queries"] == []
        assert result["import_queries"] == []
        assert result["concepts"] == []

    def test_salvage_fills_missing_intent(self):
        partial = {
            "question_type": "feature_flow",
            "concepts": [],
            "symbol_queries": [],
            "text_queries": [],
            "import_queries": [],
        }
        result = QueryAnalyzerService._salvage_partial(partial)
        assert result is not None
        assert result["intent"]  # non-empty fallback

    def test_salvage_fixes_invalid_question_type(self):
        partial = {
            "intent": "test",
            "question_type": "garbage_value",
            "concepts": [],
            "symbol_queries": [],
            "text_queries": [],
            "import_queries": [],
        }
        result = QueryAnalyzerService._salvage_partial(partial)
        assert result is not None
        valid_types = {qt.value for qt in QuestionType}
        assert result["question_type"] in valid_types

    def test_salvage_preserves_valid_data(self):
        partial = {
            "intent": "Find checkout",
            "question_type": "end_to_end_flow",
            "concepts": ["checkout", "payment"],
            "symbol_queries": ["handleCheckout"],
            "text_queries": ["checkout flow"],
            "import_queries": ["stripe"],
        }
        result = QueryAnalyzerService._salvage_partial(partial)
        assert result is not None
        assert result["intent"] == "Find checkout"
        assert result["question_type"] == "end_to_end_flow"
        assert "handleCheckout" in result["symbol_queries"]

    def test_salvage_strips_metadata_keys(self):
        partial = {
            "intent": "test",
            "question_type": "symbol_lookup",
            "concepts": [],
            "symbol_queries": [],
            "text_queries": [],
            "import_queries": [],
            "__llm_metadata__": {"was_truncated": True},
        }
        result = QueryAnalyzerService._salvage_partial(partial)
        assert result is not None
        assert "__llm_metadata__" not in result
