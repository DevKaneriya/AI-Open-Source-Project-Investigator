"""
API client for communicating with the AI Open-Source Project Investigator FastAPI backend.
Handles request routing, timeouts, JSON parsing, and structured domain errors.
"""

from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import urljoin, urlencode
import requests
from requests.exceptions import ConnectionError as ReqConnectionError
from requests.exceptions import Timeout as ReqTimeout
from requests.exceptions import RequestException

logger = logging.getLogger("investigator_api_client")


class ApiClientError(Exception):
    """Base exception for API client operations."""

    def __init__(self, message: str, status_code: int | None = None, response_body: Any = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.response_body = response_body


class BackendConnectionError(ApiClientError):
    """Raised when the backend server is unreachable (connection refused, DNS failure)."""


class BackendTimeoutError(ApiClientError):
    """Raised when a backend request exceeds the timeout threshold."""


class EndpointNotImplementedError(ApiClientError):
    """Raised when an endpoint is not supported or exposed by the backend (404/405 on route)."""


class RepositoryNotFoundError(ApiClientError):
    """Raised when the specified repository ID does not exist."""


class InvestigationError(ApiClientError):
    """Raised when the investigation workflow fails."""


class ApiClient:
    """
    HTTP client for the AI Open-Source Project Investigator FastAPI backend.
    
    Attributes:
        base_url: Root URL for the FastAPI backend (e.g., 'http://127.0.0.1:8000')
        default_timeout: Default timeout in seconds for standard queries
        investigation_timeout: Timeout in seconds for deep LangGraph investigations
    """

    def __init__(
        self,
        base_url: str | None = None,
        default_timeout: float = 15.0,
        investigation_timeout: float = 180.0,
    ):
        raw_url = base_url or os.environ.get("API_BASE_URL", "http://127.0.0.1:8000")
        self.base_url = raw_url.rstrip("/")
        self.default_timeout = default_timeout
        self.investigation_timeout = investigation_timeout
        self._session = requests.Session()
        self._session.headers.update({
            "Accept": "application/json",
            "User-Agent": "OpenSource-Investigator-Frontend/1.0",
        })

    def _url(self, path: str) -> str:
        clean_path = path.lstrip("/")
        return f"{self.base_url}/{clean_path}"

    def _request(
        self,
        method: str,
        path: str,
        timeout: float | None = None,
        params: dict | None = None,
        json_data: Any = None,
    ) -> dict[str, Any]:
        """Execute an HTTP request with structured error translation."""
        url = self._url(path)
        req_timeout = timeout or self.default_timeout

        try:
            response = self._session.request(
                method=method,
                url=url,
                params=params,
                json=json_data,
                timeout=req_timeout,
            )
        except ReqConnectionError as exc:
            msg = f"Cannot connect to backend at {self.base_url}. Ensure the FastAPI server is running."
            logger.warning("Connection failure: %s -> %s", url, exc)
            raise BackendConnectionError(msg) from exc
        except ReqTimeout as exc:
            msg = f"Backend request to {path} timed out after {req_timeout}s."
            logger.warning("Request timeout: %s", url)
            raise BackendTimeoutError(msg) from exc
        except RequestException as exc:
            msg = f"Network or protocol error: {exc}"
            logger.error("Request error: %s -> %s", url, exc)
            raise ApiClientError(msg) from exc

        # Parse JSON or extract text detail
        body: Any = None
        try:
            body = response.json()
        except Exception:
            body = {"raw_text": response.text}

        if response.status_code == 200:
            if isinstance(body, dict):
                return body
            if isinstance(body, list):
                return {"items": body}
            return {"data": body}

        # Handle errors
        detail = ""
        if isinstance(body, dict):
            detail = body.get("detail") or body.get("error") or body.get("message") or ""
        if not detail and isinstance(body, dict) and "raw_text" in body:
            detail = body["raw_text"][:300]

        status = response.status_code

        if status == 404:
            if "not found" in str(detail).lower() and "repository" in str(detail).lower():
                raise RepositoryNotFoundError(f"Repository not found: {detail}", status_code=status, response_body=body)
            # Route itself not found
            raise EndpointNotImplementedError(
                f"Endpoint '{path}' is not implemented or exposed by the backend ({status}): {detail}",
                status_code=status,
                response_body=body,
            )

        if status == 405:
            raise EndpointNotImplementedError(
                f"Method {method} not allowed for '{path}' ({status}): {detail}",
                status_code=status,
                response_body=body,
            )

        if status == 400:
            raise ApiClientError(f"Bad Request: {detail or 'Invalid input parameters'}", status_code=status, response_body=body)

        if status >= 500:
            raise ApiClientError(
                f"Backend server error ({status}): {detail or 'Internal service failure'}",
                status_code=status,
                response_body=body,
            )

        raise ApiClientError(
            f"Unexpected HTTP {status}: {detail or response.reason}",
            status_code=status,
            response_body=body,
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Health Check
    # ──────────────────────────────────────────────────────────────────────────

    def health_check(self) -> dict[str, Any]:
        """
        Check process health of the FastAPI backend via GET /health.
        Returns: {'status': 'healthy', 'service': '...', 'timestamp': '...'}
        """
        return self._request("GET", "/health", timeout=5.0)

    # ──────────────────────────────────────────────────────────────────────────
    # Repositories
    # ──────────────────────────────────────────────────────────────────────────

    def list_repositories(self) -> list[dict[str, Any]]:
        """
        Fetch registered repositories via GET /repositories if available.
        Raises EndpointNotImplementedError if the backend route is missing.
        """
        resp = self._request("GET", "/repositories", timeout=8.0)
        if isinstance(resp, dict) and "items" in resp:
            return resp["items"]
        if isinstance(resp, list):
            return resp
        return []

    def delete_repository(self, repository_id: int) -> dict[str, Any]:
        """
        Delete a repository and its indexed artifacts via DELETE /repositories/{id}.
        """
        return self._request("DELETE", f"/repositories/{repository_id}", timeout=10.0)

    def add_repository(self, repository_url: str) -> dict[str, Any]:
        """
        Register and clone a GitHub repository via POST /repositories.
        Returns: {'id': int, 'url': str, 'name': str, 'status': str, 'local_path': str}
        """
        if not repository_url or not repository_url.strip():
            raise ApiClientError("Repository URL cannot be empty.")
        return self._request(
            "POST",
            "/repositories",
            json_data={"repository_url": repository_url.strip()},
            timeout=120.0,
        )

    def scan_repository(self, repository_url: str) -> dict[str, Any]:
        """
        Scan repository architecture and file hierarchy via POST /repositories/scan.
        """
        if not repository_url or not repository_url.strip():
            raise ApiClientError("Repository URL cannot be empty.")
        return self._request(
            "POST",
            "/repositories/scan",
            json_data={"repository_url": repository_url.strip()},
            timeout=120.0,
        )

    def index_repository(self, repository_id: int) -> dict[str, Any]:
        """
        Parse AST, extract symbols and imports via POST /repositories/{id}/index.
        """
        return self._request(
            "POST",
            f"/repositories/{repository_id}/index",
            timeout=180.0,
        )

    def create_chunks(self, repository_id: int) -> dict[str, Any]:
        """
        Create code chunks for vector indexing via POST /repositories/{id}/chunks.
        """
        return self._request(
            "POST",
            f"/repositories/{repository_id}/chunks",
            timeout=120.0,
        )

    def index_vectors(self, repository_id: int) -> dict[str, Any]:
        """
        Generate BGE embeddings and index chunks into Qdrant via POST /repositories/{id}/vector-index.
        """
        return self._request(
            "POST",
            f"/repositories/{repository_id}/vector-index",
            timeout=240.0,
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Investigation
    # ──────────────────────────────────────────────────────────────────────────

    def investigate(self, repository_id: int, query: str) -> dict[str, Any]:
        """
        Execute LangGraph StateGraph investigation via GET /repositories/{id}/investigate.
        Returns: Complete InvestigationResponse structure.
        """
        clean_query = query.strip()
        if not clean_query:
            raise ApiClientError("Investigation query cannot be empty.")

        try:
            return self._request(
                "GET",
                f"/repositories/{repository_id}/investigate",
                params={"query": clean_query},
                timeout=self.investigation_timeout,
            )
        except (BackendConnectionError, BackendTimeoutError, RepositoryNotFoundError, EndpointNotImplementedError):
            raise
        except ApiClientError as exc:
            raise InvestigationError(str(exc), status_code=exc.status_code, response_body=exc.response_body) from exc

    # ──────────────────────────────────────────────────────────────────────────
    # Search & Inspection Diagnostics
    # ──────────────────────────────────────────────────────────────────────────

    def search_symbols(self, repository_id: int, query: str) -> dict[str, Any]:
        """Search symbol index via GET /repositories/{id}/search."""
        return self._request(
            "GET",
            f"/repositories/{repository_id}/search",
            params={"query": query},
            timeout=15.0,
        )

    def get_source(
        self, repository_id: int, file_path: str, start_line: int, end_line: int
    ) -> dict[str, Any]:
        """Retrieve verified source lines via GET /repositories/{id}/source."""
        return self._request(
            "GET",
            f"/repositories/{repository_id}/source",
            params={
                "file_path": file_path,
                "start_line": start_line,
                "end_line": end_line,
            },
            timeout=10.0,
        )
