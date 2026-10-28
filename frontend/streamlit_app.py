"""
OpenSource Investigator — Polished Streamlit Application.
Evidence-grounded code investigation interface powered by LangGraph & FastAPI.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure project root is in sys.path so frontend imports work cleanly
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# Load .env if python-dotenv is installed
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT_DIR / ".env")
    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass

import streamlit as st
from frontend.api_client import ApiClient
from frontend.components.sidebar import render_sidebar
from frontend.components.investigation_view import render_investigation_view
from frontend.components.repository_view import (
    render_repository_view,
    DEFAULT_PRESET_REPOSITORIES,
)
from frontend.components.status_view import render_status_view

# Page Configuration
st.set_page_config(
    page_title="OpenSource Investigator",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Professional Developer-Focused Dark Custom CSS
CUSTOM_CSS = """
<style>
    /* ── Layout ── */
    .block-container {
        padding-top: 1.5rem;
        padding-bottom: 3rem;
        max-width: 1180px;
    }

    /* ── Sidebar: remove default top padding so header sits flush ── */
    section[data-testid="stSidebar"] > div:first-child {
        padding-top: 0 !important;
    }
    section[data-testid="stSidebar"] {
        background-color: #0d1117 !important;
        border-right: 1px solid #21262d !important;
    }

    /* ── Sidebar header card ── */
    .sidebar-header {
        display: flex;
        align-items: center;
        gap: 10px;
        padding: 18px 16px 14px 16px;
        border-bottom: 1px solid #21262d;
        margin-bottom: 4px;
    }
    .sidebar-icon {
        font-size: 1.5rem;
        line-height: 1;
        flex-shrink: 0;
    }
    .sidebar-title {
        font-size: 1.05rem;
        font-weight: 700;
        color: #58a6ff;
        margin: 0;
        line-height: 1.2;
    }
    .sidebar-subtitle {
        font-size: 0.72rem;
        color: #6e7681;
        margin: 2px 0 0 0;
        line-height: 1.3;
    }

    /* ── Nav radio pills ── */
    div[data-testid="stRadio"] > label {
        display: none;
    }
    div[data-testid="stRadio"] > div {
        gap: 4px !important;
    }
    div[data-testid="stRadio"] > div > label {
        border-radius: 6px !important;
        padding: 7px 12px !important;
        font-size: 0.88rem !important;
        font-weight: 500 !important;
        transition: background 0.15s ease !important;
    }

    /* ── Section headers inside sidebar ── */
    .sidebar-section-label {
        font-size: 0.68rem;
        font-weight: 600;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: #6e7681;
        padding: 0 4px;
        margin: 2px 0 4px 0;
    }

    /* ── Sidebar history buttons ── */
    div[data-testid="stSidebar"] button {
        font-size: 0.82rem !important;
        text-align: left !important;
        padding: 5px 10px !important;
    }

    /* ── Code blocks ── */
    pre, pre code {
        font-family: 'JetBrains Mono', 'Fira Code', 'Consolas', monospace !important;
        font-size: 0.87rem !important;
        border-radius: 6px !important;
    }

    /* ── Metric cards ── */
    div[data-testid="stMetric"] {
        background: #161b22;
        border: 1px solid #21262d;
        border-radius: 8px;
        padding: 12px 16px !important;
    }
    div[data-testid="stMetricValue"] {
        font-size: 1.25rem !important;
        font-weight: 700 !important;
        color: #58a6ff !important;
    }
    div[data-testid="stMetricLabel"] {
        font-size: 0.75rem !important;
        text-transform: uppercase !important;
        letter-spacing: 0.06em !important;
        color: #8b949e !important;
    }

    /* ── Expander cards ── */
    div[data-testid="stExpander"] {
        border: 1px solid #21262d !important;
        border-radius: 8px !important;
        background-color: #161b22 !important;
        margin-bottom: 0.6rem !important;
    }

    /* ── Primary buttons ── */
    button[kind="primary"] {
        background-color: #238636 !important;
        border-color: rgba(240,246,252,0.1) !important;
        font-weight: 600 !important;
        border-radius: 6px !important;
    }
    button[kind="primary"]:hover {
        background-color: #2ea043 !important;
    }

    /* ── Info / warning / error alerts ── */
    div[data-testid="stAlert"] {
        border-radius: 8px !important;
        font-size: 0.9rem !important;
    }

    /* ── Tables ── */
    table th {
        background-color: #161b22 !important;
        color: #c9d1d9 !important;
        font-size: 0.78rem !important;
        text-transform: uppercase !important;
        letter-spacing: 0.04em;
    }
    table td {
        font-size: 0.86rem !important;
    }

    /* ── Divider thinning ── */
    hr {
        margin: 1rem 0 !important;
        border-color: #21262d !important;
    }

    /* ── Page headings ── */
    h1, h2 { color: #c9d1d9 !important; }
    h3, h4, h5 { color: #e6edf3 !important; }
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


def init_session_state() -> None:
    """Initialize persistent Streamlit session state fields."""
    if "session_repositories" not in st.session_state:
        st.session_state["session_repositories"] = list(DEFAULT_PRESET_REPOSITORIES)

    if "selected_repo_id" not in st.session_state:
        st.session_state["selected_repo_id"] = 2  # Default to Crumbs

    if "selected_repo" not in st.session_state:
        st.session_state["selected_repo"] = DEFAULT_PRESET_REPOSITORIES[0]

    if "active_page" not in st.session_state:
        st.session_state["active_page"] = "investigation"

    if "query_input_text" not in st.session_state:
        st.session_state["query_input_text"] = ""

    if "latest_investigation" not in st.session_state:
        st.session_state["latest_investigation"] = None

    if "investigation_history" not in st.session_state:
        st.session_state["investigation_history"] = []

    if "is_investigating" not in st.session_state:
        st.session_state["is_investigating"] = False


def main() -> None:
    init_session_state()

    # Instantiate API client
    api_base_url = os.environ.get("API_BASE_URL", "http://127.0.0.1:8000")
    api_client = ApiClient(base_url=api_base_url)

    # Render Sidebar and retrieve active page
    selected_page = render_sidebar(api_client)

    # Route to Views
    if selected_page == "investigation":
        render_investigation_view(api_client)
    elif selected_page == "repositories":
        render_repository_view(api_client)
    elif selected_page == "status":
        render_status_view(api_client)


if __name__ == "__main__":
    main()
