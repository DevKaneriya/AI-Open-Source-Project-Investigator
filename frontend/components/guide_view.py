"""
Getting Started guide: a live setup checklist that reflects real backend state,
an overview of the workflow, and tips for asking good questions.
"""

from __future__ import annotations

import html
from dataclasses import dataclass

import streamlit as st

from frontend.api_client import ApiClient, ApiClientError
from frontend.utils.access import is_admin
from frontend.utils.history_manager import load_history
from frontend.utils.navigation import go, select_repository, show_flash
from frontend.utils.pipeline import OUTDATED_BACKEND_MESSAGE, backend_outdated, is_ready


@dataclass
class ChecklistItem:
    title: str
    done_text: str
    todo_text: str
    done: bool
    action_label: str
    action_page: str


def _backend_online(api_client: ApiClient) -> bool:
    try:
        return api_client.health_check().get("status") == "healthy"
    except ApiClientError:
        return False


def _checklist(api_client: ApiClient) -> list[ChecklistItem]:
    repos = st.session_state.get("repositories", [])
    online = _backend_online(api_client)
    has_repo = bool(repos)
    ready = [r for r in repos if is_ready(r)]
    asked = bool(load_history())

    return [
        ChecklistItem(
            "Start the backend",
            "The API server is running and reachable.",
            "Run <code>.\\start_server.ps1</code> from the project folder, then refresh this page.",
            online,
            "Check system status",
            "status",
        ),
        ChecklistItem(
            "Add a GitHub repository",
            f"{len(repos)} repositor{'y' if len(repos) == 1 else 'ies'} added.",
            "Paste a public GitHub URL on the Repositories page. It is cloned to your machine.",
            has_repo,
            "Add a repository",
            "repositories",
        ),
        ChecklistItem(
            "Index it",
            f"{len(ready)} repositor{'y is' if len(ready) == 1 else 'ies are'} ready to investigate.",
            "Indexing parses the code, splits it into chunks and builds semantic search. "
            "It runs automatically when you add a repository and takes a few minutes for large projects.",
            bool(ready),
            "Open repositories",
            "repositories",
        ),
        ChecklistItem(
            "Ask your first question",
            "You've run an investigation. Your history is in the sidebar.",
            "Ask in plain English, e.g. <i>“Where is authentication handled?”</i>",
            asked,
            "Start investigating",
            "investigate",
        ),
    ]


def _render_checklist(items: list[ChecklistItem]) -> None:
    done_count = sum(item.done for item in items)
    current = next((i for i, item in enumerate(items) if not item.done), None)

    with st.container(border=True):
        st.html(
            f"""
            <div class="oi-section-label">Your setup</div>
            <div class="oi-meter">
                <div class="oi-meter__track"><div class="oi-meter__fill" style="width:{done_count / len(items) * 100:.0f}%"></div></div>
                <div class="oi-meter__text">{done_count} of {len(items)} done</div>
            </div>
            """
        )
        for i, item in enumerate(items):
            if item.done:
                state, icon, text = "done", "✓", item.done_text
            elif i == current:
                state, icon, text = "current", str(i + 1), item.todo_text
            else:
                state, icon, text = "todo", str(i + 1), item.todo_text
            st.html(
                f"""
                <div class="oi-check oi-check--{state}">
                    <div class="oi-check__icon">{icon}</div>
                    <div class="oi-check__body">
                        <div class="oi-check__title">{html.escape(item.title)}</div>
                        <div class="oi-check__desc">{text}</div>
                    </div>
                </div>
                """
            )
            if i == current:
                if st.button(item.action_label, type="primary", key=f"guide_action_{i}", icon=":material/arrow_forward:"):
                    go(item.action_page)

        if current is None:
            st.success("You're all set. Ask anything about your indexed repositories.")
            if st.button("Go to Investigate", type="primary", icon=":material/search:", key="guide_done"):
                go("investigate")


def _render_how_it_works() -> None:
    st.html('<div class="oi-section-label" style="margin-top:1.5rem">How it works</div>')
    cards = [
        ("1", "Add", "Paste a public GitHub URL. The repository is cloned locally. Nothing is uploaded elsewhere."),
        ("2", "Index", "The code is parsed into symbols, imports and call graphs, then embedded for semantic search."),
        ("3", "Ask", "Ask questions in plain English. Every claim in the answer cites the exact source lines."),
    ]
    cols = st.columns(3, gap="medium")
    for col, (num, title, body) in zip(cols, cards):
        with col:
            st.html(
                f'<div class="oi-card"><div class="oi-card__num">{num}</div>'
                f"<h4>{title}</h4><p>{body}</p></div>"
            )


def _render_demo_intro() -> None:
    """What a visitor to the public demo sees in place of the setup checklist."""
    ready = [r for r in st.session_state.get("repositories", []) if is_ready(r)]
    with st.container(border=True):
        st.html('<div class="oi-section-label">Try the live demo</div>')
        steps = [
            ("1", "Pick a repository", "Choose one in the sidebar under <b>Active repository</b>. "
             + (f"{len(ready)} {'is' if len(ready) == 1 else 'are'} ready to explore." if ready else "")),
            ("2", "Ask a question", "Type it in plain English, or start from one of the example chips."),
            ("3", "Check the evidence", "Every claim cites source snippets you can open on GitHub."),
        ]
        for num, title, desc in steps:
            st.html(
                f'<div class="oi-check oi-check--current"><div class="oi-check__icon">{num}</div>'
                f'<div class="oi-check__body"><div class="oi-check__title">{title}</div>'
                f'<div class="oi-check__desc">{desc}</div></div></div>'
            )
        if st.button("Start investigating", type="primary", icon=":material/arrow_forward:", key="demo_start"):
            go("investigate")


def _render_tips(show_troubleshooting: bool = True) -> None:
    st.html('<div class="oi-section-label" style="margin-top:1.75rem">Getting better answers</div>')
    left, right = st.columns(2, gap="medium")
    with left:
        with st.container(border=True):
            st.markdown("**Ask specific questions**")
            st.html(
                """
                <div class="oi-tip"><span class="oi-tip__tag oi-tip__tag--good">GOOD</span>
                <span>“How does <code>CartSyncView</code> handle POST requests?”</span></div>
                <div class="oi-tip"><span class="oi-tip__tag oi-tip__tag--good">GOOD</span>
                <span>“Trace checkout from the Angular UI to the Django backend.”</span></div>
                <div class="oi-tip"><span class="oi-tip__tag oi-tip__tag--bad">VAGUE</span>
                <span>“Explain the code.”</span></div>
                """
            )
            st.caption("Name the functions, classes, files or flows you care about when you know them.")
    with right:
        with st.container(border=True):
            st.markdown("**Reading the results**")
            st.markdown(
                "- **High confidence**: the evidence fully supports the answer.\n"
                "- **Partial evidence**: some parts couldn't be verified. Check the listed gaps.\n"
                "- **Insufficient evidence**: rephrase, or make sure the repository is fully indexed.\n"
                "- Markers like **[E1]** link each claim to a source snippet below the answer."
            )

    if not show_troubleshooting:
        return

    with st.expander("Troubleshooting", icon=":material/build:"):
        st.markdown(
            "- **Backend unreachable**: start it with `.\\start_server.ps1` (or "
            "`python -m uvicorn backend.app.main:app --port 8000`) and refresh.\n"
            "- **Cloning fails**: check the URL is a public `https://github.com/owner/repo` link.\n"
            "- **Indexing seems slow**: building the vectors is the longest step. Large repositories can take several minutes.\n"
            "- **Answers miss obvious code**: open **Repositories** and make sure the repository shows **Ready**. "
            "If it doesn't, click *Continue indexing*.\n"
            "- **LLM errors**: check that `GROQ_API_KEY` is set in your `.env` file."
        )


def render_guide_view(api_client: ApiClient) -> None:
    show_flash()
    st.html(
        """
        <div class="oi-hero">
            <span class="oi-eyebrow">Getting started</span>
            <h1>Understand any codebase, with receipts.</h1>
            <p>Point OpenSource Investigator at a GitHub repository, let it index the code,
            then ask questions. Answers are grounded in the source and cite the exact files and lines.</p>
        </div>
        """
    )

    if not is_admin():
        _render_demo_intro()
        _render_how_it_works()
        _render_tips(show_troubleshooting=False)
        return

    if backend_outdated(st.session_state.get("repositories")):
        st.warning(OUTDATED_BACKEND_MESSAGE, icon=":material/update:")

    _render_checklist(_checklist(api_client))

    # Offer a shortcut to a ready repo when one exists but isn't selected
    ready = [r for r in st.session_state.get("repositories", []) if is_ready(r)]
    selected = st.session_state.get("selected_repo")
    if ready and not is_ready(selected):
        st.info(f"**{ready[0].get('name')}** is ready, but your active repository isn't indexed yet.")
        if st.button(f"Switch to {ready[0].get('name')}", key="guide_switch_ready"):
            select_repository(ready[0])
            go("investigate")

    _render_how_it_works()
    _render_tips()
