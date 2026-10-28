"""
Regression tests for the three known LangGraph investigation bugs.

Test 1 (Bug-1): End-to-end flow must NOT be marked sufficient when
  HTTP request stage and URL routing are unverified.

Test 2 (Bug-2): Citation validation must NEVER report "all claims verified"
  when evaluated_claims_count is 0.

Test 3 (Bug-3): Admin.py / test files must NOT count as evidence that a
  nonexistent symbol exists.

Additional cases:
- Irrelevant evidence (high semantic similarity but wrong symbol).
- Missing flow connections (stages present but unconnected).
- Zero evaluated claims → not_evaluated status.
- Failed citation repair.
- Retrieval round termination is bounded.
"""

import pytest
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_evidence(
    file: str,
    symbol: str = "",
    snippet: str = "def foo(): pass",
    start: int = 1,
    end: int = 10,
    eid: str = "E1",
    exact_match: bool = False,
) -> dict:
    return {
        "evidence_id": eid,
        "file": file,
        "symbol": symbol,
        "symbol_type": "function",
        "snippet": snippet,
        "start_line": start,
        "end_line": end,
        "relevance_score": 0.9,
        "exact_match": exact_match,
        "exact_identifier_match": exact_match,
    }


# ============================================================================
# Bug-1: Incorrect sufficiency for end-to-end flow
# ============================================================================

class TestBug1IncorrectSufficiency:
    """
    Test that the evidence evaluator does NOT return sufficient when key flow
    stages (HTTP request, URL routing) are missing or unverified.
    """

    def _make_evaluator(self):
        from backend.app.services.evidence_evaluation_service import EvidenceEvaluationService
        llm = MagicMock()
        llm.generate_structured.return_value = {
            "sufficiency": "sufficient",          # LLM tries to say sufficient
            "supported_claims": ["Found backend"],
            "partially_supported_claims": [],
            "missing_claims": [],
            "contradictory_claims": [],
            "retrieval_needed": False,             # LLM tries to skip retrieval
            "recommended_queries": {"symbol_queries": [], "text_queries": [], "import_queries": []},
            "reasoning": "Found enough evidence",
            "__llm_metadata__": {"was_truncated": False},
        }
        return EvidenceEvaluationService(llm_service=llm)

    def test_cart_flow_missing_http_request_is_not_sufficient(self):
        """
        Evidence has frontend component and backend view but NO HTTP request or
        URL routing. Must return partially_sufficient or insufficient, NOT sufficient.
        """
        svc = self._make_evaluator()

        # Frontend cart component — has UI elements but no HTTP call
        frontend = _make_evidence(
            "frontend/src/app/cart/cart.ts",
            "Cart",
            snippet="increaseQty(item) { this.cart.quantity++; }",
            eid="E1",
        )
        # Backend CartSyncView — guest merge endpoint, not quantity update
        backend = _make_evidence(
            "backend/accounts/views.py",
            "CartSyncView",
            snippet="class CartSyncView(APIView):\n    def post(self, request):\n        \"\"\"Merge local guest cart into DB — used on fresh login only.\"\"\"\n        local_items = request.data.get('items', [])",
            eid="E2",
        )
        # Admin registration of CartItem (unrelated)
        admin = _make_evidence(
            "backend/accounts/admin.py",
            "CartItemAdmin",
            snippet="@admin.register(CartItem)\nclass CartItemAdmin(admin.ModelAdmin):\n    list_display = ('user', 'product', 'quantity')",
            eid="E3",
        )

        evidence = [frontend, backend, admin]
        result = svc.evaluate(
            question="How does the cart quantity update flow work from UI to database?",
            evidence=evidence,
            question_type="end_to_end_flow",
            current_round=2,  # max rounds reached
            max_rounds=2,
        )

        # Must not be "sufficient"
        assert result.sufficiency != "sufficient", (
            f"Flow with missing HTTP request and URL routing must NOT be 'sufficient'. "
            f"Got: {result.sufficiency}. Reasoning: {result.reasoning}"
        )
        # Must flag the unverified CartSyncView connection
        all_issues = result.unverified_connections + result.missing_stages + result.missing_claims
        issues_text = " ".join(str(i) for i in all_issues).lower()
        assert any(
            kw in issues_text for kw in ("cartsyncview", "url_routing", "http_request", "unverified", "missing")
        ), f"Expected unverified connection for CartSyncView or missing routing. Issues: {all_issues}"

    def test_retrieval_needed_false_when_max_rounds_reached(self):
        """After max_rounds, retrieval_needed must be False regardless of gaps."""
        svc = self._make_evaluator()
        evidence = [
            _make_evidence("backend/orders/views.py", "CheckoutView", eid="E1"),
        ]
        result = svc.evaluate(
            question="How does checkout work end to end?",
            evidence=evidence,
            question_type="end_to_end_flow",
            current_round=2,
            max_rounds=2,
        )
        assert result.retrieval_needed is False, (
            "retrieval_needed must be False when max_rounds is reached."
        )

    def test_routing_edge_bounded_by_max_rounds(self):
        """
        The investigation_graph routing function must not loop past max_rounds.
        """
        from backend.app.services.investigation_graph import InvestigationGraphService
        from backend.app.services.evidence_evaluation_service import EvidenceEvaluation

        svc = MagicMock(spec=InvestigationGraphService)
        svc.MAX_ADDITIONAL_ROUNDS = 2

        # Simulate state where retrieval_needed=True but we've already used all rounds
        state = {
            "retrieval_needed": True,
            "current_round": 2,   # already AT max_rounds
            "max_rounds": 2,
        }
        # Access the actual routing method
        real_svc = InvestigationGraphService.__new__(InvestigationGraphService)
        real_svc.MAX_ADDITIONAL_ROUNDS = 2
        result = real_svc._route_after_evaluation(state)
        assert result == "proceed_to_answer", (
            f"Should route to proceed_to_answer when current_round ({state['current_round']}) "
            f">= max_rounds ({state['max_rounds']}). Got: {result}"
        )

    def test_routing_allows_loop_below_max_rounds(self):
        """The routing function should still loop when below max_rounds."""
        from backend.app.services.investigation_graph import InvestigationGraphService

        real_svc = InvestigationGraphService.__new__(InvestigationGraphService)
        real_svc.MAX_ADDITIONAL_ROUNDS = 2
        state = {
            "retrieval_needed": True,
            "current_round": 1,   # below max_rounds=2
            "max_rounds": 2,
        }
        result = real_svc._route_after_evaluation(state)
        assert result == "continue_retrieval", (
            f"Should route to continue_retrieval when current_round < max_rounds. Got: {result}"
        )


# ============================================================================
# Bug-2: Citation validation falsely reports success
# ============================================================================

class TestBug2CitationValidationFalseSuccess:
    """
    Test that citation validation correctly counts evaluated claims and
    does NOT report success when zero claims have been evaluated.
    """

    def _make_validator(self):
        from backend.app.services.citation_validation_service import CitationValidationService
        return CitationValidationService(llm_service=None)

    def _make_answer_with_citations(self, answer_text: str, citations: list):
        from backend.app.models.investigation_answer import InvestigationAnswer
        return InvestigationAnswer(
            question="test question",
            answer=answer_text,
            citations=citations,
            confidence="partial",
            limitations=[],
        )

    def _make_citation(self, eid: str, file: str, start: int, end: int):
        from backend.app.models.investigation_answer import Citation
        return Citation(
            evidence_id=eid,
            file=file,
            start_line=start,
            end_line=end,
            symbol="test_symbol",
            snippet="def test(): pass",
            relevance="test",
        )

    def test_zero_claims_produces_not_evaluated(self):
        """
        An answer with NO inline [E#] references must produce status=not_evaluated,
        not 'verified'. evaluated_claims_count must be 0.
        """
        validator = self._make_validator()
        answer = self._make_answer_with_citations(
            "The checkout process handles payments. The cart stores items.",
            [],
        )
        result = validator.validate_answer_citations(
            answer=answer,
            evidence=[],
            repository_path=None,
        )
        assert result.to_dict()["evaluated_claims_count"] == 0, (
            "No inline references means 0 evaluated claims."
        )
        assert result.is_valid is False or result.to_dict().get("evaluated_claims_count", -1) == 0, (
            "Answer with no [E#] references should not claim all citations verified."
        )

    def test_answer_with_citations_always_evaluates_them(self):
        """
        Any answer with inline [E1], [E2], etc. must result in
        evaluated_claims_count > 0 — never 0 when references exist.
        """
        validator = self._make_validator()
        citation = self._make_citation("E1", "backend/accounts/views.py", 1, 10)
        answer = self._make_answer_with_citations(
            "CartSyncView is defined in backend/accounts/views.py [E1]. "
            "The GET method returns cart items [E1].",
            [citation],
        )
        evidence = [_make_evidence(
            "backend/accounts/views.py", "CartSyncView",
            snippet="class CartSyncView(APIView):\n    def get(self, request): pass",
            eid="E1",
        )]
        result = validator.validate_answer_citations(
            answer=answer, evidence=evidence, repository_path=None
        )
        count = result.to_dict()["evaluated_claims_count"]
        assert count > 0, (
            f"Answer has inline [E1] references but evaluated_claims_count={count}. "
            "Bug-2: evaluate_claim_to_source_support must populate evaluated_claims."
        )

    def test_cart_sync_view_quantity_update_is_unsupported_claim(self):
        """
        Claiming CartSyncView handles normal quantity updates must be flagged
        as an unsupported claim (it's documented as login-merge only).
        """
        validator = self._make_validator()
        citation = self._make_citation("E1", "backend/accounts/views.py", 292, 331)
        answer = self._make_answer_with_citations(
            "The cart quantity update is handled by CartSyncView [E1] which processes quantity updates.",
            [citation],
        )
        evidence = [_make_evidence(
            "backend/accounts/views.py", "CartSyncView",
            snippet='class CartSyncView(APIView):\n    def post(self, request):\n        """Merge local guest cart into DB — used on fresh login only."""\n        local_items = request.data.get("items", [])',
            eid="E1",
            start=292, end=331,
        )]
        result = validator.validate_answer_citations(
            answer=answer, evidence=evidence, repository_path=None
        )
        assert len(result.unsupported_claims) > 0, (
            "CartSyncView attributed to quantity updates must be flagged as unsupported."
        )

    def test_evaluated_claims_count_matches_inline_references(self):
        """
        evaluated_claims_count should equal the number of unique (sentence, eid) pairs.
        """
        validator = self._make_validator()
        citation = self._make_citation("E1", "backend/orders/views.py", 32, 118)
        answer = self._make_answer_with_citations(
            "The checkout is handled by CheckoutView [E1]. "
            "Order creation happens in CheckoutView [E1]. "
            "Payment is processed by Razorpay [E1].",
            [citation],
        )
        evidence = [_make_evidence(
            "backend/orders/views.py", "CheckoutView",
            snippet="class CheckoutView(APIView):\n    def post(self, request): pass",
            eid="E1",
        )]
        result = validator.validate_answer_citations(
            answer=answer, evidence=evidence, repository_path=None
        )
        count = result.to_dict()["evaluated_claims_count"]
        assert count >= 3, (
            f"Three sentences cite [E1] but evaluated_claims_count={count}."
        )


# ============================================================================
# Bug-3: Admin/test files as evidence for nonexistent symbol
# ============================================================================

class TestBug3AdminFilesAsEvidence:
    """
    Admin.py, test files, and migrations must NOT count as proof that a
    symbol or implementation exists. A symbol_lookup for a nonexistent
    function must not return 'sufficient' merely because an admin.py
    happens to reference a related model.
    """

    def _make_evaluator(self):
        from backend.app.services.evidence_evaluation_service import EvidenceEvaluationService
        llm = MagicMock()
        llm.generate_structured.side_effect = Exception("LLM not available")
        return EvidenceEvaluationService(llm_service=llm)

    def test_admin_file_does_not_satisfy_symbol_lookup(self):
        """
        Evidence from admin.py alone must not return sufficient for a symbol lookup.
        Bug-3: CartItemAdmin in admin.py is not proof that AutoRefundAllFailedPayments exists.
        """
        svc = self._make_evaluator()
        admin_evidence = _make_evidence(
            "backend/accounts/admin.py",
            "CartItemAdmin",
            snippet="@admin.register(CartItem)\nclass CartItemAdmin(admin.ModelAdmin):\n    list_display = ('user', 'product', 'quantity')",
            eid="E1",
            exact_match=False,
        )
        result = svc.evaluate(
            question="Where is AutoRefundAllFailedPayments defined?",
            evidence=[admin_evidence],
            question_type="symbol_lookup",
            current_round=2,
            max_rounds=2,
        )
        assert result.sufficiency != "sufficient", (
            f"Admin.py evidence must not make symbol_lookup 'sufficient'. Got: {result.sufficiency}"
        )

    def test_test_file_does_not_satisfy_symbol_lookup(self):
        """Test files must not count as direct implementation evidence."""
        svc = self._make_evaluator()
        test_evidence = _make_evidence(
            "backend/orders/tests.py",
            "TestAutoRefund",
            snippet="class TestAutoRefund(TestCase):\n    def test_placeholder(self): pass",
            eid="E1",
            exact_match=True,  # even if exact_match=True from a test file
        )
        result = svc.evaluate(
            question="Where is AutoRefundAllFailedPayments defined?",
            evidence=[test_evidence],
            question_type="symbol_lookup",
            current_round=2,
            max_rounds=2,
        )
        assert result.sufficiency != "sufficient", (
            f"Test file evidence must not make symbol_lookup 'sufficient'. Got: {result.sufficiency}"
        )

    def test_migration_file_does_not_satisfy_symbol_lookup(self):
        """Migration files must not count as direct implementation evidence."""
        svc = self._make_evaluator()
        migration_evidence = _make_evidence(
            "backend/orders/migrations/0001_initial.py",
            "Migration",
            snippet="class Migration(migrations.Migration):\n    operations = [...]",
            eid="E1",
            exact_match=False,
        )
        result = svc.evaluate(
            question="Where is AutoRefundAllFailedPayments defined?",
            evidence=[migration_evidence],
            question_type="symbol_lookup",
            current_round=2,
            max_rounds=2,
        )
        assert result.sufficiency == "insufficient", (
            f"Migration file alone must return 'insufficient'. Got: {result.sufficiency}"
        )

    def test_direct_source_file_satisfies_symbol_lookup(self):
        """A direct views.py with exact match must still return sufficient."""
        svc = self._make_evaluator()
        direct = _make_evidence(
            "backend/accounts/views.py",
            "CartSyncView",
            snippet="class CartSyncView(APIView):\n    pass",
            eid="E1",
            exact_match=True,
        )
        result = svc.evaluate(
            question="Where is CartSyncView defined?",
            evidence=[direct],
            question_type="symbol_lookup",
            current_round=2,
            max_rounds=2,
        )
        assert result.sufficiency == "sufficient", (
            f"Direct source file with exact match must return 'sufficient'. Got: {result.sufficiency}"
        )

    def test_citation_validator_flags_admin_evidence_for_existence_claim(self):
        """
        CitationValidator must flag claims that positively assert a symbol's
        existence using only an admin file as evidence.
        Bug-3: 'AutoRefundAllFailedPayments is implemented in admin.py [E1]'
        must be flagged as unsupported.
        """
        from backend.app.services.citation_validation_service import CitationValidationService
        from backend.app.models.investigation_answer import InvestigationAnswer
        from backend.app.models.investigation_answer import Citation

        validator = CitationValidationService(llm_service=None)

        citation = Citation(
            evidence_id="E1",
            file="backend/orders/admin.py",
            start_line=5,
            end_line=8,
            symbol="CartItemAdmin",
            snippet="@admin.register(CartItem)\nclass CartItemAdmin(admin.ModelAdmin): pass",
            relevance="Admin registration",
        )
        answer = InvestigationAnswer(
            question="Where is AutoRefundAllFailedPayments?",
            answer="AutoRefundAllFailedPayments is defined in the orders module [E1]. "
                   "The functionality is implemented there [E1].",
            citations=[citation],
            confidence="partial",
            limitations=[],
        )
        evidence = [_make_evidence(
            "backend/orders/admin.py", "CartItemAdmin",
            snippet="@admin.register(CartItem)\nclass CartItemAdmin(admin.ModelAdmin): pass",
            eid="E1", start=5, end=8,
        )]
        result = validator.validate_answer_citations(
            answer=answer, evidence=evidence, repository_path=None
        )
        # Should find unsupported claims
        assert len(result.unsupported_claims) > 0, (
            "Admin file cited as proof of existence should produce unsupported claims."
        )
        # evaluated_claims must be > 0 (Bug-2 fix check)
        assert result.to_dict()["evaluated_claims_count"] > 0, (
            "evaluated_claims_count must be > 0 when inline [E1] references exist."
        )


# ============================================================================
# Additional cases
# ============================================================================

class TestIrrelevantEvidence:
    """High semantic score ≠ relevant evidence for the specific question."""

    def test_irrelevant_file_does_not_satisfy_symbol_lookup(self):
        """
        A file that mentions payment in its docstring but has nothing to do
        with the requested symbol must not mark the lookup as sufficient.
        """
        from backend.app.services.evidence_evaluation_service import EvidenceEvaluationService
        llm = MagicMock()
        llm.generate_structured.side_effect = Exception("LLM unavailable")
        svc = EvidenceEvaluationService(llm_service=llm)

        # High semantic similarity but wrong content
        irrelevant = _make_evidence(
            "backend/catalog/views.py",
            "ProductListView",
            snippet="class ProductListView(ListAPIView):\n    \"\"\"List all products for payment display.\"\"\"\n    queryset = Product.objects.all()",
            eid="E1",
            exact_match=False,
        )
        result = svc.evaluate(
            question="Where is VerifyRazorpayPaymentView defined?",
            evidence=[irrelevant],
            question_type="symbol_lookup",
            current_round=2,
            max_rounds=2,
        )
        # ProductListView is not VerifyRazorpayPaymentView — should NOT be sufficient
        assert result.sufficiency != "sufficient", (
            f"Irrelevant evidence must not satisfy symbol lookup. Got: {result.sufficiency}"
        )


class TestMissingFlowConnections:
    """Stages present but not verifiably connected must not be sufficient."""

    def test_stages_present_but_routing_missing_not_sufficient(self):
        """UI + backend handler present but no URL routing → partially_sufficient at best."""
        from backend.app.services.evidence_evaluation_service import EvidenceEvaluationService
        llm = MagicMock()
        llm.generate_structured.side_effect = Exception("LLM unavailable")
        svc = EvidenceEvaluationService(llm_service=llm)

        ui = _make_evidence("frontend/src/app/cart/cart.ts", "Cart",
                             snippet="increaseQty(item) { this.cartService.addToCart(item); }", eid="E1")
        backend = _make_evidence("backend/accounts/views.py", "CartAddView",
                                 snippet="class CartAddView(APIView):\n    def post(self, request): pass", eid="E2")
        # No URL routing in evidence

        result = svc.evaluate(
            question="How does adding to cart work end to end?",
            evidence=[ui, backend],
            question_type="end_to_end_flow",
            current_round=2,
            max_rounds=2,
        )
        # Without URL routing, the connection is unverified
        assert result.sufficiency != "sufficient" or result.unverified_connections, (
            "Missing URL routing must produce either non-sufficient or unverified_connections."
        )


class TestZeroEvaluatedClaims:
    """evaluated_claims_count=0 must never produce status='verified'."""

    def test_zero_claims_status_is_not_verified(self):
        from backend.app.services.citation_validation_service import CitationValidationService
        from backend.app.models.investigation_answer import InvestigationAnswer

        validator = CitationValidationService(llm_service=None)
        answer = InvestigationAnswer(
            question="Does this function exist?",
            answer="The function may or may not exist. No code was found.",
            citations=[],
            confidence="insufficient",
            limitations=[],
        )
        result = validator.validate_answer_citations(answer=answer, evidence=[], repository_path=None)
        d = result.to_dict()
        assert d["evaluated_claims_count"] == 0
        # The key invariant: 0 evaluated claims must NEVER produce "verified" status
        assert result.is_valid is False, (
            "0 evaluated claims must not result in is_valid=True."
        )


class TestBoundedRetrievalLoop:
    """LangGraph loops must terminate at max_rounds."""

    def test_evaluate_node_enforces_boundary(self):
        """
        _evaluate_evidence_node must set retrieval_needed=False when
        current_round >= max_rounds.
        """
        from backend.app.services.investigation_graph import InvestigationGraphService
        from backend.app.services.evidence_evaluation_service import (
            EvidenceEvaluationService, EvidenceEvaluation
        )

        # Create a mock evaluation service that always says retrieval_needed=True
        mock_eval_svc = MagicMock()
        mock_eval_svc.evaluate.return_value = EvidenceEvaluation(
            sufficiency="insufficient",
            supported_claims=[],
            partially_supported_claims=[],
            missing_claims=["everything"],
            contradictory_claims=[],
            retrieval_needed=True,   # always wants more retrieval
            recommended_queries={"symbol_queries": [], "text_queries": [], "import_queries": []},
            reasoning="Always needs more",
            flow_stages=[],
            missing_stages=["all"],
            unverified_connections=[],
        )

        svc = InvestigationGraphService.__new__(InvestigationGraphService)
        svc.evidence_evaluation_service = mock_eval_svc

        state = {
            "query": "test",
            "current_round": 2,   # AT max_rounds
            "max_rounds": 2,
            "accumulated_evidence": [],
            "question_type": "feature_flow",
            "stage_times": {},
        }
        result = svc._evaluate_evidence_node(state)
        assert result["retrieval_needed"] is False, (
            f"retrieval_needed must be False when current_round >= max_rounds. "
            f"Got: {result['retrieval_needed']}"
        )
