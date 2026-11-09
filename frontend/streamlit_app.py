"""
OpenSource Investigator — Streamlit application entry point.
Evidence-grounded code investigation interface powered by LangGraph & FastAPI.

Run from the project root:  streamlit run frontend/streamlit_app.py
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
from frontend.components.guide_view import render_guide_view
from frontend.components.investigation_view import render_investigation_view
from frontend.components.repository_view import render_repository_view
from frontend.components.status_view import render_status_view
from frontend.utils.errors import render_error_dialog, report_error
from frontend.utils.history_manager import load_history
from frontend.utils.navigation import register_pages
from frontend.utils.repo_manager import sync_repositories
from frontend.utils.skeleton import page_skeleton

STYLES_PATH = Path(__file__).resolve().parent / "assets" / "styles.css"

st.set_page_config(
    page_title="OpenSource Investigator",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="auto",
)


def init_session_state() -> None:
    """Initialize persistent Streamlit session state fields."""
    defaults = {
        "repositories": [],
        "repositories_loaded": False,
        "repositories_error": None,
        "selected_repo_id": None,
        "selected_repo": None,
        "latest_investigation": None,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)
    if "investigation_history" not in st.session_state:
        load_history()


def inject_styles() -> None:
    st.html(f"<style>{STYLES_PATH.read_text(encoding='utf-8')}</style>")


api_client = ApiClient(base_url=os.environ.get("API_BASE_URL", "http://127.0.0.1:8000"))


def guide_page() -> None:
    render_guide_view(api_client)


def investigate_page() -> None:
    render_investigation_view(api_client)


def repositories_page() -> None:
    render_repository_view(api_client)


def status_page() -> None:
    render_status_view(api_client)


def main() -> None:
    inject_styles()
    init_session_state()

    pages = {
        "investigate": st.Page(investigate_page, title="Investigate", icon=":material/search:", url_path="investigate", default=True),
        "guide": st.Page(guide_page, title="Getting started", icon=":material/rocket_launch:", url_path="guide"),
        "repositories": st.Page(repositories_page, title="Repositories", icon=":material/folder_code:", url_path="repositories"),
        "status": st.Page(status_page, title="System status", icon=":material/monitor_heart:", url_path="status"),
    }
    register_pages(pages)
    current = st.navigation(list(pages.values()), position="hidden")

    # Backend is the single source of truth for repositories. The first load
    # waits on the API, so show a skeleton shaped like the page being opened.
    if st.session_state.get("repositories_loaded"):
        repos, repo_error = sync_repositories(api_client)
    else:
        with page_skeleton(current.url_path or "investigate"):
            repos, repo_error = sync_repositories(api_client)

    # First visit with nothing set up yet: open the guide instead of an empty workspace
    if not st.session_state.get("onboarding_checked"):
        st.session_state["onboarding_checked"] = True
        if not repo_error and not repos and current.url_path in ("", "investigate"):
            st.switch_page(pages["guide"])

    # Any unexpected crash in the sidebar or a page becomes an error pop-up
    # instead of a raw traceback. (st.rerun/st.stop are not Exceptions, so they pass through.)
    try:
        render_sidebar(api_client, pages)
        current.run()
    except Exception as exc:
        report_error(
            f"The {current.title} page hit an unexpected error",
            exc,
            hint="Try the action again or refresh the page. The details below show exactly where it failed.",
        )
    render_error_dialog()


if __name__ == "__main__":
    main()
