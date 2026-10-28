"""
Sidebar navigation, target repository selection, and session history management.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import streamlit as st

from frontend.api_client import ApiClient
from frontend.utils.formatting import format_timestamp


def _sidebar_header() -> None:
    """Render the polished top-header block inside the sidebar."""
    # Try to load a logo image; fall back to an emoji icon
    logo_path = Path(__file__).parent.parent / "assets" / "logo.png"
    has_logo = logo_path.is_file()

    # Render as a single HTML card so icon + text are always on one line,
    # vertically centred, with consistent padding.
    if has_logo:
        # Streamlit needs st.image for actual images; put it alongside HTML text
        icon_col, text_col = st.columns([1, 5], gap="small")
        with icon_col:
            st.image(str(logo_path), width=36)
        with text_col:
            st.markdown(
                """
                <div style="padding: 2px 0 0 0;">
                    <p style="margin:0; font-size:1.02rem; font-weight:700; color:#58a6ff; line-height:1.2;">
                        OpenSource Investigator
                    </p>
                    <p style="margin:2px 0 0 0; font-size:0.7rem; color:#6e7681; line-height:1.3;">
                        Evidence-grounded AI code investigation
                    </p>
                </div>
                """,
                unsafe_allow_html=True,
            )
    else:
        st.markdown(
            """
            <div class="sidebar-header">
                <span class="sidebar-icon">⚡</span>
                <div>
                    <p class="sidebar-title">OpenSource Investigator</p>
                    <p class="sidebar-subtitle">Evidence-grounded AI code investigation</p>
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

        st.divider()

        # ── Primary Navigation ────────────────────────────────────────────────
        st.markdown(
            '<p class="sidebar-section-label">Navigation</p>',
            unsafe_allow_html=True,
        )

        nav_options = {
            "🔎  Investigation": "investigation",
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

        st.divider()

        # ── Target Repository Selector ────────────────────────────────────────
        st.markdown(
            '<p class="sidebar-section-label">🎯 Target Repository</p>',
            unsafe_allow_html=True,
        )

        session_repos = st.session_state.get("session_repositories", [])
        if session_repos:
            repo_map = {
                f"#{r['id']}  {r.get('name', 'Repository')}": r
                for r in session_repos
                if "id" in r
            }
            current_id = st.session_state.get("selected_repo_id", 2)
            sel_idx = 0
            labels = list(repo_map.keys())
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

            # Compact status pill
            status = active_data.get("status", "")
            status_colour = (
                "#3fb950" if status == "indexed"
                else "#d29922" if status in ("active", "cloned")
                else "#8b949e"
            )
            st.markdown(
                f'<span style="font-size:0.75rem; color:{status_colour};">● {status.capitalize() if status else "Ready"}</span>',
                unsafe_allow_html=True,
            )
        else:
            st.caption("No repositories configured yet.")

        # Backend URL pill
        st.markdown(
            f'<p style="font-size:0.72rem; color:#6e7681; margin-top:8px;">🔌 {api_client.base_url}</p>',
            unsafe_allow_html=True,
        )

        st.divider()

        # ── Recent Investigations ─────────────────────────────────────────────
        st.markdown(
            '<p class="sidebar-section-label">📜 Recent Queries</p>',
            unsafe_allow_html=True,
        )

        history: list[dict[str, Any]] = st.session_state.get("investigation_history", [])

        if not history:
            st.caption("No investigations in this session yet.")
        else:
            for idx, item in enumerate(history[:6]):
                q = item.get("query", "Query")
                repo_name = item.get("repository_name", "")
                conf = item.get("confidence", "")
                ts = format_timestamp(item.get("timestamp"))

                short_q = (q[:35] + "…") if len(q) > 35 else q
                label = f"{short_q}"

                if st.button(
                    label,
                    key=f"hist_btn_{idx}",
                    help=f"{q}\nRepo: {repo_name} | Confidence: {conf} | {ts}",
                    use_container_width=True,
                ):
                    st.session_state["latest_investigation"] = item.get("response")
                    st.session_state["query_input_text"] = q
                    st.session_state["selected_repo_id"] = item.get("repository_id")
                    st.session_state["active_page"] = "investigation"
                    st.rerun()

            st.markdown("")
            if st.button(
                "🗑 Clear History",
                key="btn_clear_hist",
                use_container_width=True,
            ):
                st.session_state["investigation_history"] = []
                st.rerun()

    return selected_page
