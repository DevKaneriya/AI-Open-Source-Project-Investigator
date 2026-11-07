"""
Page registry so components can link to and switch between pages without
importing the app entry point.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

_PAGES: dict[str, Any] = {}


def register_pages(pages: dict[str, Any]) -> None:
    _PAGES.clear()
    _PAGES.update(pages)


def get_page(key: str) -> Any:
    return _PAGES[key]


def go(key: str) -> None:
    """Switch to a registered page (ends the current script run)."""
    st.switch_page(_PAGES[key])


def flash(kind: str, message: str) -> None:
    """Queue a message to show at the top of the next rendered page."""
    st.session_state["flash"] = (kind, message)


def show_flash() -> None:
    pending = st.session_state.pop("flash", None)
    if not pending:
        return
    kind, message = pending
    {"success": st.success, "error": st.error, "warning": st.warning}.get(kind, st.info)(message)


def select_repository(repo: dict[str, Any]) -> None:
    st.session_state["selected_repo_id"] = repo["id"]
    st.session_state["selected_repo"] = repo
