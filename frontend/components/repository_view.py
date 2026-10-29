"""
Repository Management component: active repository selection, registration,
indexing triggers, deletion, and architectural scan.
"""

from __future__ import annotations

import streamlit as st
from typing import Any
from frontend.api_client import (
    ApiClient,
    BackendConnectionError,
    ApiClientError,
)
from frontend.utils.repo_manager import (
    sync_repositories,
    invalidate_repositories_cache,
)


def render_repository_view(api_client: ApiClient) -> None:
    """Render repository configuration, registration, deletion, and indexing pipeline tools."""
    st.markdown(
        """
        <div style="margin-bottom: 4px;">
            <h2 style="margin:0; color:#e6edf3;">📦 Repository Management</h2>
            <p style="margin:4px 0 0 0; color:#8b949e; font-size:0.9rem;">
                Manage target codebases for LangGraph investigations — register, index, chunk, and vectorize.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown("<div style='height:4px'></div>", unsafe_allow_html=True)

    # ── Section 1: Active Repository Selection & List ──────────────────────────
    col_s1_title, col_s1_ref = st.columns([5, 1], gap="small")
    with col_s1_title:
        st.markdown(
            '<p style="font-size:0.78rem; font-weight:600; text-transform:uppercase; letter-spacing:0.07em; color:#6e7681; margin:16px 0 6px 0;">🎯 Active Target Repository</p>',
            unsafe_allow_html=True,
        )
    with col_s1_ref:
        if st.button("🔄 Refresh", key="repo_view_refresh_btn", help="Fetch latest repositories from backend"):
            sync_repositories(api_client, force_refresh=True)
            st.rerun()

    # Synchronize with backend (single source of truth)
    repos, repo_err = sync_repositories(api_client)

    if repo_err:
        st.error(
            f"⚠️ **Backend Repository Error:** {repo_err}\n\n"
            "Unable to communicate with the repository API. Please ensure the backend is running.",
            icon="⚠️",
        )
    elif not repos:
        st.info(
            "No repositories are currently registered in the database. "
            "Use the form below to clone and register a GitHub repository.",
            icon="ℹ️",
        )
    else:
        # Repositories are available from backend
        repo_options = {
            f"ID #{r['id']} — {r.get('name', 'Repository')} ({r.get('url') or 'Local'})": r
            for r in repos
            if isinstance(r, dict) and "id" in r
        }

        current_repo_id = st.session_state.get("selected_repo_id")
        current_label_idx = 0
        labels = list(repo_options.keys())

        for idx, lbl in enumerate(labels):
            if repo_options[lbl]["id"] == current_repo_id:
                current_label_idx = idx
                break

        selected_label = st.selectbox(
            "Select target codebase for investigations:",
            options=labels,
            index=current_label_idx,
            key="repo_selector_dropdown",
        )
        selected_data = repo_options[selected_label]
        st.session_state["selected_repo"] = selected_data
        st.session_state["selected_repo_id"] = selected_data["id"]

        # Display Selected Repo Card
        col_c1, col_c2, col_c3 = st.columns(3)
        with col_c1:
            st.metric("Repository ID", selected_data["id"])
        with col_c2:
            st.metric("Name", selected_data.get("name", "Unknown"))
        with col_c3:
            st.metric("Status", selected_data.get("status", "ready"))

        if selected_data.get("url"):
            st.markdown(f"**Source URL:** [{selected_data['url']}]({selected_data['url']})")
        if selected_data.get("local_path"):
            st.caption(f"Local Storage: `{selected_data['local_path']}`")

        # Delete repository button
        with st.expander("🗑️ Delete Repository", expanded=False):
            st.warning(
                f"Deleting **{selected_data.get('name')}** (ID #{selected_data['id']}) will remove all "
                "its indexed code files, symbols, chunks, and database records."
            )
            col_del_btn, _ = st.columns([1, 4])
            with col_del_btn:
                if st.button("Confirm Delete", key="btn_confirm_delete_repo", type="primary"):
                    try:
                        api_client.delete_repository(selected_data["id"])
                        st.success(f"Repository #{selected_data['id']} deleted successfully.")
                        invalidate_repositories_cache()
                        sync_repositories(api_client, force_refresh=True)
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Failed to delete repository: {exc}")

    st.divider()

    # ── Section 2: Register New Repository ─────────────────────────────────────
    st.markdown(
        '<p style="font-size:0.78rem; font-weight:600; text-transform:uppercase; letter-spacing:0.07em; color:#6e7681; margin:16px 0 6px 0;">➕ Register New GitHub Repository</p>',
        unsafe_allow_html=True,
    )
    st.markdown(
        "Registers a GitHub repository with the backend (`POST /repositories`). "
        "The backend clones the remote repository to local storage and assigns a unique repository ID."
    )

    with st.form("add_repo_form", clear_on_submit=False):
        new_repo_url = st.text_input(
            "GitHub Repository URL:",
            placeholder="https://github.com/owner/repository",
            help="Full HTTPS GitHub URL to clone and register.",
        )
        submit_add = st.form_submit_button("Clone & Register Repository", type="primary")

    if submit_add:
        if not new_repo_url or not new_repo_url.strip():
            st.error("Please enter a valid GitHub repository URL.")
        else:
            with st.spinner("Registering and cloning repository on backend..."):
                try:
                    add_result = api_client.add_repository(new_repo_url.strip())
                    st.success("✅ Repository successfully registered!")
                    st.json(add_result)

                    # Invalidate cache and force refresh from backend
                    invalidate_repositories_cache()
                    sync_repositories(api_client, force_refresh=True)

                    if add_result.get("id"):
                        st.session_state["selected_repo_id"] = add_result["id"]
                        st.session_state["selected_repo"] = add_result

                    st.rerun()

                except BackendConnectionError as exc:
                    st.error(f"Backend unreachable: {exc}")
                except ApiClientError as exc:
                    st.error(f"Registration failed: {exc.message}")

    st.divider()

    # ── Section 3: Indexing Pipeline ───────────────────────────────────────────
    active_repo_id = st.session_state.get("selected_repo_id")
    st.markdown(
        '<p style="font-size:0.78rem; font-weight:600; text-transform:uppercase; letter-spacing:0.07em; color:#6e7681; margin:16px 0 6px 0;">⚙️ Indexing &amp; Vectorization Pipeline</p>',
        unsafe_allow_html=True,
    )

    if not active_repo_id:
        st.warning("Please register or select a repository above before running the indexing pipeline.")
    else:
        st.markdown(
            f"Trigger backend indexing stages for **Repository #{active_repo_id}**. "
            "These endpoints execute AST symbol extraction, chunking, and Qdrant vector indexing."
        )

        col_idx1, col_idx2, col_idx3 = st.columns(3)

        with col_idx1:
            st.markdown("**1. Code Indexing**")
            st.caption("Parses AST, symbols, imports, and file tree (`POST /repositories/{id}/index`).")
            if st.button("Run Code Indexer", key="btn_run_indexer", use_container_width=True):
                with st.spinner("Indexing repository symbols..."):
                    try:
                        res = api_client.index_repository(int(active_repo_id))
                        st.success("Code indexing complete!")
                        st.json(res)
                        invalidate_repositories_cache()
                        sync_repositories(api_client, force_refresh=True)
                    except Exception as exc:
                        st.error(f"Indexing error: {exc}")

        with col_idx2:
            st.markdown("**2. Create Chunks**")
            st.caption("Generates syntactic code chunks (`POST /repositories/{id}/chunks`).")
            if st.button("Create Code Chunks", key="btn_create_chunks", use_container_width=True):
                with st.spinner("Generating code chunks..."):
                    try:
                        res = api_client.create_chunks(int(active_repo_id))
                        st.success("Chunks created successfully!")
                        st.json(res)
                        invalidate_repositories_cache()
                        sync_repositories(api_client, force_refresh=True)
                    except Exception as exc:
                        st.error(f"Chunking error: {exc}")

        with col_idx3:
            st.markdown("**3. Vector Indexing**")
            st.caption("Embeds chunks and writes to Qdrant (`POST /repositories/{id}/vector-index`).")
            if st.button("Index Vectors (Qdrant)", key="btn_index_vectors", use_container_width=True):
                with st.spinner("Generating embeddings & updating Qdrant collection..."):
                    try:
                        res = api_client.index_vectors(int(active_repo_id))
                        st.success("Vector indexing complete!")
                        st.json(res)
                        invalidate_repositories_cache()
                        sync_repositories(api_client, force_refresh=True)
                    except Exception as exc:
                        st.error(f"Vector indexing error: {exc}")

    st.divider()

    # ── Section 4: Architecture Scanner ───────────────────────────────────────
    st.markdown(
        '<p style="font-size:0.78rem; font-weight:600; text-transform:uppercase; letter-spacing:0.07em; color:#6e7681; margin:16px 0 6px 0;">🔭 Quick Architecture Scanner</p>',
        unsafe_allow_html=True,
    )
    st.markdown(
        "Scan any repository's directory structure and frameworks before indexing (`POST /repositories/scan`)."
    )

    with st.form("scan_repo_form"):
        scan_url = st.text_input(
            "Repository URL to scan:",
            value=st.session_state.get("selected_repo", {}).get("url", "") if isinstance(st.session_state.get("selected_repo"), dict) else "",
            placeholder="https://github.com/owner/repository",
        )
        submit_scan = st.form_submit_button("Run Architecture Scan")

    if submit_scan:
        if not scan_url.strip():
            st.error("Please provide a repository URL to scan.")
        else:
            with st.spinner("Scanning repository structure..."):
                try:
                    scan_res = api_client.scan_repository(scan_url.strip())
                    st.success("Scan complete!")
                    st.json(scan_res)
                except Exception as exc:
                    st.error(f"Scan error: {exc}")
