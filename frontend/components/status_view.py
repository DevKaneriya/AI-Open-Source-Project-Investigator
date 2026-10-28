"""
System Status component: backend health inspection, reachability diagnostics,
and dependency transparency matrix.
"""

from __future__ import annotations

import time
import streamlit as st
from typing import Any
from frontend.api_client import (
    ApiClient,
    BackendConnectionError,
    BackendTimeoutError,
    ApiClientError,
)
from frontend.utils.formatting import format_timestamp, format_execution_time


def render_status_view(api_client: ApiClient) -> None:
    """Render backend process health diagnostics and architectural status."""
    st.markdown(
        """
        <div style="margin-bottom: 4px;">
            <h2 style="margin:0; color:#e6edf3;">🚦 System Status &amp; Diagnostics</h2>
            <p style="margin:4px 0 0 0; color:#8b949e; font-size:0.9rem;">
                Real-time health check of the FastAPI backend and subsystem transparency matrix.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    col_btn, col_empty = st.columns([1, 5], gap="small")
    with col_btn:
        recheck = st.button("🔄 Check Backend Health", type="primary", use_container_width=True)

    # Perform Health Check Probe
    health_data: dict[str, Any] | None = None
    health_error: str | None = None
    latency: float | None = None

    t0 = time.time()
    try:
        health_data = api_client.health_check()
        latency = time.time() - t0
    except BackendConnectionError as exc:
        health_error = f"Connection Refused: Cannot reach backend at {api_client.base_url}."
    except BackendTimeoutError as exc:
        health_error = f"Timeout: Backend health endpoint did not respond within threshold."
    except ApiClientError as exc:
        health_error = f"API Error ({exc.status_code}): {exc.message}"
    except Exception as exc:
        health_error = f"Unexpected health probe failure: {exc}"

    st.markdown("---")

    # Primary Health Status Card
    if health_data and health_data.get("status") == "healthy":
        st.success("🟢 **Backend Process Reachable & Healthy**")
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.metric("Process Status", "Healthy")
        with c2:
            st.metric("Service Name", health_data.get("service", "N/A"))
        with c3:
            st.metric("Response Latency", format_execution_time(latency))
        with c4:
            st.metric("Server Timestamp", format_timestamp(health_data.get("timestamp")))

        with st.expander("📄 Raw Health Check Response", expanded=False):
            st.json(health_data)
    else:
        st.error(f"🔴 **Backend Unreachable / Unhealthy**\n\n{health_error or 'Unknown failure'}")
        st.warning(
            f"**Troubleshooting Guide:**\n"
            f"1. Verify that the FastAPI backend server is running: `python -m uvicorn backend.app.main:app --port 8000`\n"
            f"2. Check that the configured URL matches: `{api_client.base_url}`\n"
            f"3. Check your network or local firewall settings.",
            icon="🛠️",
        )

    # ── Subsystem Matrix ────────────────────────────────────────────────────────
    st.markdown(
        '<p style="font-size:0.78rem; font-weight:600; text-transform:uppercase; letter-spacing:0.07em; color:#6e7681; margin:16px 0 6px 0;">🧩 Subsystem Verification Matrix</p>',
        unsafe_allow_html=True,
    )
    st.caption(
        "To maintain factual integrity, the system distinguishes the verified API process "
        "health check (`GET /health`) from internal subsystems that are exercised during operations."
    )

    matrix_data = [
        {
            "Subsystem": "FastAPI Web Server",
            "Target": api_client.base_url,
            "Verification Endpoint": "`GET /health`",
            "Status": "🟢 Verified Online" if health_data else "🔴 Unreachable",
            "Notes": "Process event loop is actively accepting HTTP requests.",
        },
        {
            "Subsystem": "PostgreSQL & SQLAlchemy",
            "Target": "Local / Remote DB",
            "Verification Endpoint": "Operational queries (`/repositories/*`)",
            "Status": "🔵 Operational via Backend",
            "Notes": "Tables managed via Alembic. Exercised during repository and search operations.",
        },
        {
            "Subsystem": "Qdrant Vector Database",
            "Target": "data/qdrant / Local storage",
            "Verification Endpoint": "Operational queries (`/semantic-search`)",
            "Status": "🔵 Operational via Backend",
            "Notes": "Holds chunk embeddings (`repository_code` collection). Accessed via VectorService.",
        },
        {
            "Subsystem": "Groq LLM Service",
            "Target": "Groq API (Llama 3 / DeepSeek)",
            "Verification Endpoint": "Investigation pipeline (`/investigate`)",
            "Status": "🔵 Operational via Backend",
            "Notes": "Executes query analysis, evidence evaluation, answer generation, and citation repairs.",
        },
        {
            "Subsystem": "Sentence Transformers",
            "Target": "BAAI/bge-small-en-v1.5",
            "Verification Endpoint": "Retrieval pipeline (`/retrieve`)",
            "Status": "🔵 Operational via Backend",
            "Notes": "Generates 384-dimensional dense semantic embeddings.",
        },
    ]

    st.table(matrix_data)
