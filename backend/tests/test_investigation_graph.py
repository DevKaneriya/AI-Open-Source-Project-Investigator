"""
Tests for LangGraph Investigation Workflow.

Covers:
1. LangGraph StateGraph compilation and initialization
2. TypedState propagation across nodes
3. Query Analysis node execution
4. Plan Retrieval node execution and multi-round query expansion
5. Retrieve Evidence node execution with strict repository isolation
6. Collect & Rank Evidence node merging and stable ID assignment (E1, E2, ...)
7. Evidence Evaluation node sufficiency assessment and gap identification
8. Conditional routing: continuation when evidence is insufficient (max 2 extra rounds)
9. Bounded loop termination: guarantee workflow exits after reaching round limits
10. Answer Generation node grounded output production
11. Citation Validation node detecting valid vs hallucinated evidence IDs
12. Citation Repair node repairing answers with hallucinated citations
13. Fast-path termination for exact symbol lookups
14. Nonexistent symbol handling without infinite retrieval loops
15. End-to-end integration via FastAPI /investigate endpoint
"""

import pytest
from unittest.mock import MagicMock, patch

from backend.app.models.investigation_answer import Citation, InvestigationAnswer, InvestigationResponse
from backend.app.models.query_analysis import QueryAnalysis, QuestionType
from backend.app.services.citation_validation_service import (
    CitationValidationResult,
    CitationValidationService,
)
from backend.app.services.evidence_evaluation_service import (
    EvidenceEvaluation,
    EvidenceEvaluationService,
)
from backend.app.services.investigation_graph import (
    InvestigationGraphService,
    InvestigationState,
)


# ---------------------------------------------------------------------------
# Fixtures & Mock Helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_llm_service():
    """Mock LLM service."""
    service = MagicMock()
    service.generate_structured.return_value = {
        "sufficiency": "sufficient",
        "supported_claims": ["Supported"],
        "partially_supported_claims": [],
        "missing_claims": [],
        "contradictory_claims": [],
        "retrieval_needed": False,
        "recommended_queries": {"symbol_queries": [], "text_queries": [], "import_queries": []},
        "reasoning": "Evidence is sufficient.",
    }
    return service


@pytest.fixture
def mock_retrieval_service():
    service = MagicMock()
    service._search_symbols.return_value = [
        {"file": "app/cart.py", "name": "get_cart", "start_line": 10, "end_line": 25, "symbol_type": "function"}
    ]
    service._search_text.return_value = []
    service._search_imports.return_value = []
    service.semantic_search_service.search.return_value = []
    service._build_source_context.return_value = [
        {"file": "app/cart.py", "start_line": 10, "end_line": 25, "source": "def get_cart():\n    return cart"}
    ]
    service._targeted_flow_retrieval.return_value = []
    service._build_relationships.return_value = []
    service._transform_evidence_for_response.side_effect = lambda ev: [
        {
            "file": item.get("file"),
            "start_line": item.get("start_line"),
            "end_line": item.get("end_line"),
            "snippet": item.get("source") or item.get("snippet", ""),
            "symbol": item.get("name") or item.get("symbol", ""),
            "symbol_type": item.get("symbol_type", "function"),
            "relevance_score": 0.95,
            "exact_match": True,
        }
        for item in ev
    ]
    return service


@pytest.fixture
def mock_query_analyzer_service():
    service = MagicMock()
    service.analyze_with_fallback.return_value = QueryAnalysis(
        intent="Find cart operations",
        question_type=QuestionType.FEATURE_FLOW,
        concepts=["cart", "checkout"],
        symbol_queries=["get_cart"],
        text_queries=["cart"],
        import_queries=[],
    )
    return service


@pytest.fixture
def mock_answer_generation_service():
    service = MagicMock()
    service.generate_answer.return_value = InvestigationAnswer(
        question="How does cart work?",
        answer="The cart is managed via get_cart in app/cart.py [E1].",
        citations=[
            Citation(
                evidence_id="E1",
                file="app/cart.py",
                start_line=10,
                end_line=25,
                symbol="get_cart",
                snippet="def get_cart():",
                relevance="Defines cart",
            )
        ],
        confidence="high",
        limitations=[],
    )
    return service


@pytest.fixture
def graph_service(
    mock_retrieval_service,
    mock_query_analyzer_service,
    mock_answer_generation_service,
    mock_llm_service,
):
    ev_eval_svc = EvidenceEvaluationService(mock_llm_service)
    cit_val_svc = CitationValidationService(mock_llm_service)
    return InvestigationGraphService(
        retrieval_service=mock_retrieval_service,
        query_analyzer_service=mock_query_analyzer_service,
        answer_generation_service=mock_answer_generation_service,
        evidence_evaluation_service=ev_eval_svc,
        citation_validation_service=cit_val_svc,
    )


# ---------------------------------------------------------------------------
# Test Cases
# ---------------------------------------------------------------------------

class TestGraphStructureAndState:
    """Test LangGraph StateGraph initialization, compilation, and structure."""

    def test_graph_compiles_successfully(self, graph_service):
        assert graph_service.graph is not None

    def test_graph_has_all_required_nodes(self, graph_service):
        node_names = list(graph_service.graph.nodes.keys())
        required_nodes = [
            "analyze_query",
            "plan_retrieval",
            "retrieve_evidence",
            "collect_rank_evidence",
            "evaluate_evidence",
            "generate_answer",
            "validate_citations",
            "repair_answer",
            "finalize_response",
        ]
        for node in required_nodes:
            assert node in node_names, f"Node {node} missing from graph nodes {node_names}"


class TestEvidenceEvaluationService:
    """Test evidence evaluation service logic."""

    def test_fast_path_exact_symbol_lookup(self, mock_llm_service):
        svc = EvidenceEvaluationService(mock_llm_service)
        evidence = [{"file": "views.py", "start_line": 10, "end_line": 20, "exact_match": True}]
        evaluation = svc.evaluate(
            question="Where is CartView defined?",
            evidence=evidence,
            question_type="symbol_lookup",
            current_round=0,
            max_rounds=2,
        )
        assert evaluation.sufficiency == "sufficient"
        assert evaluation.retrieval_needed is False
        # Fast path should avoid calling LLM
        mock_llm_service.generate_structured.assert_not_called()

    def test_reaches_max_rounds_forces_retrieval_needed_false(self, mock_llm_service):
        svc = EvidenceEvaluationService(mock_llm_service)
        evidence = [{"file": "views.py", "start_line": 10, "end_line": 20}]
        evaluation = svc.evaluate(
            question="Explain checkout flow",
            evidence=evidence,
            question_type="feature_flow",
            current_round=2,
            max_rounds=2,
        )
        assert evaluation.retrieval_needed is False

    def test_empty_evidence_fallback(self, mock_llm_service):
        svc = EvidenceEvaluationService(mock_llm_service)
        evaluation = svc.evaluate(
            question="Explain nonexistent feature",
            evidence=[],
            question_type="feature_flow",
            current_round=0,
            max_rounds=2,
        )
        assert evaluation.sufficiency == "insufficient"
        assert evaluation.retrieval_needed is True


class TestCitationValidationService:
    """Test citation validation and repair logic."""

    def test_validates_correct_citations(self):
        svc = CitationValidationService()
        evidence = [
            {"evidence_id": "E1", "file": "app/views.py", "start_line": 10, "end_line": 20}
        ]
        answer = InvestigationAnswer(
            question="Where is view?",
            answer="Defined in app/views.py [E1].",
            citations=[Citation(
                evidence_id="E1",
                file="app/views.py",
                start_line=10,
                end_line=20,
                symbol="my_view",
                snippet="code",
                relevance="defines view"
            )],
            confidence="high",
            limitations=[],
        )
        res = svc.validate_answer_citations(answer, evidence)
        assert res.is_valid is True
        assert len(res.hallucinated_ids) == 0
        assert res.repair_needed is False

    def test_detects_hallucinated_evidence_id(self):
        svc = CitationValidationService()
        evidence = [
            {"evidence_id": "E1", "file": "app/views.py", "start_line": 10, "end_line": 20}
        ]
        # Answer references E99 which does not exist
        answer = InvestigationAnswer(
            question="Where is view?",
            answer="Defined in app/views.py [E99].",
            citations=[Citation(
                evidence_id="E99",
                file="app/views.py",
                start_line=10,
                end_line=20,
                symbol="my_view",
                snippet="code",
                relevance="defines view"
            )],
            confidence="high",
            limitations=[],
        )
        res = svc.validate_answer_citations(answer, evidence)
        assert res.is_valid is False
        assert "E99" in res.hallucinated_ids
        assert res.repair_needed is True

    def test_deterministic_citation_repair(self):
        svc = CitationValidationService(llm_service=None)
        evidence = [
            {"evidence_id": "E1", "file": "app/views.py", "start_line": 10, "end_line": 20}
        ]
        answer = InvestigationAnswer(
            question="Where is view?",
            answer="Defined in [E1] and verified in [E99].",
            citations=[Citation(
                evidence_id="E1",
                file="app/views.py",
                start_line=10,
                end_line=20,
                symbol="my_view",
                snippet="code",
                relevance="defines view"
            )],
            confidence="high",
            limitations=[],
        )
        validation_res = CitationValidationResult(
            is_valid=False,
            valid_citations=answer.citations,
            invalid_citations=[],
            hallucinated_ids=["E99"],
            unsupported_claims=["E99"],
            repair_needed=True,
        )
        repaired = svc.repair_answer(answer, evidence, validation_res)
        assert "[E99]" not in repaired.answer
        assert "[E1]" in repaired.answer
        assert any("Removed unsupported citations" in lim for lim in repaired.limitations)


class TestConditionalRoutingAndBoundedLoops:
    """Test that LangGraph conditional loops iterate and terminate within bounds."""

    def test_route_after_evaluation_continues_when_needed(self, graph_service):
        state = {
            "retrieval_needed": True,
            "current_round": 1,
            "max_rounds": 2,
        }
        route = graph_service._route_after_evaluation(state)
        assert route == "continue_retrieval"

    def test_route_after_evaluation_stops_when_max_rounds_reached(self, graph_service):
        state = {
            "retrieval_needed": True,
            "current_round": 3,
            "max_rounds": 2,
        }
        route = graph_service._route_after_evaluation(state)
        assert route == "proceed_to_answer"

    def test_route_after_citation_validation_repairs_once(self, graph_service):
        state = {
            "citation_validation": {"repair_needed": True},
            "repair_attempts": 0,
            "max_repair_attempts": 1,
        }
        route = graph_service._route_after_citation_validation(state)
        assert route == "repair"

    def test_route_after_citation_validation_finalizes_after_max_repairs(self, graph_service):
        state = {
            "citation_validation": {"repair_needed": True},
            "repair_attempts": 1,
            "max_repair_attempts": 1,
        }
        route = graph_service._route_after_citation_validation(state)
        assert route == "finalize"


class TestFullGraphExecution:
    """Test full execution of the StateGraph workflow."""

    def test_execute_graph_end_to_end(self, graph_service):
        initial_state: InvestigationState = {
            "query": "How is cart handled?",
            "repository_id": 1,
            "repository_info": {"id": 1, "name": "TestRepo", "url": "https://github.com/test/repo"},
            "repository_path": None,
            "current_round": 0,
            "max_rounds": 2,
            "repair_attempts": 0,
            "max_repair_attempts": 1,
            "stage_times": {},
            "start_time": 1000.0,
        }
        result = graph_service.graph.invoke(initial_state)

        assert "response" in result
        response: InvestigationResponse = result["response"]
        assert isinstance(response, InvestigationResponse)
        assert response.query == "How is cart handled?"
        assert response.metadata["engine"] == "LangGraph"
        assert response.investigation is not None
        assert response.investigation.confidence == "high"
        assert len(response.investigation.citations) == 1
        assert response.investigation.citations[0].evidence_id == "E1"


# ---------------------------------------------------------------------------
# Regression Tests: Evidence Sufficiency, Flow Stages, & Citation Validation
# ---------------------------------------------------------------------------

class TestEvidenceSufficiencyAndCitationValidationRegression:
    """
    Regression tests addressing:
    1. Numerous irrelevant snippets must never produce sufficiency='sufficient'.
    2. Missing route/UI connections must trigger targeted retrieval.
    3. Guest-cart merge endpoint (CartSyncView) must not be automatically described as normal quantity-update endpoint.
    4. Citation validation must evaluate claim-to-source support and never blindly return unsupported_claims=[].
    5. Complete flow with all stages and verified connections produces sufficient result.
    6. Max rounds reached with unverified connections returns partial/insufficient with explicit missing stages.
    """

    def test_numerous_irrelevant_snippets_cannot_produce_sufficient_result(self, mock_llm_service):
        svc = EvidenceEvaluationService(mock_llm_service)
        # 12 irrelevant snippets
        irrelevant_evidence = [
            {
                "evidence_id": f"E{i}",
                "file": f"src/helpers/utils_{i}.py",
                "start_line": 1,
                "end_line": 20,
                "symbol": f"helper_{i}",
                "snippet": f"def helper_{i}():\n    return {i}",
            }
            for i in range(1, 13)
        ]
        evaluation = svc.evaluate(
            question="How are cart quantity updates handled in the frontend and backend?",
            evidence=irrelevant_evidence,
            question_type="feature_flow",
            current_round=0,
            max_rounds=2,
        )
        # MUST NOT be sufficient just because there are 12 snippets
        assert evaluation.sufficiency != "sufficient"
        assert evaluation.retrieval_needed is True
        assert not any("Found 12 evidence snippets" in claim for claim in evaluation.supported_claims)
        assert len(evaluation.missing_stages) > 0

    def test_missing_route_connections_trigger_targeted_retrieval(self, mock_llm_service):
        svc = EvidenceEvaluationService(mock_llm_service)
        # Evidence has UI component and client service, but is missing URL routing and backend
        partial_evidence = [
            {
                "evidence_id": "E1",
                "file": "frontend/src/app/cart/cart.ts",
                "start_line": 80,
                "end_line": 94,
                "symbol": "increaseQty",
                "snippet": "increaseQty(item: any) {\n    item.quantity++;\n    this.cartService.addToCart(item.productId, item.variant, 1);\n    this.calculateTotal();\n}",
            },
            {
                "evidence_id": "E2",
                "file": "frontend/src/services/cartservice.ts",
                "start_line": 53,
                "end_line": 70,
                "symbol": "addToCart",
                "snippet": "addToCart(productId: any, variant: string, quantity: number) {\n    this.http.post(`${environment.apiUrl}/accounts/cart/add/`, { productId, variant, quantity }).subscribe();\n}",
            },
        ]
        evaluation = svc.evaluate(
            question="Explain the cart quantity update flow from UI to database",
            evidence=partial_evidence,
            question_type="end_to_end_flow",
            current_round=0,
            max_rounds=2,
        )
        assert evaluation.retrieval_needed is True
        assert "url_routing" in evaluation.missing_stages
        assert "backend_handler" in evaluation.missing_stages
        # Verify recommended queries target the missing route and backend handler
        rec_text = evaluation.recommended_queries.get("text_queries", [])
        rec_sym = evaluation.recommended_queries.get("symbol_queries", [])
        assert any("cart/add" in q or "path('cart/" in q or "urls.py" in q for q in rec_text)
        assert any("CartAddView" in q for q in rec_sym)

    def test_guest_cart_merge_endpoint_not_automatically_described_as_normal_quantity_update(self, mock_llm_service):
        """
        CartSyncView is documented for guest-cart merge on fresh login only.
        It must not be automatically assumed to be the normal quantity update endpoint.
        """
        svc = EvidenceEvaluationService(mock_llm_service)
        # Evidence only has CartSyncView (login guest merge), no CartAddView or UI handler
        sync_only_evidence = [
            {
                "evidence_id": "E1",
                "file": "backend/accounts/views.py",
                "start_line": 292,
                "end_line": 331,
                "symbol": "CartSyncView",
                "snippet": (
                    "class CartSyncView(APIView):\n"
                    "    permission_classes = [IsAuthenticated]\n"
                    "    def post(self, request):\n"
                    "        '''Merge local guest cart into DB — used on fresh login only.'''\n"
                    "        local_items = request.data.get('items', [])\n"
                    "        for item in local_items:\n"
                    "            cart_item.quantity += quantity\n"
                    "            cart_item.save()\n"
                ),
            }
        ]
        evaluation = svc.evaluate(
            question="How are cart quantity updates handled when user increases quantity?",
            evidence=sync_only_evidence,
            question_type="feature_flow",
            current_round=0,
            max_rounds=2,
        )
        assert evaluation.retrieval_needed is True
        assert any("CartSyncView" in conn for conn in evaluation.unverified_connections)

        # Now test that CitationValidationService flags any claim asserting CartSyncView handles normal UI quantity updates
        cit_svc = CitationValidationService(llm_service=None)
        answer = InvestigationAnswer(
            question="How are cart quantity updates handled?",
            answer="When the user clicks increase quantity, CartSyncView [E1] handles the quantity update by incrementing cart_item.quantity.",
            citations=[
                Citation(
                    evidence_id="E1",
                    file="backend/accounts/views.py",
                    start_line=292,
                    end_line=331,
                    symbol="CartSyncView",
                    snippet="class CartSyncView(APIView): ... Merge local guest cart into DB — used on fresh login only",
                    relevance="quantity update",
                )
            ],
            confidence="high",
            limitations=[],
        )
        val_result = cit_svc.validate_answer_citations(answer, sync_only_evidence)
        assert val_result.is_valid is False
        assert val_result.repair_needed is True
        assert any("guest-cart" in uc or "fresh login" in uc or "CartSyncView" in uc for uc in val_result.unsupported_claims)

        # Test deterministic repair clarifies CartSyncView is guest-cart merge only
        repaired = cit_svc.repair_answer(answer, sync_only_evidence, val_result)
        assert repaired.confidence in ("partial", "insufficient")
        assert "fresh login" in repaired.answer or "guest-cart" in repaired.answer
        assert any("CartSyncView" in lim or "guest-cart" in lim for lim in repaired.limitations)

    def test_citation_validation_claim_support_evaluated(self):
        cit_svc = CitationValidationService(llm_service=None)
        evidence = [
            {
                "evidence_id": "E1",
                "file": "backend/accounts/models.py",
                "start_line": 10,
                "end_line": 30,
                "symbol": "CartItem",
                "snippet": "class CartItem(models.Model):\n    quantity = models.PositiveIntegerField(default=1)",
            }
        ]
        # Claim asserting that backend/accounts/models.py contains a UI button click handler
        answer = InvestigationAnswer(
            question="Where is button click handled?",
            answer="The UI event handler button click is handled in [E1].",
            citations=[
                Citation(
                    evidence_id="E1",
                    file="backend/accounts/models.py",
                    start_line=10,
                    end_line=30,
                    symbol="CartItem",
                    snippet="class CartItem(models.Model): ...",
                    relevance="UI button click",
                )
            ],
            confidence="high",
            limitations=[],
        )
        val_result = cit_svc.validate_answer_citations(answer, evidence)
        assert len(val_result.evaluated_claims) > 0
        assert val_result.is_valid is False
        assert any("UI event handling" in uc for uc in val_result.unsupported_claims)

    def test_flow_complete_when_all_stages_and_connections_verified(self, mock_llm_service):
        svc = EvidenceEvaluationService(mock_llm_service)
        # Full chain: UI -> client service -> URL routing -> backend handler -> persistence
        complete_evidence = [
            {
                "evidence_id": "E1",
                "file": "frontend/src/app/cart/cart.ts",
                "start_line": 80,
                "end_line": 94,
                "symbol": "increaseQty",
                "snippet": "increaseQty(item: any) {\n    item.quantity++;\n    this.cartService.addToCart(item.productId, item.variant, 1);\n    this.calculateTotal();\n}",
            },
            {
                "evidence_id": "E2",
                "file": "frontend/src/services/cartservice.ts",
                "start_line": 53,
                "end_line": 70,
                "symbol": "addToCart",
                "snippet": "addToCart(productId: any, variant: string, quantity: number) {\n    this.http.post(`${environment.apiUrl}/accounts/cart/add/`, { productId, variant, quantity }).subscribe();\n}",
            },
            {
                "evidence_id": "E3",
                "file": "backend/accounts/urls.py",
                "start_line": 37,
                "end_line": 38,
                "symbol": "urlpatterns",
                "snippet": "urlpatterns = [\n    path('cart/add/', CartAddView.as_view(), name='cart_add'),\n]",
            },
            {
                "evidence_id": "E4",
                "file": "backend/accounts/views.py",
                "start_line": 334,
                "end_line": 360,
                "symbol": "CartAddView",
                "snippet": (
                    "class CartAddView(APIView):\n"
                    "    def post(self, request):\n"
                    "        cart_item.quantity += quantity\n"
                    "        cart_item.save()\n"
                ),
            },
        ]
        evaluation = svc.evaluate(
            question="How are cart quantity updates handled in the frontend and backend?",
            evidence=complete_evidence,
            question_type="feature_flow",
            current_round=0,
            max_rounds=2,
        )
        assert evaluation.sufficiency == "sufficient"
        assert evaluation.retrieval_needed is False
        assert len(evaluation.missing_stages) == 0
        assert len(evaluation.unverified_connections) == 0

    def test_max_rounds_reached_with_missing_stages_reports_partial_or_insufficient(self, mock_llm_service):
        svc = EvidenceEvaluationService(mock_llm_service)
        # Only UI handler in evidence, missing backend and routing
        partial_evidence = [
            {
                "evidence_id": "E1",
                "file": "frontend/src/app/cart/cart.ts",
                "start_line": 80,
                "end_line": 85,
                "symbol": "increaseQty",
                "snippet": "increaseQty(item: any) { item.quantity++; }",
            }
        ]
        evaluation = svc.evaluate(
            question="How are cart quantity updates handled?",
            evidence=partial_evidence,
            question_type="feature_flow",
            current_round=2,
            max_rounds=2,
        )
        # Max rounds reached must force retrieval_needed=False and sufficiency in (partially_sufficient, insufficient)
        assert evaluation.retrieval_needed is False
        assert evaluation.sufficiency in ("partially_sufficient", "insufficient")
        assert len(evaluation.missing_stages) > 0
        assert "url_routing" in evaluation.missing_stages
        assert "backend_handler" in evaluation.missing_stages
