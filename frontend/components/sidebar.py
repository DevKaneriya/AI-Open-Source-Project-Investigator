"""
Sidebar: brand, page navigation, active repository selection, and recent queries.
"""

from __future__ import annotations

import html
from typing import Any

import streamlit as st

from frontend.api_client import ApiClient
from frontend.utils.formatting import format_timestamp
from frontend.utils.access import demo_mode, is_admin, render_admin_controls
from frontend.utils.history_manager import load_history, clear_history
from frontend.utils.navigation import go, select_repository
from frontend.utils.pipeline import is_ready, pick_default_repo, stage_chip_html, stage_unknown

REPO_SELECT_KEY = "sidebar_repo_select"


def _brand() -> None:
    st.html(
        """
        <div class="oi-brand">
            <div class="oi-brand__mark">OI</div>
            <div>
                <div class="oi-brand__title">OpenSource Investigator</div>
                <div class="oi-brand__sub">Evidence-grounded answers</div>
            </div>
        </div>
        """
    )


def _on_repo_change(repos_by_id: dict[int, dict[str, Any]]) -> None:
    repo = repos_by_id.get(st.session_state.get(REPO_SELECT_KEY))
    if repo:
        select_repository(repo)


def _repository_selector(repos: list[dict[str, Any]], repo_error: str | None) -> None:
    st.html('<div class="oi-side-label">Active repository</div>')

    repos_by_id = {r["id"]: r for r in repos if isinstance(r, dict) and "id" in r}
    if not repos_by_id:
        if repo_error:
            st.caption("The demo is starting up. Refresh in a moment." if demo_mode() else "⚠️ Can't reach the backend. Check **System status**.")
        else:
            st.caption("No repositories yet.")
        if is_admin() and st.button("Add a repository", icon=":material/add:", use_container_width=True, key="side_add_repo"):
            go("repositories")
        return

    selected_id = st.session_state.get("selected_repo_id")
    if selected_id not in repos_by_id:
        default = pick_default_repo(list(repos_by_id.values()))
        select_repository(default)
        selected_id = default["id"]

    # The selectbox mirrors selected_repo_id, which other pages may change.
    if st.session_state.get(REPO_SELECT_KEY) != selected_id:
        st.session_state[REPO_SELECT_KEY] = selected_id

    st.selectbox(
        "Active repository",
        options=list(repos_by_id.keys()),
        format_func=lambda rid: repos_by_id[rid].get("name") or f"Repository #{rid}",
        key=REPO_SELECT_KEY,
        on_change=_on_repo_change,
        args=(repos_by_id,),
        label_visibility="collapsed",
    )

    repo = repos_by_id[selected_id]
    if stage_unknown(repo):
        hint = "Restart backend"
    else:
        hint = "Ready to investigate" if is_ready(repo) else "Needs indexing"
    st.html(
        f'<div class="oi-side-status">{stage_chip_html(repo)}'
        f'<span class="oi-muted">{hint}</span></div>'
    )


def _recent_queries() -> None:
    st.html('<div class="oi-side-label">Recent questions</div>')
    history = load_history()
    if not history:
        st.caption("Your questions will appear here.")
        return

    for idx, item in enumerate(history[:5]):
        query = item.get("query", "Query")
        tooltip = (
            f"{query}\n\nRepo: {item.get('repository_name', '')} · "
            f"Confidence: {item.get('confidence', '')} · {format_timestamp(item.get('timestamp'))}"
        )
        if st.button(
            query,
            key=f"hist_btn_{idx}",
            help=tooltip,
            icon=":material/history:",
            type="tertiary",
            use_container_width=True,
        ):
            st.session_state["latest_investigation"] = item.get("response")
            st.session_state["pending_query"] = query
            repo = next(
                (r for r in st.session_state.get("repositories", []) if r.get("id") == item.get("repository_id")),
                None,
            )
            if repo:
                select_repository(repo)
            go("investigate")

    if st.button("Clear history", key="btn_clear_hist", icon=":material/delete_sweep:", type="tertiary", use_container_width=True):
        clear_history()
        st.rerun()


def render_sidebar(api_client: ApiClient, pages: dict[str, Any]) -> None:
    with st.sidebar:
        _brand()

        st.page_link(pages["guide"], label="Getting started", icon=":material/rocket_launch:")
        st.page_link(pages["investigate"], label="Investigate", icon=":material/search:")
        st.page_link(pages["repositories"], label="Repositories", icon=":material/folder_code:")
        st.page_link(pages["status"], label="System status", icon=":material/monitor_heart:")

        st.divider()
        _repository_selector(
            st.session_state.get("repositories", []),
            st.session_state.get("repositories_error"),
        )

        st.divider()
        _recent_queries()

        if demo_mode():
            st.divider()
            render_admin_controls()
        else:
            clean_url = api_client.base_url.replace("http://", "").replace("https://", "")
            st.html(f'<div class="oi-side-foot">Backend · {html.escape(clean_url)}</div>')
