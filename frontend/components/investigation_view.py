"""
Main Investigation workspace component: query input, example queries,
grounded answer presentation, limitations, and execution diagnostics.
"""

from __future__ import annotations

import streamlit as st
import time
from typing import Any
from frontend.utils.history_manager import save_history_entry, load_history
from frontend.api_client import (
    ApiClient,
    BackendConnectionError,
    BackendTimeoutError,
    InvestigationError,
    RepositoryNotFoundError,
    ApiClientError,
)
from frontend.components.evidence_view import render_evidence_citations
from frontend.utils.repo_manager import validate_selected_repository
from frontend.utils.formatting import (
    get_confidence_badge_html,
    format_execution_time,
    format_timestamp,
)

EXAMPLE_QUESTIONS = [
    "Explain the CartItem model and its fields.",
    "Trace an authenticated cart update from frontend to database.",
    "Where is authentication handled?",
    "How is product data retrieved and cached?",
    "What calls this function and what does it depend on?",
]


def render_investigation_view(api_client: ApiClient) -> None:
    """Render the primary investigation workspace."""

    # ── Page header ────────────────────────────────────────────────────────────
    st.markdown(
        """
        <div style="margin-bottom: 4px;">
            <h2 style="margin:0; color:#e6edf3;">🔎 Repository Investigation</h2>
            <p style="margin:4px 0 0 0; color:#8b949e; font-size:0.9rem;">
                Ask natural-language questions. The LangGraph engine retrieves, evaluates,
                and cites verified repository source evidence.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    selected_repo = st.session_state.get("selected_repo")
    repo_id = selected_repo.get("id") if isinstance(selected_repo, dict) else st.session_state.get("selected_repo_id")
    repo_name = selected_repo.get("name") if isinstance(selected_repo, dict) else (f"Repository #{repo_id}" if repo_id else None)
    repo_url = selected_repo.get("url") if isinstance(selected_repo, dict) else None

    # Guard: require repository selection
    if not repo_id or not selected_repo:
        st.warning(
            "**No repository selected.** Select or configure a repository in the sidebar, "
            "or visit the **Repositories** tab to register a codebase.",
            icon="⚠️",
        )
        return

    # ── Active-target banner ───────────────────────────────────────────────────
    st.markdown(
        f"""
        <div style="
            display:flex; align-items:center; justify-content:space-between;
            background:#161b22; border:1px solid #21262d; border-radius:8px;
            padding:10px 16px; margin: 12px 0 16px 0; gap: 12px;
        ">
            <div style="font-size:0.88rem; color:#c9d1d9; min-width:0;">
                <span style="color:#6e7681;">Active target: </span>
                <code style="color:#58a6ff; background:transparent; padding:0;">{repo_name}</code>
                <span style="color:#6e7681; margin-left:8px;">ID&nbsp;{repo_id}</span>
            </div>
            {"<a href='" + repo_url + "' target='_blank' style='font-size:0.8rem; color:#58a6ff; text-decoration:none; white-space:nowrap; flex-shrink:0;'>↗ GitHub</a>" if repo_url else ""}
        </div>
        """,
        unsafe_allow_html=True,
    )

    # ── Suggested queries ──────────────────────────────────────────────────────
    st.markdown(
        '<p style="font-size:0.78rem; font-weight:600; text-transform:uppercase; letter-spacing:0.07em; color:#6e7681; margin-bottom:6px;">Suggested queries</p>',
        unsafe_allow_html=True,
    )
    example_cols = st.columns(len(EXAMPLE_QUESTIONS), gap="small")
    for i, example in enumerate(EXAMPLE_QUESTIONS):
        with example_cols[i]:
            if st.button(
                example,
                key=f"ex_btn_{i}",
                use_container_width=True,
                help="Click to populate this question",
            ):
                st.session_state["query_input_text"] = example
                st.session_state["query_input_area"] = example
                st.rerun()

    # ── Recent queries quick access ───────────────────────────────────────────
    recent_history = load_history()
    if recent_history:
        st.markdown(
            '<p style="font-size:0.78rem; font-weight:600; text-transform:uppercase; letter-spacing:0.07em; color:#6e7681; margin:10px 0 6px 0;">Recent queries</p>',
            unsafe_allow_html=True,
        )
        recent_display = recent_history[:4]
        recent_cols = st.columns(len(recent_display), gap="small")
        for i, hist_item in enumerate(recent_display):
            rq = hist_item.get("query", "")
            short_rq = (rq[:36] + "…") if len(rq) > 36 else rq
            with recent_cols[i]:
                if st.button(
                    f"🕒 {short_rq}",
                    key=f"recent_chip_{i}",
                    use_container_width=True,
                    help=f"Reload investigation:\n{rq}\nRepo: {hist_item.get('repository_name', '')}",
                ):
                    st.session_state["latest_investigation"] = hist_item.get("response")
                    st.session_state["query_input_text"] = rq
                    st.session_state["query_input_area"] = rq
                    st.session_state["selected_repo_id"] = hist_item.get("repository_id")
                    st.rerun()

    # ── Query input ────────────────────────────────────────────────────────────
    # If the previous investigation requested a clear, reset the query box keys
    # here BEFORE the text_area widget is instantiated. Streamlit forbids
    # modifying a widget's session-state key after that widget is created in
    # the same run, so the clear must happen prior to creating the text_area.
    if st.session_state.pop("clear_query", False):
        st.session_state["query_input_text"] = ""
        st.session_state["query_input_area"] = ""
    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
    query_text = st.text_area(
        label="Investigation query",
        value=st.session_state.get("query_input_text", ""),
        key="query_input_area",
        placeholder="e.g. Trace an authenticated cart update from the frontend to the database.",
        height=90,
        help="Be specific about functions, flows, models, or behaviors to investigate.",
        label_visibility="collapsed",
    )

    col_btn, col_hint = st.columns([1, 5], gap="small")
    with col_btn:
        investigate_clicked = st.button(
            "🚀 Investigate",
            type="primary",
            use_container_width=True,
            disabled=st.session_state.get("is_investigating", False),
        )
    with col_hint:
        st.markdown(
            '<p style="font-size:0.78rem; color:#6e7681; margin:8px 0 0 4px;">Powered by LangGraph · Analysis → Retrieval → Evaluation → Answer → Citation Validation</p>',
            unsafe_allow_html=True,
        )

    # ── Run investigation ──────────────────────────────────────────────────────
    if investigate_clicked:
        if not query_text or not query_text.strip():
            st.error("Please enter a question before running an investigation.")
        else:
            # Task 6: Validate selected repository against latest backend state before submitting
            verified_repo, val_err = validate_selected_repository(api_client)
            if val_err or not verified_repo:
                st.error(
                    f"❌ **Repository Validation Error:** {val_err or 'Repository not found on backend.'}\n\n"
                    "The selected repository is no longer available in the database. "
                    "Please select an active repository or register one.",
                    icon="❌",
                )
                return

            # Task 10: Ensure displayed name and ID sent to endpoint come from the same current record
            target_repo_id = verified_repo["id"]
            target_repo_name = verified_repo.get("name") or f"Repository #{target_repo_id}"

            st.session_state["is_investigating"] = True
            try:
                with st.spinner("Running multi-node investigation…"):
                    result = api_client.investigate(
                        repository_id=int(target_repo_id),
                        query=query_text.strip(),
                    )
                st.session_state["latest_investigation"] = result
                st.session_state["last_investigated_query"] = query_text.strip()

                history_entry = {
                    "repository_id": target_repo_id,
                    "repository_name": target_repo_name,
                    "query": query_text.strip(),
                    "timestamp": result.get("metadata", {}).get("timestamp") or time.time(),
                    "confidence": (
                        result.get("investigation", {}).get("confidence")
                        if isinstance(result.get("investigation"), dict)
                        else "unknown"
                    ),
                    "response": result,
                }
                save_history_entry(history_entry)
                st.session_state["is_investigating"] = False
                # Defer clearing the query box to the next run: the text_area
                # widget (key="query_input_area") was already instantiated
                # earlier in this run, so its key can't be modified here.
                st.session_state["clear_query"] = True
                st.rerun()

            except BackendConnectionError as exc:
                st.error(f"🔌 **Backend Connection Error:** {exc}")
            except BackendTimeoutError as exc:
                st.error(f"⏱️ **Investigation Timed Out:** {exc}")
            except RepositoryNotFoundError as exc:
                st.error(f"🔍 **Repository Not Found:** {exc}")
            except InvestigationError as exc:
                st.error(f"❌ **Investigation Failed:** {exc.message}")
            except ApiClientError as exc:
                st.error(f"⚠️ **API Error:** {exc.message}")
            except Exception as exc:
                st.error(f"💥 **Unexpected Error:** {exc}")
            finally:
                st.session_state["is_investigating"] = False

    # ── Results ────────────────────────────────────────────────────────────────
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

    # ── Result header ──────────────────────────────────────────────────────────
    col_q, col_badge = st.columns([4, 1], gap="small")
    with col_q:
        st.markdown(f"#### {question}")
        if question_type:
            st.markdown(
                f'<span style="font-size:0.75rem; color:#6e7681;">Intent: <code>{question_type}</code></span>',
                unsafe_allow_html=True,
            )
    with col_badge:
        st.markdown(
            '<div style="display:flex; justify-content:flex-end; padding-top:6px;">'
            + get_confidence_badge_html(confidence)
            + "</div>",
            unsafe_allow_html=True,
        )

    # ── Answer ─────────────────────────────────────────────────────────────────
    st.markdown(
        '<p style="font-size:0.78rem; font-weight:600; text-transform:uppercase; letter-spacing:0.07em; color:#6e7681; margin: 16px 0 6px 0;">📝 Grounded Answer</p>',
        unsafe_allow_html=True,
    )
    if answer:
        st.markdown(answer)
    else:
        st.warning("No answer text was generated for this investigation.")

    # ── Limitations ────────────────────────────────────────────────────────────
    if limitations:
        with st.container():
            st.markdown(
                """
                <div style="background:rgba(187,128,9,0.1); border:1px solid #bb8009;
                border-radius:8px; padding:12px 16px; margin:12px 0;">
                    <p style="margin:0 0 6px 0; font-size:0.82rem; font-weight:700; color:#d29922;">
                        ⚠️ Verification Limits &amp; Evidence Gaps
                    </p>
                """,
                unsafe_allow_html=True,
            )
            for lim in limitations:
                st.markdown(f"- {lim}")
            st.markdown("</div>", unsafe_allow_html=True)

    # ── Citations ──────────────────────────────────────────────────────────────
    st.divider()
    repo_url = repo_info.get("url") or st.session_state.get("selected_repo", {}).get("url")
    render_evidence_citations(
        citations=citations,
        repository_url=repo_url,
        answer_text=answer,
    )

    # ── Diagnostics ────────────────────────────────────────────────────────────
    st.divider()
    with st.expander("🛠️ Investigation Diagnostics", expanded=False):
        d1, d2, d3, d4 = st.columns(4, gap="small")

        engine = metadata.get("engine") or metadata.get("execution_engine") or "LangGraph"
        rounds = metadata.get("rounds_executed") or metadata.get("retrieval_rounds")
        repair = metadata.get("repair_attempts", 0)
        total_time = (
            metadata.get("total_time_seconds")
            or investigation.get("execution_time_seconds")
        )

        with d1:
            st.metric("Engine", engine)
        with d2:
            st.metric("Retrieval Rounds", rounds if rounds is not None else "1")
        with d3:
            st.metric("Citation Repairs", repair)
        with d4:
            st.metric("Total Time", format_execution_time(total_time))

        if evidence_summary:
            st.markdown("##### Evidence Summary")
            st.json(evidence_summary)

        if metadata:
            st.markdown("##### Raw Workflow Metadata")
            st.json(metadata)
