"""
Sidebar navigation, target repository selection, and session history management.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from frontend.api_client import ApiClient
from frontend.utils.formatting import format_timestamp
from frontend.utils.repo_manager import sync_repositories
from frontend.utils.history_manager import load_history, clear_history


def _sidebar_header() -> None:
    """Render the sleek developer-focused brand header inside the sidebar."""
    st.markdown(
        """
        <div class="sidebar-brand">
            <div class="sidebar-brand-badge">⚡</div>
            <div class="sidebar-brand-text">
                <span class="sidebar-brand-title">OpenSource Investigator</span>
                <span class="sidebar-brand-sub">AI Code Intelligence</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_sidebar(api_client: ApiClient) -> str:
    """
    Render sidebar controls and navigation.
    Returns the selected page key ('investigation', 'repositories', 'status').
    """
    with st.sidebar:
        # ── Header ────────────────────────────────────────────────────────────
        _sidebar_header()

        # ── Primary Navigation ────────────────────────────────────────────────
        st.markdown(
            '<p class="sidebar-section-label">🗂 Navigation</p>',
            unsafe_allow_html=True,
        )

        nav_options = {
            "🔍  Investigation": "investigation",
            "📦  Repositories": "repositories",
            "🚦  System Status": "status",
        }

        current_nav = st.session_state.get("active_page", "investigation")
        default_index = 0
        for i, val in enumerate(nav_options.values()):
            if val == current_nav:
                default_index = i
                break

        selected_label = st.radio(
            "Navigation",
            options=list(nav_options.keys()),
            index=default_index,
            label_visibility="collapsed",
        )
        selected_page = nav_options[selected_label]
        st.session_state["active_page"] = selected_page

        # ── Divider ───────────────────────────────────────────────────────────
        st.markdown('<hr class="sidebar-divider">', unsafe_allow_html=True)

        # ── Target Repository Selector ────────────────────────────────────────
        st.markdown(
            '<p class="sidebar-section-label">🎯 Target Repository</p>',
            unsafe_allow_html=True,
        )

        repo_error = st.session_state.get("repositories_error")
        repos = st.session_state.get("repositories", [])

        if repo_error:
            st.markdown(
                f'<p class="sidebar-error-msg">⚠️ {repo_error}</p>',
                unsafe_allow_html=True,
            )

        if repos:
            repo_map = {
                f"#{r['id']}  {r.get('name', 'Repository')}": r
                for r in repos
                if isinstance(r, dict) and "id" in r
            }
            current_id = st.session_state.get("selected_repo_id")
            labels = list(repo_map.keys())
            sel_idx = 0
            for idx, lbl in enumerate(labels):
                if repo_map[lbl]["id"] == current_id:
                    sel_idx = idx
                    break

            chosen_label = st.selectbox(
                "Active Codebase",
                options=labels,
                index=sel_idx,
                key="sidebar_repo_select",
                label_visibility="collapsed",
            )
            active_data = repo_map[chosen_label]
            st.session_state["selected_repo"] = active_data
            st.session_state["selected_repo_id"] = active_data["id"]

            status = active_data.get("status", "")
            status_colour = (
                "#3fb950" if status == "indexed"
                else "#d29922" if status in ("active", "cloned")
                else "#8b949e"
            )
            clean_url = api_client.base_url.replace("http://", "").replace("https://", "")
            st.markdown(
                f"""
                <div class="repo-status-row">
                    <span style="color:{status_colour}; font-weight:500;">● {status.capitalize() if status else "Ready"}</span>
                    <span style="color:#6e7681;" title="{api_client.base_url}">🔌 {clean_url}</span>
                </div>
                """,
                unsafe_allow_html=True,
            )
        else:
            if not repo_error:
                st.markdown(
                    '<p class="sidebar-caption">No repositories configured yet.</p>',
                    unsafe_allow_html=True,
                )
            clean_url = api_client.base_url.replace("http://", "").replace("https://", "")
            st.markdown(
                f'<p class="sidebar-caption">🔌 {clean_url}</p>',
                unsafe_allow_html=True,
            )

        # ── Divider ───────────────────────────────────────────────────────────
        st.markdown('<hr class="sidebar-divider">', unsafe_allow_html=True)

        # ── Recent Investigations ─────────────────────────────────────────────
        st.markdown(
            '<p class="sidebar-section-label">📜 Recent Queries</p>',
            unsafe_allow_html=True,
        )

        history = load_history()

        if not history:
            st.markdown(
                '<p class="sidebar-caption">No recent queries yet.</p>',
                unsafe_allow_html=True,
            )
        else:
            for idx, item in enumerate(history[:5]):
                q = item.get("query", "Query")
                repo_name = item.get("repository_name", "")
                conf = item.get("confidence", "")
                ts = format_timestamp(item.get("timestamp"))

                short_q = (q[:28] + "…") if len(q) > 28 else q
                label = f"💬 {short_q}"

                if st.button(
                    label,
                    key=f"hist_btn_{idx}",
                    help=f"{q}\nRepo: {repo_name} | Confidence: {conf} | {ts}",
                    use_container_width=True,
                ):
                    st.session_state["latest_investigation"] = item.get("response")
                    st.session_state["query_input_text"] = q
                    st.session_state["query_input_area"] = q
                    st.session_state["selected_repo_id"] = item.get("repository_id")
                    st.session_state["active_page"] = "investigation"
                    st.rerun()

            if st.button(
                "🗑 Clear History",
                key="btn_clear_hist",
                use_container_width=True,
            ):
                clear_history()
                st.rerun()

    return selected_page
