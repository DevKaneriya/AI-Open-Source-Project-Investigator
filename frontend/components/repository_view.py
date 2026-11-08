"""
Repository management: add + index in one step, per-repository setup progress,
resume / re-index / delete actions, and advanced pipeline tools.
"""

from __future__ import annotations

import html
import re
from typing import Any

import streamlit as st

from frontend.api_client import ApiClient, ApiClientError
from frontend.utils.access import backend_unavailable_message, demo_mode, is_admin
from frontend.utils.navigation import flash, go, select_repository, show_flash
from frontend.utils.pipeline import (
    OUTDATED_BACKEND_MESSAGE,
    PIPELINE_STEPS,
    backend_outdated,
    stage_unknown,
    is_ready,
    remaining_steps,
    run_pipeline,
    stage_chip_html,
    stepper_html,
)
from frontend.utils.repo_manager import invalidate_repositories_cache, sync_repositories

GITHUB_URL_RE = re.compile(r"^https?://(www\.)?github\.com/[\w.-]+/[\w.-]+?(\.git)?/?$", re.IGNORECASE)


def _refresh(api_client: ApiClient) -> list[dict[str, Any]]:
    invalidate_repositories_cache()
    repos, _ = sync_repositories(api_client, force_refresh=True)
    return repos


def _normalize_url(url: str) -> str:
    url = url.strip().rstrip("/")
    return url[:-4] if url.lower().endswith(".git") else url


def _finish(api_client: ApiClient, ok: bool, repo: dict[str, Any] | None, message: str) -> None:
    """Refresh state after a pipeline run; on success rerun with a flash message."""
    repos = _refresh(api_client)
    if repo and repo.get("id") is not None:
        fresh = next((r for r in repos if r.get("id") == repo["id"]), repo)
        select_repository(fresh)
    if ok:
        flash("success", message)
        st.rerun()


# ── Add repository ────────────────────────────────────────────────────────────

def _render_add_form(api_client: ApiClient, repos: list[dict[str, Any]]) -> None:
    with st.container(border=True):
        st.markdown("**Add a GitHub repository**")
        with st.form("add_repo_form", border=False):
            with st.container(horizontal=True, vertical_alignment="bottom", gap="small"):
                url = st.text_input(
                    "Repository URL",
                    placeholder="https://github.com/owner/repository",
                    label_visibility="collapsed",
                    width="stretch",
                )
                submitted = st.form_submit_button("Add & index", type="primary", icon=":material/add:", width="content")
            auto_index = st.toggle(
                "Index automatically after cloning",
                value=True,
                help="Runs parsing, chunking and embedding right away. Turn off to run the steps yourself later.",
            )

        st.caption("Public repositories only. Indexing takes from under a minute to several minutes, depending on repository size.")

    if not submitted:
        return

    clean = _normalize_url(url or "")
    if not GITHUB_URL_RE.match(clean):
        st.error("Enter a GitHub URL like `https://github.com/owner/repository`.")
        return

    existing = next((r for r in repos if _normalize_url(r.get("url") or "").lower() == clean.lower()), None)
    if existing:
        select_repository(existing)
        st.info(
            f"**{existing.get('name')}** is already added. "
            + ("It's ready to investigate." if is_ready(existing) else "Use *Continue indexing* on its card below.")
        )
        return

    if auto_index:
        ok, repo, message = run_pipeline(api_client, {}, clone_url=clean)
        _finish(api_client, ok, repo, message)
    else:
        with st.spinner("Cloning repository…"):
            try:
                repo = api_client.add_repository(clean)
            except ApiClientError as exc:
                st.error(f"Couldn't add the repository: {exc.message}")
                return
        _finish(api_client, True, repo, f"{repo.get('name')} was cloned. Index it to start investigating.")


# ── Repository cards ─────────────────────────────────────────────────────────

def _count(value: Any) -> str:
    return f"{value:,}" if isinstance(value, int) else "–"


def _render_delete(api_client: ApiClient, rid: int, name: str) -> None:
    with st.popover("Delete", icon=":material/delete:"):
        st.markdown(f"Delete **{html.escape(name)}**?")
        st.caption("This removes its indexed files, symbols, chunks and vectors. The GitHub repository is not affected.")
        if st.button("Delete permanently", key=f"repo_delete_{rid}", type="primary"):
            try:
                api_client.delete_repository(rid)
            except ApiClientError as exc:
                st.error(f"Couldn't delete: {exc.message}")
            else:
                if st.session_state.get("selected_repo_id") == rid:
                    st.session_state["selected_repo_id"] = None
                    st.session_state["selected_repo"] = None
                _refresh(api_client)
                flash("success", f"{name} was deleted.")
                st.rerun()


def _render_repo_card(api_client: ApiClient, repo: dict[str, Any], active: bool) -> None:
    rid = repo["id"]
    name = repo.get("name") or f"Repository #{rid}"
    url = repo.get("url") or ""
    ready = is_ready(repo)
    unknown = stage_unknown(repo)

    with st.container(border=True):
        active_chip = '<span class="oi-chip oi-chip--accent">Active</span>' if active else ""
        link = (
            f'<a href="{html.escape(url)}" target="_blank" rel="noopener">View on GitHub ↗</a>'
            if url.startswith("http") else ""
        )
        meta = ""
        if "file_count" in repo:
            meta = (
                '<div class="oi-repo-meta">'
                f"<span><b>{_count(repo.get('file_count'))}</b> files</span>"
                f"<span><b>{_count(repo.get('chunk_count'))}</b> chunks</span>"
                f"<span><b>{_count(repo.get('vector_count'))}</b> vectors</span>"
                f"<span>ID #{rid}</span></div>"
            )
        st.html(
            f"""
            <div class="oi-repo-head">
                <div class="oi-repo-head__name"><span class="oi-truncate">{html.escape(name)}</span>
                {stage_chip_html(repo)}{active_chip}</div>
                {link}
            </div>
            <div class="oi-url">{html.escape(url)}</div>
            {"" if unknown else stepper_html(repo)}
            {meta}
            """
        )

        run_clicked = False
        admin = is_admin()
        with st.container(horizontal=True, gap="small"):
            if st.button(
                "Investigate",
                key=f"repo_investigate_{rid}",
                type="primary" if ready or unknown else "secondary",
                icon=":material/search:",
                disabled=not (ready or unknown),
                help=None if ready or unknown else "Finish indexing first",
            ):
                select_repository(repo)
                go("investigate")

            if admin and not ready:
                todo = remaining_steps(repo)
                label = "Continue indexing" if len(todo) < len(PIPELINE_STEPS) - 1 else "Index now"
                run_clicked = st.button(label, key=f"repo_run_{rid}", type="primary", icon=":material/play_arrow:")
            elif admin:
                with st.popover("Re-index", icon=":material/refresh:"):
                    st.markdown("Re-run parsing, chunking and embedding. Useful after the repository changes upstream.")
                    st.caption("The repository can't be investigated until this finishes.")
                    if st.button("Start re-indexing", key=f"repo_reindex_{rid}", type="primary"):
                        st.session_state["reindex_repo_id"] = rid
                        st.rerun()

            if admin:
                _render_delete(api_client, rid, name)

        # Re-index requests come from a popover, so run them outside it
        if st.session_state.get("reindex_repo_id") == rid:
            st.session_state.pop("reindex_repo_id")
            ok, fresh, message = run_pipeline(api_client, {**repo, "stage": "cloned"})
            _finish(api_client, ok, fresh, message)
        elif run_clicked:
            ok, fresh, message = run_pipeline(api_client, repo)
            _finish(api_client, ok, fresh, message)


# ── Advanced tools ───────────────────────────────────────────────────────────

def _render_advanced(api_client: ApiClient, repos: list[dict[str, Any]]) -> None:
    with st.expander("Advanced tools", icon=":material/tune:"):
        st.markdown("**Run a single pipeline step**")
        st.caption("Normally you won't need this. *Add & index* and *Continue indexing* run every step in order.")
        if not repos:
            st.caption("Add a repository first.")
        else:
            by_id = {r["id"]: r for r in repos}
            current = st.session_state.get("selected_repo_id")
            rid = st.selectbox(
                "Repository",
                options=list(by_id.keys()),
                index=list(by_id.keys()).index(current) if current in by_id else 0,
                format_func=lambda i: f"{by_id[i].get('name')} (#{i})",
                key="adv_repo_select",
            )
            calls = {
                "parsed": ("Parse code", api_client.index_repository),
                "chunked": ("Create chunks", api_client.create_chunks),
                "indexed": ("Embed vectors", api_client.index_vectors),
            }
            result_slot = st.empty()
            with st.container(horizontal=True, gap="small"):
                for stage, (label, call) in calls.items():
                    if st.button(label, key=f"adv_{stage}"):
                        with result_slot, st.spinner(f"{label}…"):
                            try:
                                res = call(int(rid))
                            except ApiClientError as exc:
                                result_slot.error(f"{label} failed: {exc.message}")
                            else:
                                _refresh(api_client)
                                with result_slot.container():
                                    st.success(f"{label} finished.")
                                    st.json(res, expanded=False)

        st.divider()
        st.markdown("**Quick architecture scan**")
        st.caption("Clone a repository temporarily and list its structure and frameworks without indexing it.")
        with st.form("scan_repo_form", border=False):
            selected = st.session_state.get("selected_repo") or {}
            scan_url = st.text_input(
                "Repository URL to scan",
                value=selected.get("url", "") if isinstance(selected, dict) else "",
                placeholder="https://github.com/owner/repository",
            )
            submit_scan = st.form_submit_button("Run scan", icon=":material/travel_explore:")
        if submit_scan:
            if not GITHUB_URL_RE.match(_normalize_url(scan_url or "")):
                st.error("Enter a GitHub URL like `https://github.com/owner/repository`.")
            else:
                with st.spinner("Scanning repository structure…"):
                    try:
                        st.json(api_client.scan_repository(scan_url.strip()), expanded=1)
                    except ApiClientError as exc:
                        st.error(f"Scan failed: {exc.message}")


def render_repository_view(api_client: ApiClient) -> None:
    show_flash()

    if is_admin():
        title, intro = "Your codebases", "Add a repository and it's indexed automatically. Once it shows <b>Ready</b>, you can investigate it."
    else:
        title, intro = "Explore codebases", "These repositories are indexed and ready for questions."
    st.html(
        f"""
        <div class="oi-page-header">
            <span class="oi-eyebrow">Repositories</span>
            <h1>{title}</h1>
            <p>{intro}</p>
        </div>
        """
    )

    repos, repo_err = sync_repositories(api_client)
    if repo_err:
        st.error(f"{backend_unavailable_message()}\n\nDetails: {repo_err}", icon=":material/cloud_off:")
        if not demo_mode() and st.button("Open system status", icon=":material/monitor_heart:"):
            go("status")
        return

    admin = is_admin()
    if backend_outdated(repos) and admin:
        st.warning(OUTDATED_BACKEND_MESSAGE, icon=":material/update:")

    if admin:
        _render_add_form(api_client, repos)
        repos = st.session_state.get("repositories", repos)
    else:
        st.info(
            "This is a public demo, so the repositories are picked by the owner. "
            "Choose a **Ready** one and click **Investigate** to ask questions about it.",
            icon=":material/visibility:",
        )

    with st.container(horizontal=True, vertical_alignment="center", horizontal_alignment="distribute"):
        st.html(f'<div class="oi-section-label" style="margin:0">Added repositories · {len(repos)}</div>', width="content")
        if st.button("Refresh", icon=":material/refresh:", key="repo_view_refresh_btn", type="tertiary"):
            _refresh(api_client)
            st.rerun()
    if not repos:
        st.caption("Nothing here yet. Add your first repository above." if admin else "No repositories have been published yet.")
    else:
        active_id = st.session_state.get("selected_repo_id")
        for repo in repos:
            if isinstance(repo, dict) and "id" in repo:
                _render_repo_card(api_client, repo, active=repo["id"] == active_id)

    if admin:
        _render_advanced(api_client, repos)
