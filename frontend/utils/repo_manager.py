"""
Repository state management and synchronization with the FastAPI backend.
Ensures the backend database is the single source of truth for repositories.
"""

from __future__ import annotations

try:
    import streamlit as st
except ImportError:  # pragma: no cover
    class _DummyStreamlit:
        """Fallback stub for Streamlit when not installed (e.g., during testing)."""
        session_state: dict = {}
    st = _DummyStreamlit()
from typing import Any
from frontend.api_client import (
    ApiClient,
    BackendConnectionError,
    BackendTimeoutError,
    EndpointNotImplementedError,
    ApiClientError,
)
from frontend.utils.errors import clear_error, report_error

REPO_SYNC_ERROR_KEY = "repository-sync"


def sync_repositories(
    api_client: ApiClient, force_refresh: bool = False
) -> tuple[list[dict[str, Any]], str | None]:
    """
    Synchronize the repository list with the FastAPI backend.
    
    Returns:
        tuple: (repositories_list, error_message)
        - On success: (list_of_repos, None).
        - On failure: (cached_or_empty_list, error_message).
    """
    # If already loaded and not force_refresh, return current state
    if not force_refresh and st.session_state.get("repositories_loaded", False):
        return (
            st.session_state.get("repositories", []),
            st.session_state.get("repositories_error"),
        )

    try:
        repos = api_client.list_repositories()
        if not isinstance(repos, list):
            repos = []

        # Update session state with fresh data from backend
        st.session_state["repositories"] = repos
        st.session_state["repositories_loaded"] = True
        st.session_state["repositories_error"] = None

        # Re-validate the selected repository against the fresh list
        current_id = st.session_state.get("selected_repo_id")
        repo_lookup = {r["id"]: r for r in repos if isinstance(r, dict) and "id" in r}

        if current_id is not None and current_id in repo_lookup:
            # Sync to the exact fresh record
            st.session_state["selected_repo"] = repo_lookup[current_id]
        else:
            # Task 5: Remove any selected repository ID that is no longer valid
            st.session_state["selected_repo_id"] = None
            st.session_state["selected_repo"] = None

        clear_error(REPO_SYNC_ERROR_KEY)
        return repos, None

    except EndpointNotImplementedError as exc:
        err = f"GET /repositories endpoint is not implemented by backend: {exc}"
        st.session_state["repositories_loaded"] = True
        st.session_state["repositories"] = []
        st.session_state["repositories_error"] = err
        st.session_state["selected_repo_id"] = None
        st.session_state["selected_repo"] = None
        report_error("The backend doesn't support listing repositories", exc, key=REPO_SYNC_ERROR_KEY)
        return [], err

    except (BackendConnectionError, BackendTimeoutError, ApiClientError, Exception) as exc:
        err = f"Backend repository API error: {exc}"
        st.session_state["repositories_error"] = err
        report_error("Couldn't load the repository list", exc, key=REPO_SYNC_ERROR_KEY)
        # If never initialized, ensure repositories list is empty
        if "repositories" not in st.session_state:
            st.session_state["repositories"] = []
            st.session_state["selected_repo_id"] = None
            st.session_state["selected_repo"] = None
        return st.session_state.get("repositories", []), err


def invalidate_repositories_cache() -> None:
    """Mark repository cache as stale so next access will re-fetch from FastAPI."""
    st.session_state["repositories_loaded"] = False


def validate_selected_repository(
    api_client: ApiClient,
) -> tuple[dict[str, Any] | None, str | None]:
    """
    Force-refresh the repository list from the backend and verify that the selected
    repository ID still exists in PostgreSQL.
    
    Returns:
        tuple: (selected_repo_dict, error_message)
    """
    target_id = st.session_state.get("selected_repo_id")
    if target_id is None:
        return None, "No repository is currently selected."

    repos, err = sync_repositories(api_client, force_refresh=True)

    if err:
        return None, f"Cannot verify target repository: {err}"

    if not repos:
        st.session_state["selected_repo_id"] = None
        st.session_state["selected_repo"] = None
        return None, "No repositories are indexed or registered in the backend."

    repo_lookup = {r["id"]: r for r in repos if isinstance(r, dict) and "id" in r}

    if target_id not in repo_lookup:
        # Invalidated or deleted
        st.session_state["selected_repo_id"] = None
        st.session_state["selected_repo"] = None
        return (
            None,
            f"The selected repository (ID #{target_id}) is no longer available on the backend.",
        )

    # Valid and synchronized
    current_record = repo_lookup[target_id]
    st.session_state["selected_repo"] = current_record
    st.session_state["selected_repo_id"] = target_id
    return current_record, None
