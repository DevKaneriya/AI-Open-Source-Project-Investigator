"""
Repository Management component: active repository selection, registration,
indexing triggers, and architectural scan.
"""

from __future__ import annotations

import streamlit as st
from typing import Any
from frontend.api_client import (
    ApiClient,
    BackendConnectionError,
    EndpointNotImplementedError,
    ApiClientError,
)

# Known repository presets for developer convenience
DEFAULT_PRESET_REPOSITORIES = [
    {
        "id": 2,
        "name": "Crumbs",
        "url": "https://github.com/dogukanayd/crumbs",
        "status": "indexed",
        "notes": "Full-stack e-commerce project (Django backend + Angular frontend). Already indexed.",
    },
    {
        "id": 1,
        "name": "Target Repository #1",
        "url": "",
        "status": "active",
        "notes": "Custom registered repository #1",
    },
]


def render_repository_view(api_client: ApiClient) -> None:
    """Render repository configuration, registration, and indexing pipeline tools."""
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
    st.markdown(
        '<p style="font-size:0.78rem; font-weight:600; text-transform:uppercase; letter-spacing:0.07em; color:#6e7681; margin:16px 0 6px 0;">🎯 Active Target Repository</p>',
        unsafe_allow_html=True,
    )

    # Initialize session repositories list if not present
    if "session_repositories" not in st.session_state:
        st.session_state["session_repositories"] = list(DEFAULT_PRESET_REPOSITORIES)

    session_repos: list[dict[str, Any]] = st.session_state["session_repositories"]

    # Check if backend exposes a live GET /repositories endpoint
    backend_repos: list[dict[str, Any]] | None = None
    endpoint_missing = False
    try:
        backend_repos = api_client.list_repositories()
    except EndpointNotImplementedError:
        endpoint_missing = True
    except BackendConnectionError:
        backend_repos = None
    except Exception:
        backend_repos = None

    if endpoint_missing:
        st.info(
            "**Backend API Notice:** The FastAPI backend does not currently implement a "
            "`GET /repositories` endpoint. Repositories are managed via direct Repository ID "
            "and session state. Any newly registered repository via `POST /repositories` will be "
            "automatically retained here.",
            icon="ℹ️",
        )
    elif backend_repos:
        # Merge backend repositories with session list
        existing_ids = {r["id"] for r in session_repos if "id" in r}
        for br in backend_repos:
            if br.get("id") not in existing_ids:
                session_repos.append(br)

    # Display selector
    repo_options = {
        f"ID {r['id']} — {r.get('name', 'Repository')} ({r.get('url') or 'Local'})": r
        for r in session_repos
        if "id" in r
    }

    current_repo_id = st.session_state.get("selected_repo_id", 2)
    current_label_idx = 0

    if repo_options:
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
        if selected_data.get("notes"):
            st.caption(f"Note: {selected_data['notes']}")

    # Manual ID Override
    with st.expander("⚙️ Manual Repository ID Override", expanded=False):
        col_mid, col_mset = st.columns([3, 1])
        with col_mid:
            manual_id = st.number_input(
                "Enter custom Repository ID directly:",
                min_value=1,
                value=int(current_repo_id or 1),
                step=1,
            )
        with col_mset:
            st.write("")
            st.write("")
            if st.button("Set ID", use_container_width=True):
                st.session_state["selected_repo_id"] = int(manual_id)
                st.session_state["selected_repo"] = {
                    "id": int(manual_id),
                    "name": f"Repository #{manual_id}",
                    "url": "",
                    "status": "custom",
                }
                st.success(f"Active repository set to ID {manual_id}")
                st.rerun()

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

                    # Update session state with the new repository
                    new_entry = {
                        "id": add_result.get("id"),
                        "name": add_result.get("name"),
                        "url": add_result.get("url"),
                        "status": add_result.get("status", "cloned"),
                        "local_path": add_result.get("local_path"),
                    }
                    if new_entry["id"]:
                        st.session_state["session_repositories"].append(new_entry)
                        st.session_state["selected_repo"] = new_entry
                        st.session_state["selected_repo_id"] = new_entry["id"]
                        st.rerun()

                except BackendConnectionError as exc:
                    st.error(f"Backend unreachable: {exc}")
                except ApiClientError as exc:
                    st.error(f"Registration failed: {exc.message}")

    st.divider()

    # ── Section 3: Indexing Pipeline ───────────────────────────────────────────
    st.markdown(
        '<p style="font-size:0.78rem; font-weight:600; text-transform:uppercase; letter-spacing:0.07em; color:#6e7681; margin:16px 0 6px 0;">⚙️ Indexing &amp; Vectorization Pipeline</p>',
        unsafe_allow_html=True,
    )
    st.markdown(
        f"Trigger backend indexing stages for **Repository #{current_repo_id}**. "
        "These endpoints execute AST symbol extraction, chunking, and Qdrant vector indexing."
    )

    col_idx1, col_idx2, col_idx3 = st.columns(3)

    with col_idx1:
        st.markdown("**1. Code Indexing**")
        st.caption("Parses AST, symbols, imports, and file tree (`POST /repositories/{id}/index`).")
        if st.button("Run Code Indexer", key="btn_run_indexer", use_container_width=True):
            with st.spinner("Indexing repository symbols..."):
                try:
                    res = api_client.index_repository(int(current_repo_id))
                    st.success("Code indexing complete!")
                    st.json(res)
                except Exception as exc:
                    st.error(f"Indexing error: {exc}")

    with col_idx2:
        st.markdown("**2. Create Chunks**")
        st.caption("Generates syntactic code chunks (`POST /repositories/{id}/chunks`).")
        if st.button("Create Code Chunks", key="btn_create_chunks", use_container_width=True):
            with st.spinner("Generating code chunks..."):
                try:
                    res = api_client.create_chunks(int(current_repo_id))
                    st.success("Chunks created successfully!")
                    st.json(res)
                except Exception as exc:
                    st.error(f"Chunking error: {exc}")

    with col_idx3:
        st.markdown("**3. Vector Indexing**")
        st.caption("Embeds chunks and writes to Qdrant (`POST /repositories/{id}/vector-index`).")
        if st.button("Index Vectors (Qdrant)", key="btn_index_vectors", use_container_width=True):
            with st.spinner("Generating embeddings & updating Qdrant collection..."):
                try:
                    res = api_client.index_vectors(int(current_repo_id))
                    st.success("Vector indexing complete!")
                    st.json(res)
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
            value=st.session_state.get("selected_repo", {}).get("url", ""),
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
