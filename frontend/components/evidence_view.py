"""
Component for rendering evidence citations, code snippets, and source references.
"""

from __future__ import annotations

import streamlit as st
from typing import Any
from frontend.utils.formatting import (
    generate_github_permalink,
    detect_unmapped_citations,
)


def render_evidence_citations(
    citations: list[dict[str, Any]] | None,
    repository_url: str | None = None,
    answer_text: str | None = None,
) -> None:
    """
    Render expandable source code evidence cards with syntax-highlighted code snippets,
    line boundary annotations, relevance notes, and safe GitHub permalinks.
    """
    count = len(citations or [])
    st.html(
        '<div class="oi-section-label" style="margin-top:1.25rem">'
        f'Source evidence{f" · {count}" if count else ""}</div>'
    )

    # Check for unmapped citations referenced in answer text
    if answer_text:
        unmapped = detect_unmapped_citations(answer_text, citations or [])
        if unmapped:
            st.warning(
                f"**Citation mismatch:** the answer mentions `{', '.join(unmapped)}`, "
                "but no matching source was returned. Treat those claims with care.",
                icon=":material/warning:",
            )

    if not citations:
        st.info("No source citations were returned for this investigation.", icon=":material/info:")
        return

    for idx, citation in enumerate(citations):
        # Support both 'evidence_id' and 'id'
        eid = citation.get("evidence_id") or citation.get("id") or f"E{idx+1}"
        file_path = citation.get("file") or citation.get("file_path") or "Unknown file"
        start_line = citation.get("start_line") or 0
        end_line = citation.get("end_line") or 0
        symbol = citation.get("symbol") or citation.get("symbol_name") or ""
        snippet = citation.get("snippet") or ""
        relevance = citation.get("relevance") or ""

        # Construct line range string
        if start_line > 0 and end_line > start_line:
            line_str = f"L{start_line}–{end_line}"
        elif start_line > 0:
            line_str = f"L{start_line}"
        else:
            line_str = "Line range unavailable"

        # Title for expander
        expander_title = f"**{eid}** · `{file_path}` · {line_str}" + (f" · `{symbol}`" if symbol else "")

        # Expand the first 3 by default
        is_expanded = idx < 3

        with st.expander(expander_title, expanded=is_expanded):
            if relevance:
                st.markdown(relevance)

            # Safe GitHub Permalink
            gh_url = generate_github_permalink(
                repository_url, file_path, start_line, end_line
            )
            if gh_url:
                st.link_button(
                    "View on GitHub",
                    gh_url,
                    icon=":material/open_in_new:",
                    help="Open source code on GitHub at exact lines",
                )

            # Code Snippet with language inference
            if snippet:
                ext = file_path.split(".")[-1].lower() if "." in file_path else ""
                lang_map = {
                    "py": "python",
                    "ts": "typescript",
                    "tsx": "tsx",
                    "js": "javascript",
                    "jsx": "jsx",
                    "json": "json",
                    "html": "html",
                    "css": "css",
                    "sql": "sql",
                    "yaml": "yaml",
                    "yml": "yaml",
                    "rs": "rust",
                    "go": "go",
                }
                code_lang = lang_map.get(ext, "")
                st.code(snippet, language=code_lang, line_numbers=True)
            else:
                st.caption("No code snippet text provided for this citation.")
