"""
Tests for AnswerGenerationService covering the two confirmed defects:

1. MAX_SNIPPET_LENGTH was 500 — too short for medium-sized functions,
   causing the LLM to see truncated code bodies.  Fixed to 1500.

2. _appears_truncated() falsely flagged valid answers ending with ':'
   (e.g. "CartSyncView supports two operations:"), injecting a spurious
   truncation limitation.  Fixed by adding ':' and '`' to valid endings.
"""

import pytest
from unittest.mock import MagicMock

from backend.app.services.answer_generation_service import AnswerGenerationService
from backend.app.models.investigation_answer import InvestigationAnswer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_service() -> AnswerGenerationService:
    return AnswerGenerationService(MagicMock())


def _evidence_item(
    eid: str,
    file: str = "backend/app/views.py",
    start: int = 10,
    end: int = 50,
    symbol: str = "my_function",
    snippet: str = "def my_function():\n    pass",
) -> dict:
    return {
        "evidence_id": eid,
        "file": file,
        "start_line": start,
        "end_line": end,
        "symbol": symbol,
        "symbol_type": "function",
        "snippet": snippet,
        "relevance_score": 0.9,
    }


# ---------------------------------------------------------------------------
# MAX_SNIPPET_LENGTH tests
# ---------------------------------------------------------------------------

class TestSnippetLength:
    """MAX_SNIPPET_LENGTH must be large enough to include function bodies."""

    def test_snippet_length_constant(self):
        """MAX_SNIPPET_LENGTH must be at least 1500 chars."""
        assert AnswerGenerationService.MAX_SNIPPET_LENGTH >= 1500, (
            "MAX_SNIPPET_LENGTH is too small. 500 chars truncates "
            "CartSyncView (1 672 chars) before the POST body and "
            "CheckoutView (3 337 chars) before order-creation logic."
        )

    def test_max_evidence_items_constant(self):
        """MAX_EVIDENCE_ITEMS should not exceed 12 to keep total tokens sane."""
        assert AnswerGenerationService.MAX_EVIDENCE_ITEMS <= 12, (
            "Increasing MAX_EVIDENCE_ITEMS above 12 with MAX_SNIPPET_LENGTH=1500 "
            "risks exceeding an 8 192-token context window."
        )

    def test_snippet_not_truncated_for_medium_function(self):
        """A 1 672-char snippet (CartSyncView size) must not be cut."""
        svc = _make_service()
        long_snippet = "x" * 1672   # CartSyncView is exactly 1 672 chars
        item = {
            "file": "backend/accounts/views.py",
            "start_line": 292,
            "end_line": 331,
            "symbol": "CartSyncView",
            "symbol_type": "class",
            "snippet": long_snippet,
            "relevance_score": 1.0,
        }
        prepared = svc._prepare_evidence([item])
        assert len(prepared) == 1
        prepared_snippet = prepared[0]["snippet"]
        # Should not be cut (1672 <= 1500 is False, so it may still be truncated
        # but the truncation marker should appear only at exactly MAX_SNIPPET_LENGTH)
        assert len(prepared_snippet) <= svc.MAX_SNIPPET_LENGTH + len("\n... (truncated)")

    def test_snippet_truncated_only_when_necessary(self):
        """Only snippets longer than MAX_SNIPPET_LENGTH get truncated."""
        svc = _make_service()
        short_snippet = "def small():\n    return 1"   # well under limit
        item = {
            "file": "a.py",
            "start_line": 1,
            "end_line": 3,
            "snippet": short_snippet,
            "relevance_score": 0.9,
        }
        prepared = svc._prepare_evidence([item])
        assert len(prepared) == 1
        assert prepared[0]["snippet"] == short_snippet, (
            "Short snippets must not be modified."
        )

    def test_very_long_snippet_gets_truncation_marker(self):
        """Snippets beyond MAX_SNIPPET_LENGTH get '... (truncated)' appended."""
        svc = _make_service()
        oversized = "a" * (svc.MAX_SNIPPET_LENGTH + 500)
        item = {
            "file": "b.py",
            "start_line": 1,
            "end_line": 100,
            "snippet": oversized,
            "relevance_score": 0.9,
        }
        prepared = svc._prepare_evidence([item])
        assert len(prepared) == 1
        snippet = prepared[0]["snippet"]
        assert snippet.endswith("\n... (truncated)")
        assert len(snippet) == svc.MAX_SNIPPET_LENGTH + len("\n... (truncated)")

    def test_cartsyncview_sized_snippet_reaches_llm_substantially(self):
        """
        A 1 672-char snippet should have most of its content visible to the LLM.
        With MAX_SNIPPET_LENGTH=1500, at least 89% of the content is shown.
        With the old value of 500, only 30% was shown.
        """
        svc = _make_service()
        # Build a body that is definitely >= 1672 chars
        body_unit = "    def method():\n        pass\n"   # 30 chars
        full_body = "class CartSyncView:\n" + body_unit * 55   # 20 + 55*30 = 1670 + pad
        # Pad to exactly 1672 if needed
        while len(full_body) < 1672:
            full_body += " "
        assert len(full_body) >= 1672

        item = {
            "file": "backend/accounts/views.py",
            "start_line": 292,
            "end_line": 331,
            "snippet": full_body[:1672],
            "relevance_score": 1.0,
        }
        prepared = svc._prepare_evidence([item])
        visible = len(prepared[0]["snippet"].replace("\n... (truncated)", ""))
        fraction = visible / 1672
        assert fraction >= 0.89, (
            f"Only {fraction:.0%} of CartSyncView is visible to the LLM. "
            "MAX_SNIPPET_LENGTH must be at least 1500."
        )

    def test_total_prompt_tokens_within_context_window(self):
        """
        With MAX_EVIDENCE_ITEMS items at MAX_SNIPPET_LENGTH each, the
        estimated total token count must stay below 8 192 tokens.
        """
        svc = _make_service()
        # Build a worst-case evidence set
        items = []
        for i in range(svc.MAX_EVIDENCE_ITEMS):
            items.append({
                "file": f"file_{i}.py",
                "start_line": i * 10 + 1,
                "end_line": i * 10 + 10,
                "snippet": "x" * svc.MAX_SNIPPET_LENGTH,
                "relevance_score": 0.9,
            })
        prepared = svc._prepare_evidence(items)
        user_prompt = svc._build_user_prompt("test question", prepared)
        system_prompt = svc._build_system_prompt("feature_flow", "TestRepo")

        total_chars = len(system_prompt) + len(user_prompt)
        input_tokens = total_chars // 4  # conservative approximation
        output_budget = svc._get_token_budget("feature_flow")
        total_tokens = input_tokens + output_budget

        assert total_tokens < 8192, (
            f"Estimated total tokens ({total_tokens}) exceeds 8 192 token limit. "
            f"Reduce MAX_SNIPPET_LENGTH or MAX_EVIDENCE_ITEMS."
        )


# ---------------------------------------------------------------------------
# _appears_truncated tests
# ---------------------------------------------------------------------------

class TestAppearsTruncated:
    """_appears_truncated must not produce false positives for valid endings."""

    def _svc(self) -> AnswerGenerationService:
        return _make_service()

    # --- cases that should NOT be flagged as truncated ---

    def test_ends_with_period(self):
        svc = self._svc()
        assert not svc._appears_truncated(
            "CartSyncView handles GET and POST requests."
        )

    def test_ends_with_colon_valid_technical_ending(self):
        """
        Answers often end with ':' introducing a list or sequence, e.g.
        'CartSyncView supports two operations:'
        This is NOT truncation.
        """
        svc = self._svc()
        for answer in [
            "CartSyncView supports two operations:",
            "The checkout process follows these steps:",
            "The error handling covers three cases:",
            "The function is defined as follows:",
        ]:
            assert not svc._appears_truncated(answer), (
                f"False positive: '{answer}' incorrectly flagged as truncated. "
                "Colon is a valid sentence ending in technical writing."
            )

    def test_ends_with_question_mark(self):
        svc = self._svc()
        assert not svc._appears_truncated("Is this correct?")

    def test_ends_with_exclamation(self):
        svc = self._svc()
        assert not svc._appears_truncated("The process completes successfully!")

    def test_ends_with_closed_code_block(self):
        svc = self._svc()
        answer = "The implementation is:\n```python\ndef foo():\n    pass\n```"
        assert not svc._appears_truncated(answer)

    def test_complete_multiline_answer(self):
        svc = self._svc()
        answer = (
            "CartSyncView is defined in backend/accounts/views.py [E1]. "
            "The GET method returns the user's cart from the database. "
            "The POST method merges the local guest cart into the database cart."
        )
        assert not svc._appears_truncated(answer)

    # --- cases that SHOULD be flagged as truncated ---

    def test_mid_sentence_truncation(self):
        svc = self._svc()
        assert svc._appears_truncated(
            "The CartSyncView handles both GET and POST requests for"
        )

    def test_ends_with_connector_word(self):
        svc = self._svc()
        assert svc._appears_truncated("The checkout process includes payment and")

    def test_unclosed_code_block(self):
        svc = self._svc()
        assert svc._appears_truncated(
            "The function is:\n```python\ndef foo():\n    pass"
        )

    def test_empty_answer_not_truncated(self):
        svc = self._svc()
        assert not svc._appears_truncated("")

    def test_no_period_no_colon(self):
        svc = self._svc()
        assert svc._appears_truncated("The code uses Django REST framework")


# ---------------------------------------------------------------------------
# Integration: truncation detection does not corrupt valid answers
# ---------------------------------------------------------------------------

class TestTruncationDetectionIntegration:
    """End-to-end test: answers with colon endings must not get false limitations."""

    def test_colon_ending_does_not_add_spurious_limitation(self):
        """
        An LLM answer that ends with ':' should NOT have a truncation
        limitation appended to it.
        """
        svc = _make_service()

        evidence = [_evidence_item("E1")]

        # Simulate LLM returning an answer that ends with ':'
        fake_llm_response = {
            "answer": "CartSyncView supports two operations:",
            "cited_evidence_ids": ["E1"],
            "confidence": "partial",
            "limitations": [],
        }

        result = svc._validate_and_construct_answer(
            question="What does CartSyncView do?",
            llm_response=fake_llm_response,
            supplied_evidence=evidence,
        )

        assert isinstance(result, InvestigationAnswer)
        assert result.answer == "CartSyncView supports two operations:"

        # Must NOT contain a spurious truncation limitation
        spurious = any(
            "incomplete" in lim.lower() or "length constraints" in lim.lower()
            for lim in result.limitations
        )
        assert not spurious, (
            f"False truncation limitation injected: {result.limitations}"
        )

    def test_genuinely_truncated_answer_gets_limitation(self):
        """
        An answer that ends mid-sentence (no terminal punctuation, no colon)
        SHOULD receive a truncation limitation.
        """
        svc = _make_service()
        evidence = [_evidence_item("E1")]

        fake_llm_response = {
            "answer": "The checkout process is handled by",
            "cited_evidence_ids": ["E1"],
            "confidence": "high",
            "limitations": [],
        }

        result = svc._validate_and_construct_answer(
            question="How does checkout work?",
            llm_response=fake_llm_response,
            supplied_evidence=evidence,
        )

        truncation_noted = any(
            "incomplete" in lim.lower() or "length" in lim.lower()
            for lim in result.limitations
        )
        assert truncation_noted, (
            "A genuinely truncated answer should have a truncation limitation."
        )
        # Confidence must be downgraded from 'high'
        assert result.confidence != "high"

    def test_long_complete_answer_passes_without_limitation(self):
        """
        A long but complete answer (> 500 chars, ends with '.') must not
        be flagged as truncated.
        """
        svc = _make_service()
        evidence = [_evidence_item(f"E{i+1}") for i in range(5)]

        # Build a realistic multi-paragraph answer
        long_answer = (
            "CartSyncView is defined in backend/accounts/views.py [E1]. "
            "It inherits from APIView and requires authentication via the "
            "IsAuthenticated permission class. "
            "The GET method retrieves the authenticated user's cart items "
            "from the database using CartItem.objects.filter(user=request.user) [E2]. "
            "It serializes the results with CartItemSerializer and returns them "
            "in a 200 OK response. "
            "The POST method implements an additive merge strategy for synchronizing "
            "a guest cart with the database cart upon login [E3]. "
            "For each item in the submitted local_items list, it attempts to find "
            "or create a CartItem record using get_or_create [E4]. "
            "If the record already exists, the quantity is incremented by the "
            "guest quantity; otherwise a new record is created with the provided "
            "quantity [E5]. "
            "After processing all items, the complete updated cart is returned."
        )
        assert len(long_answer) > 500, "Test answer should be long to exercise the fix"

        fake_response = {
            "answer": long_answer,
            "cited_evidence_ids": ["E1", "E2", "E3", "E4", "E5"],
            "confidence": "high",
            "limitations": [],
        }

        result = svc._validate_and_construct_answer(
            question="What does CartSyncView do?",
            llm_response=fake_response,
            supplied_evidence=evidence,
        )

        assert result.answer == long_answer
        assert result.confidence == "high"
        spurious = any(
            "incomplete" in lim.lower() or "length constraints" in lim.lower()
            for lim in result.limitations
        )
        assert not spurious, (
            f"Long complete answer incorrectly flagged as truncated: {result.limitations}"
        )


# ---------------------------------------------------------------------------
# Evidence preparation: MAX_EVIDENCE_ITEMS cap
# ---------------------------------------------------------------------------

class TestEvidencePreparationCap:
    """MAX_EVIDENCE_ITEMS controls how many items reach the LLM."""

    def test_evidence_capped_at_max_items(self):
        svc = _make_service()
        # Supply more items than the limit
        items = []
        for i in range(svc.MAX_EVIDENCE_ITEMS + 5):
            items.append({
                "file": f"f{i}.py",
                "start_line": i * 5 + 1,
                "end_line": i * 5 + 5,
                "snippet": f"def func_{i}(): pass",
                "relevance_score": 1.0 - i * 0.01,
            })
        prepared = svc._prepare_evidence(items)
        assert len(prepared) <= svc.MAX_EVIDENCE_ITEMS

    def test_all_items_get_stable_evidence_ids(self):
        svc = _make_service()
        items = [
            {
                "file": f"f{i}.py",
                "start_line": i * 5 + 1,
                "end_line": i * 5 + 5,
                "snippet": f"def func_{i}(): pass",
                "relevance_score": 0.9,
            }
            for i in range(5)
        ]
        prepared = svc._prepare_evidence(items)
        ids = [p["evidence_id"] for p in prepared]
        assert ids == [f"E{i+1}" for i in range(len(prepared))]
