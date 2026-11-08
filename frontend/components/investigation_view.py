"""
Main Investigation workspace component: query input, example queries,
grounded answer presentation, limitations, and execution diagnostics.
"""

from __future__ import annotations

import html
import time
from typing import Any

import streamlit as st

from frontend.utils.access import backend_unavailable_message, demo_mode, is_admin, reserve_query
from frontend.utils.history_manager import save_history_entry
from frontend.api_client import ApiClient, ApiClientError, BackendTimeoutError
from frontend.utils.errors import report_error
from frontend.components.evidence_view import render_evidence_citations
from frontend.utils.navigation import flash, go, show_flash, select_repository
from frontend.utils.pipeline import (
    OUTDATED_BACKEND_MESSAGE,
    is_ready,
    run_pipeline,
    stage_chip_html,
    stage_label,
    stage_unknown,
)
from frontend.utils.repo_manager import validate_selected_repository, invalidate_repositories_cache, sync_repositories
from frontend.utils.formatting import (
    get_confidence_badge_html,
    format_execution_time,
)

EXAMPLE_QUESTIONS = [
    "Give me an overview of the architecture",
    "Where is authentication handled?",
    "What are the main API endpoints?",
    "Which data models exist?",
    "Which frameworks and versions are used?",
    "Trace a request from the frontend to the database",
]

QUERY_KEY = "query_input_area"
EXAMPLE_KEY = "example_pills"


def _use_example() -> None:
    choice = st.session_state.get(EXAMPLE_KEY)
    if choice:
        st.session_state[QUERY_KEY] = choice
    st.session_state[EXAMPLE_KEY] = None


def _page_header() -> None:
    st.html(
        """
        <div class="oi-page-header">
            <span class="oi-eyebrow">Investigate</span>
            <h1>Ask about the code</h1>
            <p>Ask a question in plain English. The answer is built from verified source
            snippets, and every claim cites where it came from.</p>
        </div>
        """
    )


def _render_empty_state(repo_error: str | None) -> None:
    with st.container(border=True):
        if repo_error:
            st.markdown(backend_unavailable_message())
            if not demo_mode() and st.button("Open system status", type="primary", icon=":material/monitor_heart:"):
                go("status")
        elif not is_admin():
            st.markdown("#### No demo repositories yet")
            st.markdown("The owner hasn't published a repository for this demo yet. Please check back soon.")
        else:
            st.markdown("#### Add a repository to get started")
            st.markdown(
                "You need at least one indexed repository before you can ask questions. "
                "It takes one step: paste a GitHub URL and the app clones and indexes it for you."
            )
            with st.container(horizontal=True, gap="small"):
                if st.button("Add a repository", type="primary", icon=":material/add:"):
                    go("repositories")
                if st.button("Read the guide", icon=":material/rocket_launch:"):
                    go("guide")


def _render_target_banner(api_client: ApiClient, repo: dict[str, Any]) -> None:
    name = repo.get("name") or f"Repository #{repo.get('id')}"
    url = repo.get("url") or ""
    link = (
        f'<a href="{html.escape(url)}" target="_blank" rel="noopener">View on GitHub ↗</a>'
        if url.startswith("http") else ""
    )
    with st.container(border=True):
        st.html(
            f"""
            <div class="oi-repo-head">
                <div class="oi-repo-head__name"><span class="oi-muted" style="font-weight:500">Repository</span>
                <span class="oi-truncate">{html.escape(name)}</span>{stage_chip_html(repo)}</div>
                {link}
            </div>
            """
        )
        st.caption("Switch repositories from the sidebar.")

    if is_ready(repo):
        return
    if stage_unknown(repo):
        st.warning(OUTDATED_BACKEND_MESSAGE, icon=":material/update:")
        return

    ready_other = next(
        (r for r in st.session_state.get("repositories", []) if is_ready(r) and r.get("id") != repo.get("id")),
        None,
    )
    if not is_admin():
        st.html(
            f"""
            <div class="oi-callout oi-callout--warn">
                <strong>{html.escape(name)} is still being prepared</strong> and can't answer questions yet.
            </div>
            """
        )
        if ready_other and st.button(f"Use {ready_other.get('name')} instead", type="primary", key="inv_use_ready_demo"):
            select_repository(ready_other)
            st.rerun()
        return

    st.html(
        f"""
        <div class="oi-callout oi-callout--warn">
            <strong>{html.escape(name)} isn't fully indexed yet</strong> (current step: {html.escape(stage_label(repo))}).
            Answers will be incomplete or empty until indexing finishes.
        </div>
        """
    )
    with st.container(horizontal=True, gap="small"):
        run = st.button("Finish indexing now", type="primary", icon=":material/play_arrow:", key="inv_finish_index")
        if ready_other and st.button(f"Use {ready_other.get('name')} instead", key="inv_use_ready"):
            select_repository(ready_other)
            st.rerun()
    if run:
        ok, fresh, message = run_pipeline(api_client, repo)
        invalidate_repositories_cache()
        sync_repositories(api_client, force_refresh=True)
        if ok:
            flash("success", message)
            st.rerun()


def _run_investigation(api_client: ApiClient, query: str) -> None:
    verified_repo, val_err = validate_selected_repository(api_client)
    if val_err or not verified_repo:
        report_error(
            "The selected repository is no longer available",
            val_err or "The repository could not be found on the backend.",
            hint="Pick another repository in the sidebar or add one on the Repositories page.",
        )
        return

    target_repo_id = verified_repo["id"]
    target_repo_name = verified_repo.get("name") or f"Repository #{target_repo_id}"

    rejection = reserve_query(query)
    if rejection:
        st.warning(rejection, icon=":material/hourglass_top:")
        return

    context = {"Repository": f"{target_repo_name} (#{target_repo_id})", "Question": query}
    try:
        with st.spinner("Investigating: analysing the question, retrieving evidence and verifying citations…"):
            result = api_client.investigate(repository_id=int(target_repo_id), query=query)
    except BackendTimeoutError as exc:
        report_error("The investigation timed out", exc, context=context,
                     hint="Try again, or ask a narrower question that names specific files, functions or flows.")
        return
    except ApiClientError as exc:
        report_error("The investigation failed", exc, context=context)
        return

    if not isinstance(result.get("investigation"), dict):
        report_error(
            "The investigation returned an unexpected response",
            "The backend answered, but the response didn't contain an investigation result.",
            context={**context, "Response": result},
            hint="Try again. If it keeps happening, restart the backend so both parts run the latest code.",
        )
        return

    st.session_state["latest_investigation"] = result
    investigation = result.get("investigation")
    save_history_entry(
        {
            "repository_id": target_repo_id,
            "repository_name": target_repo_name,
            "query": query,
            "timestamp": result.get("metadata", {}).get("timestamp") or time.time(),
            "confidence": investigation.get("confidence") if isinstance(investigation, dict) else "unknown",
            "response": result,
        }
    )
    # The text area was already rendered this run, so clear it on the next one.
    st.session_state["clear_query"] = True
    st.rerun()


def render_investigation_view(api_client: ApiClient) -> None:
    show_flash()
    _page_header()

    repos = st.session_state.get("repositories", [])
    selected_repo = st.session_state.get("selected_repo")
    if not repos or not isinstance(selected_repo, dict):
        _render_empty_state(st.session_state.get("repositories_error"))
        return

    _render_target_banner(api_client, selected_repo)

    # Apply deferred query updates before the text area is created; Streamlit
    # forbids changing a widget's key after it is instantiated in a run.
    if st.session_state.pop("clear_query", False):
        st.session_state[QUERY_KEY] = ""
    pending = st.session_state.pop("pending_query", None)
    if pending is not None:
        st.session_state[QUERY_KEY] = pending
    st.session_state.setdefault(QUERY_KEY, "")

    st.pills(
        "Try an example",
        EXAMPLE_QUESTIONS,
        key=EXAMPLE_KEY,
        on_change=_use_example,
    )

    # A multi-line box for longer questions; Ctrl+Enter (Cmd+Enter on Mac) submits.
    with st.form("investigate_form", border=False):
        query_text = st.text_area(
            "Your question",
            key=QUERY_KEY,
            height=140,
            placeholder="e.g. How does the checkout flow work from the UI to the database?",
            help="Name the functions, classes, files or flows you care about for the most precise answers. "
                 "Press Ctrl+Enter (Cmd+Enter on Mac) to submit.",
        )
        submitted = st.form_submit_button("Investigate", type="primary", icon=":material/search:")

    if submitted:
        if not query_text or not query_text.strip():
            st.warning("Type a question first, or pick one of the examples above.")
        else:
            _run_investigation(api_client, query_text.strip())

    latest = st.session_state.get("latest_investigation")
    if latest:
        st.divider()
        render_investigation_result(latest)


def render_investigation_result(result: dict[str, Any]) -> None:
    """Render the structured investigation response."""
    investigation = result.get("investigation", {})
    if not isinstance(investigation, dict):
        investigation = {}

    metadata = result.get("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}

    evidence_summary = result.get("evidence_summary", {})
    if not isinstance(evidence_summary, dict):
        evidence_summary = {}

    repo_info = result.get("repository", {})
    if not isinstance(repo_info, dict):
        repo_info = {}

    answer = investigation.get("answer", "")
    confidence = investigation.get("confidence", "unknown")
    limitations = investigation.get("limitations", [])
    citations = investigation.get("citations", [])
    question = investigation.get("question") or result.get("query", "")
    question_type = result.get("question_type")
    total_time = metadata.get("total_time_seconds") or investigation.get("execution_time_seconds")

    meta_bits = []
    if repo_info.get("name"):
        meta_bits.append(html.escape(str(repo_info["name"])))
    if question_type:
        meta_bits.append(f"Intent <code>{html.escape(str(question_type))}</code>")
    if total_time is not None:
        meta_bits.append(format_execution_time(total_time))
    if citations:
        meta_bits.append(f"{len(citations)} source{'s' if len(citations) != 1 else ''}")

    st.html(
        f"""
        <div class="oi-result-head">
            <h3>{html.escape(str(question))}</h3>
            {get_confidence_badge_html(confidence)}
        </div>
        <div class="oi-result-meta">{" · ".join(meta_bits)}</div>
        """
    )

    with st.container(border=True):
        if answer:
            st.markdown(answer)
        else:
            st.warning("No answer text was generated for this investigation.")

    if limitations:
        st.warning(
            "**Verification limits and evidence gaps**\n\n" + "\n".join(f"- {lim}" for lim in limitations),
            icon=":material/info:",
        )

    repo_url = repo_info.get("url") or (st.session_state.get("selected_repo") or {}).get("url")
    render_evidence_citations(
        citations=citations,
        repository_url=repo_url,
        answer_text=answer,
    )

    with st.expander("Investigation diagnostics", icon=":material/insights:"):
        engine = metadata.get("engine") or metadata.get("execution_engine") or "LangGraph"
        rounds = metadata.get("rounds_executed") or metadata.get("retrieval_rounds")
        repair = metadata.get("repair_attempts", 0)

        d1, d2, d3, d4 = st.columns(4, gap="small")
        with d1:
            st.metric("Engine", engine)
        with d2:
            st.metric("Retrieval rounds", rounds if rounds is not None else "1")
        with d3:
            st.metric("Citation repairs", repair)
        with d4:
            st.metric("Total time", format_execution_time(total_time))

        if evidence_summary:
            st.markdown("##### Evidence summary")
            st.json(evidence_summary, expanded=False)

        if metadata:
            st.markdown("##### Raw workflow metadata")
            st.json(metadata, expanded=False)
