"""
Comprehensive unit and integration tests for OpenSource Investigator Streamlit Frontend.
Uses built-in unittest.mock so no external mocking libraries are required.

Tests:
- API Client request/response handling, custom exception mapping, timeouts, and route errors.
- Formatting utilities: GitHub permalinks, timestamp conversions, unmapped citation detection.
- Investigation response fixture validation matching real FastAPI schemas.
- Health-check success and connection failure states.
- Missing endpoint (e.g. GET /repositories) graceful degradation.
"""

import pytest
from unittest.mock import MagicMock, patch
import requests
from requests.exceptions import ConnectionError as ReqConnectionError, Timeout as ReqTimeout

from frontend.api_client import (
    ApiClient,
    ApiClientError,
    BackendConnectionError,
    BackendTimeoutError,
    EndpointNotImplementedError,
    RepositoryNotFoundError,
    InvestigationError,
)
from frontend.utils.formatting import (
    format_timestamp,
    format_execution_time,
    get_confidence_badge_html,
    generate_github_permalink,
    parse_citations_in_answer,
    detect_unmapped_citations,
)


@pytest.fixture
def client():
    return ApiClient(base_url="http://testserver:8000")


# ─────────────────────────────────────────────────────────────────────────────
# 1. Formatting & Utility Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_format_timestamp():
    # Valid ISO string
    formatted = format_timestamp("2026-10-28T09:30:00Z")
    assert "2026-10-28" in formatted
    assert "UTC" in formatted

    # Float timestamp
    formatted_epoch = format_timestamp(1761643800)
    assert "2025" in formatted_epoch or "2026" in formatted_epoch

    # None and fallback
    assert format_timestamp(None) == "N/A"
    assert format_timestamp("not-a-timestamp") == "not-a-timestamp"


def test_format_execution_time():
    assert format_execution_time(0.045) == "45 ms"
    assert format_execution_time(2.456) == "2.46 s"
    assert format_execution_time(None) == "N/A"


def test_confidence_badge_html():
    high_badge = get_confidence_badge_html("high")
    assert "HIGH CONFIDENCE" in high_badge
    assert "#238636" in high_badge

    partial_badge = get_confidence_badge_html("partial")
    assert "PARTIAL EVIDENCE" in partial_badge
    assert "#bb8009" in partial_badge

    insufficient_badge = get_confidence_badge_html("insufficient")
    assert "INSUFFICIENT EVIDENCE" in insufficient_badge
    assert "#da3633" in insufficient_badge


def test_generate_github_permalink():
    # Valid GitHub repository URL with valid range
    url = generate_github_permalink(
        "https://github.com/dogukanayd/crumbs",
        "frontend/src/services/cartservice.ts",
        start_line=53,
        end_line=70,
    )
    assert url == "https://github.com/dogukanayd/crumbs/blob/main/frontend/src/services/cartservice.ts#L53-L70"

    # Single line
    url_single = generate_github_permalink(
        "https://github.com/dogukanayd/crumbs.git",
        "backend/accounts/views.py",
        start_line=337,
        end_line=337,
    )
    assert url_single == "https://github.com/dogukanayd/crumbs/blob/main/backend/accounts/views.py#L337"

    # Directory traversal rejection (security requirement)
    assert generate_github_permalink("https://github.com/org/repo", "../secret.py", 1, 10) is None
    assert generate_github_permalink("https://github.com/org/repo", "/etc/passwd", 1, 10) is None

    # Non-GitHub URL rejection
    assert generate_github_permalink("https://gitlab.com/org/repo", "file.py", 1, 10) is None


def test_citation_detection_and_unmapped_warnings():
    answer_text = (
        "The Cart is initialized in [E1]. The quantity update triggers addToCart [E2] "
        "and is processed by CartAddView [E4]. An unmapped hallucination is [E99]."
    )
    parsed = parse_citations_in_answer(answer_text)
    assert parsed == ["E1", "E2", "E4", "E99"]

    available_citations = [
        {"evidence_id": "E1", "file": "cart.ts"},
        {"evidence_id": "E2", "file": "cartservice.ts"},
        {"evidence_id": "E4", "file": "views.py"},
    ]

    unmapped = detect_unmapped_citations(answer_text, available_citations)
    assert unmapped == ["E99"]


# ─────────────────────────────────────────────────────────────────────────────
# 2. API Client Health Check Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_health_check_success(client):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "status": "healthy",
        "service": "AI Open-Source Project Investigator",
        "timestamp": "2026-10-28T09:40:00+00:00",
    }

    with patch.object(client._session, "request", return_value=mock_resp):
        data = client.health_check()
        assert data["status"] == "healthy"
        assert data["service"] == "AI Open-Source Project Investigator"


def test_health_check_connection_failure(client):
    with patch.object(client._session, "request", side_effect=ReqConnectionError("Connection refused")):
        with pytest.raises(BackendConnectionError) as exc_info:
            client.health_check()
        assert "Cannot connect to backend" in str(exc_info.value)


def test_health_check_timeout(client):
    with patch.object(client._session, "request", side_effect=ReqTimeout("Timed out")):
        with pytest.raises(BackendTimeoutError) as exc_info:
            client.health_check()
        assert "timed out" in str(exc_info.value)


# ─────────────────────────────────────────────────────────────────────────────
# 3. API Client Repository Operations Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_list_repositories_endpoint_not_implemented(client):
    # Simulate FastAPI returning 404 for unimplemented GET /repositories
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_resp.json.return_value = {"detail": "Not Found"}

    with patch.object(client._session, "request", return_value=mock_resp):
        with pytest.raises(EndpointNotImplementedError):
            client.list_repositories()


def test_add_repository_success(client):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "id": 2,
        "url": "https://github.com/dogukanayd/crumbs",
        "name": "crumbs",
        "status": "cloned",
        "local_path": "/data/repos/crumbs",
    }

    with patch.object(client._session, "request", return_value=mock_resp) as mock_req:
        result = client.add_repository("https://github.com/dogukanayd/crumbs")
        assert result["id"] == 2
        assert result["name"] == "crumbs"
        assert result["status"] == "cloned"
        mock_req.assert_called_once()
        assert mock_req.call_args[1]["json"] == {"repository_url": "https://github.com/dogukanayd/crumbs"}


def test_index_repository_success(client):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"files_indexed": 42, "symbols_extracted": 310}

    with patch.object(client._session, "request", return_value=mock_resp):
        res = client.index_repository(2)
        assert res["files_indexed"] == 42


# ─────────────────────────────────────────────────────────────────────────────
# 4. API Client Investigation Tests (Real Schema Fixtures)
# ─────────────────────────────────────────────────────────────────────────────

REAL_INVESTIGATION_RESPONSE_FIXTURE = {
    "repository": {
        "id": 2,
        "name": "crumbs",
        "url": "https://github.com/dogukanayd/crumbs",
    },
    "query": "How are cart quantity updates handled in the frontend and backend?",
    "question_type": "ARCHITECTURE_FLOW",
    "investigation": {
        "question": "How are cart quantity updates handled in the frontend and backend?",
        "answer": (
            "**Frontend**\n"
            "- In `cart.ts`, the `increaseQty(item)` handler increments local quantity and calls "
            "`addToCart(productId, variant, 1)` [E2].\n"
            "**Backend**\n"
            "- The request is routed to `CartAddView.post()` in `backend/accounts/views.py` [E4], "
            "which processes the item update."
        ),
        "citations": [
            {
                "evidence_id": "E2",
                "file": "frontend/src/services/cartservice.ts",
                "start_line": 53,
                "end_line": 70,
                "symbol": "addToCart",
                "snippet": "addToCart(productId: number, variant: any, quantity: number) {\n  // updates cart state\n}",
                "relevance": "Handles client cart state updates and sends request to server.",
            },
            {
                "evidence_id": "E4",
                "file": "backend/accounts/views.py",
                "start_line": 337,
                "end_line": 359,
                "symbol": "post",
                "snippet": "def post(self, request):\n    data = request.data\n    # processes cart item delta",
                "relevance": "Backend API view handling cart item quantity increments.",
            },
        ],
        "confidence": "partial",
        "limitations": [
            "UI event binding outside increaseQty/decreaseQty is not present in retrieved evidence.",
            "Backend item deletion logic when quantity <= 0 is unverified.",
        ],
        "execution_time_seconds": 1.42,
    },
    "evidence_summary": {
        "symbol_count": 8,
        "semantic_chunks": 12,
        "execution_time_seconds": 1.42,
    },
    "metadata": {
        "engine": "LangGraph",
        "rounds_executed": 2,
        "repair_attempts": 0,
        "total_time_seconds": 1.42,
        "timestamp": 1761644000.0,
    },
}


def test_investigate_success_with_schema(client):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = REAL_INVESTIGATION_RESPONSE_FIXTURE

    with patch.object(client._session, "request", return_value=mock_resp):
        resp = client.investigate(
            repository_id=2,
            query="How are cart quantity updates handled in the frontend and backend?",
        )

        assert resp["repository"]["id"] == 2
        assert resp["investigation"]["confidence"] == "partial"
        assert len(resp["investigation"]["citations"]) == 2
        assert resp["investigation"]["citations"][0]["evidence_id"] == "E2"
        assert len(resp["investigation"]["limitations"]) == 2
        assert resp["metadata"]["engine"] == "LangGraph"
        assert resp["metadata"]["rounds_executed"] == 2


def test_investigate_empty_query_rejected(client):
    with pytest.raises(ApiClientError) as exc_info:
        client.investigate(repository_id=2, query="   ")
    assert "cannot be empty" in str(exc_info.value)


def test_investigate_repository_not_found(client):
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_resp.json.return_value = {"detail": "Repository 999 not found."}

    with patch.object(client._session, "request", return_value=mock_resp):
        with pytest.raises(RepositoryNotFoundError):
            client.investigate(repository_id=999, query="Explain CartItem")


def test_investigate_server_error(client):
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.json.return_value = {"detail": "Groq LLM service rate limit exceeded."}

    with patch.object(client._session, "request", return_value=mock_resp):
        with pytest.raises(InvestigationError) as exc_info:
            client.investigate(repository_id=2, query="Explain CartItem")
        assert "Groq LLM service rate limit exceeded" in str(exc_info.value)
