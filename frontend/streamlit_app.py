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
import streamlit.components.v1 as components
from frontend.api_client import ApiClient
from frontend.components.sidebar import render_sidebar
from frontend.components.investigation_view import render_investigation_view
from frontend.components.repository_view import render_repository_view
from frontend.utils.repo_manager import sync_repositories
from frontend.utils.history_manager import load_history
from frontend.components.status_view import render_status_view

# Page Configuration
st.set_page_config(
    page_title="OpenSource Investigator",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Professional Developer-Focused Dark Custom CSS
CUSTOM_CSS = """
<style>
    /* ══════════════════════════════════════════════════════════════
       SIDEBAR — FIXED, NO COLLAPSE, WIDER
    ══════════════════════════════════════════════════════════════ */
    section[data-testid="stSidebar"] {
        background-color: #0d1117 !important;
        border-right: 1px solid #21262d !important;
        min-width: 310px !important;
        max-width: 320px !important;
        width: 315px !important;
    }

    /* ── Hide the entire sidebar header div (logo spacer + collapse button) ── */
    /* Actual element is a div, NOT a header tag — selector must match exactly   */
    div[data-testid="stSidebarHeader"],
    div[data-testid="stLogoSpacer"],
    div[data-testid="stSidebarCollapseButton"],
    div[data-testid="collapsedControl"],
    button[aria-label="Collapse sidebar"],
    button[aria-label="Expand sidebar"],
    button[aria-label="Close sidebar"],
    button[aria-label="Open sidebar"] {
        display: none !important;
        visibility: hidden !important;
        height: 0 !important;
        min-height: 0 !important;
        width: 0 !important;
        padding: 0 !important;
        margin: 0 !important;
        overflow: hidden !important;
        pointer-events: none !important;
    }

    /* Sidebar inner content: starts flush at top, good horizontal breathing room */
    div[data-testid="stSidebarUserContent"] {
        padding: 28px 18px 20px 18px !important;
    }

    /* Remove Streamlit's default extra gap between each widget block in sidebar */
    div[data-testid="stSidebar"] div[data-testid="stVerticalBlock"] {
        gap: 0 !important;
    }
    div[data-testid="stSidebar"] div[data-testid="stVerticalBlock"] > div {
        padding-bottom: 0 !important;
        margin-bottom: 0 !important;
    }

    /* ── Sidebar Brand Card ── */
    .sidebar-brand {
        display: flex;
        align-items: center;
        gap: 12px;
        padding: 0 0 14px 0;
        border-bottom: 1px solid #21262d;
        margin-bottom: 16px;
    }
    .sidebar-brand-badge {
        width: 40px;
        height: 40px;
        background: #161b22;
        border: 1px solid #30363d;
        border-radius: 10px;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 1.3rem;
        flex-shrink: 0;
    }
    .sidebar-brand-text {
        display: flex;
        flex-direction: column;
        overflow: hidden;
        min-width: 0;
    }
    .sidebar-brand-title {
        font-size: 1.05rem;
        font-weight: 700;
        color: #e6edf3;
        line-height: 1.3;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    .sidebar-brand-sub {
        font-size: 0.77rem;
        color: #8b949e;
        line-height: 1.2;
        margin-top: 2px;
        white-space: nowrap;
    }

    /* ── Section Labels ── */
    .sidebar-section-label {
        font-size: 0.78rem !important;
        font-weight: 700 !important;
        letter-spacing: 0.06em !important;
        text-transform: uppercase !important;
        color: #8b949e !important;
        margin: 0 0 8px 0 !important;
        display: block !important;
        padding: 0 !important;
    }

    /* ── Navigation Radio Pills ── */
    div[data-testid="stSidebar"] div[data-testid="stRadio"] > label {
        display: none !important;
    }
    div[data-testid="stSidebar"] div[data-testid="stRadio"] div[role="radiogroup"] {
        gap: 3px !important;
        padding-bottom: 0 !important;
    }
    div[data-testid="stSidebar"] div[data-testid="stRadio"] div[role="radiogroup"] label > div:first-child,
    div[data-testid="stSidebar"] div[data-testid="stRadioOption"] > label > div:first-child,
    div[data-testid="stSidebar"] div[data-testid="stRadioOption"] input[type="radio"],
    div[data-testid="stSidebar"] div[data-testid="stRadio"] input[type="radio"] {
        display: none !important;
        width: 0 !important;
        height: 0 !important;
        opacity: 0 !important;
    }
    div[data-testid="stSidebar"] div[data-testid="stRadio"] div[role="radiogroup"] label,
    div[data-testid="stSidebar"] div[data-testid="stRadioOption"] label {
        display: flex !important;
        align-items: center !important;
        width: 100% !important;
        padding: 9px 12px !important;
        margin: 0 !important;
        border-radius: 6px !important;
        font-size: 0.95rem !important;
        font-weight: 500 !important;
        color: #8b949e !important;
        background: transparent !important;
        border: 1px solid transparent !important;
        cursor: pointer !important;
        transition: all 0.15s ease !important;
    }
    div[data-testid="stSidebar"] div[data-testid="stRadio"] div[role="radiogroup"] label:hover,
    div[data-testid="stSidebar"] div[data-testid="stRadioOption"] label:hover {
        background: #161b22 !important;
        color: #c9d1d9 !important;
    }
    div[data-testid="stSidebar"] div[data-testid="stRadio"] div[role="radiogroup"] label:has(input:checked),
    div[data-testid="stSidebar"] div[data-testid="stRadioOption"] label:has(input:checked) {
        background: #161b22 !important;
        border-color: #30363d !important;
        color: #58a6ff !important;
        font-weight: 600 !important;
    }

    /* ── Dividers inside sidebar ── */
    div[data-testid="stSidebar"] hr,
    hr.sidebar-divider {
        margin: 14px 0 !important;
        border: none !important;
        border-top: 1px solid #21262d !important;
    }

    /* ── Repo status row ── */
    .repo-status-row {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-top: 6px;
        font-size: 0.76rem;
    }

    /* ── Small caption text in sidebar ── */
    .sidebar-caption {
        font-size: 0.8rem;
        color: #8b949e;
        margin: 4px 0 0 0;
        padding: 0;
    }

    /* ── Error message in sidebar ── */
    .sidebar-error-msg {
        font-size: 0.77rem;
        color: #f85149;
        margin: 4px 0 6px 0;
        padding: 0;
    }

    /* ── Recent query history buttons ── */
    div[data-testid="stSidebar"] div[data-testid="stButton"] button {
        font-size: 0.88rem !important;
        text-align: left !important;
        justify-content: flex-start !important;
        background: #0d1117 !important;
        border: 1px solid #21262d !important;
        border-radius: 6px !important;
        color: #c9d1d9 !important;
        padding: 6px 10px !important;
        margin-bottom: 3px !important;
        box-shadow: none !important;
        transition: all 0.15s ease !important;
    }
    div[data-testid="stSidebar"] div[data-testid="stButton"] button:hover {
        background: #161b22 !important;
        border-color: #388bfd !important;
        color: #58a6ff !important;
    }
    /* Clear history — subtle dashed style */
    div[data-testid="stSidebar"] div[data-testid="stButton"]:has(button[data-testid*="btn_clear_hist"]) button,
    div[data-testid="stSidebar"] div[data-testid="stButton"]:last-of-type button {
        background: transparent !important;
        border: 1px dashed #30363d !important;
        color: #6e7681 !important;
        font-size: 0.8rem !important;
        margin-top: 6px !important;
        justify-content: center !important;
    }
    div[data-testid="stSidebar"] div[data-testid="stButton"]:last-of-type button:hover {
        border-color: #f85149 !important;
        color: #f85149 !important;
        background: rgba(248,81,73,0.08) !important;
    }

    /* ── Selectbox inside sidebar ── */
    div[data-testid="stSidebar"] div[data-testid="stSelectbox"] {
        margin-top: 2px !important;
        margin-bottom: 4px !important;
    }

    /* ══════════════════════════════════════════════════════════════
       MAIN CONTENT AREA
    ══════════════════════════════════════════════════════════════ */
    .block-container {
        padding-top: 1.5rem !important;
        padding-bottom: 3rem !important;
        max-width: 1200px !important;
    }

    /* ── Code blocks ── */
    pre, pre code {
        font-family: 'JetBrains Mono', 'Fira Code', 'Consolas', monospace !important;
        font-size: 0.87rem !important;
        border-radius: 6px !important;
    }

    /* ── Metric cards ── */
    div[data-testid="stMetric"] {
        background-color: #161b22 !important;
        border: 1px solid #21262d !important;
        border-radius: 8px !important;
        padding: 10px 14px !important;
    }
    div[data-testid="stMetric"] label {
        color: #8b949e !important;
        font-size: 0.78rem !important;
        font-weight: 500 !important;
    }
    div[data-testid="stMetric"] div[data-testid="stMetricValue"] {
        color: #58a6ff !important;
        font-size: 1.4rem !important;
        font-weight: 700 !important;
    }

    /* ── Badges & Status indicators ── */
    .status-badge {
        display: inline-block;
        padding: 2px 8px;
        border-radius: 12px;
        font-size: 0.75rem;
        font-weight: 600;
        letter-spacing: 0.03em;
    }
    .badge-high {
        background-color: #0f291e;
        color: #3fb950;
        border: 1px solid #1a4731;
    }
    .badge-partial {
        background-color: #2e1f00;
        color: #d29922;
        border: 1px solid #4d3800;
    }
    .badge-insufficient {
        background-color: #2b0b08;
        color: #f85149;
        border: 1px solid #4a1510;
    }

    /* ── Citation Cards ── */
    .citation-card {
        border-left: 3px solid #58a6ff;
        background-color: #161b22;
        border: 1px solid #21262d;
        border-left: 3px solid #58a6ff;
        border-radius: 6px;
        padding: 10px 14px;
        margin-bottom: 8px;
    }
    .citation-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        font-size: 0.85rem;
        font-weight: 600;
        color: #58a6ff;
        margin-bottom: 4px;
    }
    .citation-relevance {
        font-size: 0.82rem;
        color: #8b949e;
        margin-bottom: 6px;
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

    /* ── Tooltip dismiss on button click & focus ── */
    body:has(button:active) div[data-testid="stTooltipContent"],
    body:has(button:active) .stTooltipContent,
    body:has(button:active) [data-baseweb="tooltip"],
    body:has(button:focus:not(:focus-visible)) div[data-testid="stTooltipContent"],
    body:has(button:focus:not(:focus-visible)) .stTooltipContent,
    body:has(button:focus:not(:focus-visible)) [data-baseweb="tooltip"] {
        display: none !important;
        opacity: 0 !important;
        visibility: hidden !important;
        pointer-events: none !important;
    }

    /* ── Hide 0-height iframe container used for tooltip fix ── */
    iframe[height="0"] {
        display: none !important;
        position: absolute !important;
        width: 0 !important;
        height: 0 !important;
        border: none !important;
    }
    div[data-testid="stCustomComponentV1"]:has(iframe[height="0"]) {
        display: none !important;
        height: 0 !important;
        margin: 0 !important;
        padding: 0 !important;
    }
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


def init_session_state() -> None:
    """Initialize persistent Streamlit session state fields."""
    if "repositories" not in st.session_state:
        st.session_state["repositories"] = []

    if "repositories_loaded" not in st.session_state:
        st.session_state["repositories_loaded"] = False

    if "repositories_error" not in st.session_state:
        st.session_state["repositories_error"] = None

    if "selected_repo_id" not in st.session_state:
        st.session_state["selected_repo_id"] = None

    if "selected_repo" not in st.session_state:
        st.session_state["selected_repo"] = None

    if "active_page" not in st.session_state:
        st.session_state["active_page"] = "investigation"

    if "query_input_text" not in st.session_state:
        st.session_state["query_input_text"] = ""

    if "latest_investigation" not in st.session_state:
        st.session_state["latest_investigation"] = None

    if "investigation_history" not in st.session_state:
        load_history()

    if "is_investigating" not in st.session_state:
        st.session_state["is_investigating"] = False


def inject_tooltip_fix() -> None:
    """Ensure tooltips close immediately when a button is clicked while preserving hover."""
    components.html(
        """
        <script>
        (function() {
            try {
                const doc = window.parent.document;
                if (!doc || doc.__stTooltipFixAttached) return;
                doc.__stTooltipFixAttached = true;

                function closeTooltip(target) {
                    const btn = target.closest('button');
                    if (!btn) return;

                    // 1. Remove focus so focus-triggered tooltip closes
                    btn.blur();

                    // 2. Dispatch pointerleave and mouseleave to trigger React hover close
                    try {
                        btn.dispatchEvent(new PointerEvent('pointerleave', { bubbles: true, cancelable: true }));
                        btn.dispatchEvent(new MouseEvent('mouseleave', { bubbles: true, cancelable: true }));
                    } catch (e) {}

                    // 3. Force-hide active tooltip portals in DOM
                    try {
                        const tooltips = doc.querySelectorAll(
                            '[data-testid="stTooltipContent"], .stTooltipContent, [data-baseweb="tooltip"]'
                        );
                        tooltips.forEach(function(t) {
                            t.style.display = 'none';
                            t.style.visibility = 'hidden';
                            t.style.opacity = '0';
                            const portal = t.closest('[data-floating-ui-portal], [data-baseweb="popover"]');
                            if (portal) {
                                portal.style.display = 'none';
                                portal.style.visibility = 'hidden';
                            }
                        });
                    } catch (e) {}
                }

                doc.addEventListener('pointerdown', function(e) {
                    closeTooltip(e.target);
                }, true);

                doc.addEventListener('click', function(e) {
                    closeTooltip(e.target);
                }, true);
            } catch (err) {}
        })();
        </script>
        """,
        height=0,
        width=0,
    )


def main() -> None:
    inject_tooltip_fix()
    init_session_state()

    # Instantiate API client
    api_base_url = os.environ.get("API_BASE_URL", "http://127.0.0.1:8000")
    api_client = ApiClient(base_url=api_base_url)

    # Synchronize repository list with backend on load (Backend is single source of truth)
    sync_repositories(api_client)

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
