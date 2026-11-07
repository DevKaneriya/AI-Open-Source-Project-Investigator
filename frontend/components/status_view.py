"""
System Status component: backend health inspection, reachability diagnostics,
and an overview of the subsystems the backend depends on.
"""

from __future__ import annotations

import html
import time
from typing import Any

import streamlit as st

from frontend.api_client import (
    ApiClient,
    BackendConnectionError,
    BackendTimeoutError,
    ApiClientError,
)
from frontend.utils.formatting import format_timestamp, format_execution_time

SUBSYSTEMS = [
    ("PostgreSQL", "Stores repositories, files, symbols, imports and chunks.", "Checked through repository requests"),
    ("Qdrant", "Local vector database holding chunk embeddings for semantic search.", "Checked through indexing and search"),
    ("Groq LLM", "Analyses questions, evaluates evidence, writes and verifies answers.", "Checked through investigations"),
    ("BGE embeddings", "BAAI/bge-small-en-v1.5 model producing 384-dimensional vectors.", "Checked through indexing and search"),
]


def _probe(api_client: ApiClient) -> tuple[dict[str, Any] | None, str | None, float | None]:
    started = time.time()
    try:
        return api_client.health_check(), None, time.time() - started
    except BackendConnectionError:
        return None, f"Connection refused at {api_client.base_url}.", None
    except BackendTimeoutError:
        return None, "The health endpoint didn't respond in time.", None
    except ApiClientError as exc:
        return None, f"API error ({exc.status_code}): {exc.message}", None


def render_status_view(api_client: ApiClient) -> None:
    st.html(
        """
        <div class="oi-page-header">
            <span class="oi-eyebrow">System status</span>
            <h1>Backend health</h1>
            <p>Check that the API server is reachable before adding repositories or running investigations.</p>
        </div>
        """
    )

    health, error, latency = _probe(api_client)
    healthy = bool(health) and health.get("status") == "healthy"

    if healthy:
        st.success("**Backend is online and healthy.**", icon=":material/check_circle:")
        c1, c2 = st.columns(2)
        with c1:
            st.metric("Status", "Healthy")
        with c2:
            st.metric("Response time", format_execution_time(latency))
        st.caption(
            f"{health.get('service', 'Backend')} · {api_client.base_url} · "
            f"server time {format_timestamp(health.get('timestamp'))}"
        )
        with st.expander("Raw health response", icon=":material/data_object:"):
            st.json(health)
    else:
        st.error(f"**Backend is unreachable.** {error or 'Unknown failure.'}", icon=":material/cloud_off:")
        with st.container(border=True):
            st.markdown("**How to fix it**")
            st.markdown(
                "1. From the project folder, start the server:\n"
                "   ```powershell\n   .\\start_server.ps1\n   ```\n"
                "   or run `python -m uvicorn backend.app.main:app --port 8000`.\n"
                f"2. Make sure the frontend points at the right URL: `{api_client.base_url}` "
                "(set `API_BASE_URL` in `.env` to change it).\n"
                "3. Click **Re-check**."
            )

    st.button("Re-check", icon=":material/refresh:", key="status_recheck")

    st.html('<div class="oi-section-label" style="margin-top:1.5rem">Subsystems</div>')
    st.caption(
        "Only the API server has a dedicated health check. The services below are used by the "
        "backend and are exercised when you index or investigate."
    )
    # Row by row so cards keep their order when columns stack on small screens
    for row_start in range(0, len(SUBSYSTEMS), 2):
        for col, (name, role, check) in zip(st.columns(2, gap="medium"), SUBSYSTEMS[row_start:row_start + 2]):
            with col:
                st.html(
                    f"""
                    <div class="oi-card" style="margin-bottom:1rem">
                        <h4>{html.escape(name)}</h4>
                        <p>{html.escape(role)}</p>
                        <p style="margin-top:0.5rem; color:var(--oi-faint); font-size:0.8rem">{html.escape(check)}</p>
                    </div>
                    """
                )
