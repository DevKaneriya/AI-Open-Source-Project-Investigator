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



# ─────────────────────────────────────────────────────────────────────────────
# 5. Repository State Management & Synchronization Tests
# ─────────────────────────────────────────────────────────────────────────────

from frontend.utils.repo_manager import (
    sync_repositories,
    invalidate_repositories_cache,
    validate_selected_repository,
)
import streamlit as st


@pytest.fixture(autouse=True)
def clean_session_state():
    """Clear Streamlit session state between tests."""
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    yield
    for key in list(st.session_state.keys()):
        del st.session_state[key]


def test_delete_repository_client_call(client):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"message": "Repository deleted successfully.", "repository_id": 2}

    with patch.object(client._session, "request", return_value=mock_resp) as mock_req:
        res = client.delete_repository(2)
        assert res["repository_id"] == 2
        mock_req.assert_called_once()
        assert mock_req.call_args.kwargs["method"] == "DELETE"
        assert mock_req.call_args.kwargs["url"] == "http://testserver:8000/repositories/2"


def test_sync_repositories_two_repositories(client):
    """Test when the backend returns two repositories."""
    # User had repo 2 previously selected
    st.session_state["selected_repo_id"] = 2

    repos_data = [
        {"id": 1, "name": "repo-alpha", "url": "https://github.com/org/alpha", "status": "indexed"},
        {"id": 2, "name": "repo-beta", "url": "https://github.com/org/beta", "status": "cloned"},
    ]
    with patch.object(client, "list_repositories", return_value=repos_data):
        repos, err = sync_repositories(client, force_refresh=True)

        assert err is None
        assert len(repos) == 2
        assert st.session_state["repositories"] == repos
        assert st.session_state["repositories_loaded"] is True
        assert st.session_state["repositories_error"] is None
        # Retains and updates the selected repo record from fresh backend data
        assert st.session_state["selected_repo_id"] == 2
        assert st.session_state["selected_repo"]["name"] == "repo-beta"


def test_sync_repositories_empty_list(client):
    """Test when the backend returns an empty list (e.g. database wiped)."""
    # Seed state with stale data to verify it is pruned
    st.session_state["repositories"] = [{"id": 99, "name": "stale-repo"}]
    st.session_state["selected_repo_id"] = 99
    st.session_state["selected_repo"] = {"id": 99, "name": "stale-repo"}

    with patch.object(client, "list_repositories", return_value=[]):
        repos, err = sync_repositories(client, force_refresh=True)

        assert err is None
        assert repos == []
        assert st.session_state["repositories"] == []
        assert st.session_state["repositories_loaded"] is True
        assert st.session_state["repositories_error"] is None
        # Stale selection must be removed
        assert st.session_state["selected_repo_id"] is None
        assert st.session_state["selected_repo"] is None


def test_sync_repositories_previously_selected_repo_deleted(client):
    """Test when the previously selected repository has been deleted from the backend."""
    # User had repo 2 selected, but repo 2 was deleted and only repo 1 remains
    st.session_state["selected_repo_id"] = 2
    st.session_state["selected_repo"] = {"id": 2, "name": "deleted-repo"}

    fresh_repos = [
        {"id": 1, "name": "repo-alpha", "url": "https://github.com/org/alpha", "status": "indexed"}
    ]
    with patch.object(client, "list_repositories", return_value=fresh_repos):
        repos, err = sync_repositories(client, force_refresh=True)

        assert err is None
        assert len(repos) == 1
        # Task 5: Stale selection ID 2 is removed because it is no longer valid in backend
        assert st.session_state["selected_repo_id"] is None
        assert st.session_state["selected_repo"] is None


def test_sync_repositories_api_failure(client):
    """Test when the repository API is unavailable."""
    with patch.object(client, "list_repositories", side_effect=BackendConnectionError("Connection refused")):
        repos, err = sync_repositories(client, force_refresh=True)

        assert "Backend repository API error" in err
        assert "Connection refused" in err
        assert st.session_state["repositories_error"] is not None
        # Should NOT claim success or falsely report a healthy 0-repo state
        assert st.session_state.get("repositories_loaded") is not True


def test_cannot_submit_investigation_using_deleted_repository_id(client):
    """Confirm that an investigation cannot be submitted using a deleted repository ID."""
    # State has a deleted repo ID
    st.session_state["selected_repo_id"] = 999
    st.session_state["selected_repo"] = {"id": 999, "name": "phantom-repo"}

    # Backend says only repo 1 exists
    with patch.object(client, "list_repositories", return_value=[{"id": 1, "name": "live-repo"}]):
        verified_repo, err = validate_selected_repository(client)

        assert verified_repo is None
        assert "is no longer available" in err
        assert st.session_state["selected_repo_id"] is None
        assert st.session_state["selected_repo"] is None


def test_validate_selected_repository_backend_empty(client):
    """Confirm that investigation cannot submit when backend has 0 repositories."""
    st.session_state["selected_repo_id"] = 1
    st.session_state["selected_repo"] = {"id": 1, "name": "crumbs"}

    with patch.object(client, "list_repositories", return_value=[]):
        verified_repo, err = validate_selected_repository(client)

        assert verified_repo is None
        assert "No repositories are indexed" in err
        assert st.session_state["selected_repo_id"] is None


def test_validate_selected_repository_success(client):
    """Test successful validation of an existing repository."""
    st.session_state["selected_repo_id"] = 2

    repos_data = [
        {"id": 1, "name": "other-repo"},
        {"id": 2, "name": "crumbs", "url": "https://github.com/dogukanayd/crumbs"},
    ]
    with patch.object(client, "list_repositories", return_value=repos_data):
        verified_repo, err = validate_selected_repository(client)

        assert err is None
        assert verified_repo["id"] == 2
        assert verified_repo["name"] == "crumbs"
        assert st.session_state["selected_repo_id"] == 2
        assert st.session_state["selected_repo"] == verified_repo



def test_repository_service_list_and_delete_with_session():
    from backend.app.services.repository_service import RepositoryService
    from backend.app.models.repository import Repository
    
    mock_db = MagicMock()
    svc = RepositoryService()
    
    # Test list_repositories
    repo1 = Repository(id=1, url="https://github.com/org/repo1", name="repo1", status="indexed", local_path="/tmp/1")
    repo2 = Repository(id=2, url="https://github.com/org/repo2", name="repo2", status="cloned", local_path="/tmp/2")
    
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = [repo1, repo2]
    mock_db.scalars.return_value = mock_scalars
    
    listed = svc.list_repositories(mock_db)
    assert len(listed) == 2
    assert listed[0]["id"] == 1
    assert listed[0]["name"] == "repo1"
    assert listed[1]["id"] == 2
    
    # Test delete_repository not found
    mock_db.get.return_value = None
    assert svc.delete_repository(mock_db, 999) is False
    
    # Test delete_repository found
    mock_db.get.return_value = repo1
    mock_db.scalars.return_value.all.return_value = []
    assert svc.delete_repository(mock_db, 1) is True
    mock_db.delete.assert_called_with(repo1)
    mock_db.commit.assert_called()


def test_history_manager_save_load_clear(tmp_path, monkeypatch):
    from frontend.utils import history_manager
    test_file = tmp_path / "test_history.json"
    monkeypatch.setattr(history_manager, "HISTORY_FILE", test_file)

    st.session_state["investigation_history"] = []
    # 1. Initially empty
    loaded = history_manager.load_history()
    assert loaded == []

    # 2. Save an entry
    entry1 = {
        "repository_id": 1,
        "repository_name": "repo-one",
        "query": "Where is auth?",
        "timestamp": 123456.78,
        "confidence": "high",
        "response": {"answer": "auth.py"},
    }
    history_manager.save_history_entry(entry1)
    assert len(st.session_state["investigation_history"]) == 1
    assert st.session_state["investigation_history"][0]["query"] == "Where is auth?"
    assert test_file.exists()

    # 3. Save a second entry
    entry2 = {
        "repository_id": 1,
        "repository_name": "repo-one",
        "query": "Where is DB connection?",
        "timestamp": 123457.0,
        "confidence": "partial",
        "response": {"answer": "db.py"},
    }
    history_manager.save_history_entry(entry2)
    assert len(st.session_state["investigation_history"]) == 2
    assert st.session_state["investigation_history"][0]["query"] == "Where is DB connection?"

    # 4. Clear state and verify load_history restores from file
    st.session_state.clear()
    loaded_restored = history_manager.load_history()
    assert len(loaded_restored) == 2
    assert loaded_restored[0]["query"] == "Where is DB connection?"

    # 5. Clear history
    history_manager.clear_history()
    assert st.session_state["investigation_history"] == []
    assert not test_file.exists()


def test_history_manager_deduplicates_and_limits(tmp_path, monkeypatch):
    from frontend.utils import history_manager
    test_file = tmp_path / "test_history2.json"
    monkeypatch.setattr(history_manager, "HISTORY_FILE", test_file)
    monkeypatch.setattr(history_manager, "MAX_HISTORY_ENTRIES", 3)

    st.session_state.clear()

    # Save 4 items
    for i in range(4):
        history_manager.save_history_entry({
            "repository_id": 1,
            "query": f"Query {i}",
        })

    # Capped at 3
    assert len(st.session_state["investigation_history"]) == 3
    assert st.session_state["investigation_history"][0]["query"] == "Query 3"

    # Re-saving same query pushes it to the front without duplicating
    history_manager.save_history_entry({
        "repository_id": 1,
        "query": "Query 1",
    })
    queries = [item["query"] for item in st.session_state["investigation_history"]]
    assert queries[0] == "Query 1"
    assert queries.count("Query 1") == 1
