"""
Loading skeletons shaped like each page.

Streamlit's built-in "app skeleton" is a generic layout that looks nothing like
this app. These skeletons mirror the real pages (header, cards, checklist,
question box ...) and are shown while a page waits for the backend, then
replaced by the real content.

Every block carries its own small stylesheet with fallback colours, so it also
renders correctly before the app's stylesheet has loaded (on start-up).

Usage:
    with page_skeleton("repositories"):
        repos = slow_call()
"""

from __future__ import annotations

import html
from contextlib import contextmanager
from typing import Iterator

import streamlit as st

_CSS = """
<style>
.oi-skel { display: flex; flex-direction: column; gap: 0.75rem; }
.oi-skel__b {
  border-radius: 0.45rem;
  background: linear-gradient(90deg, #182030 0%, #222c40 45%, #182030 90%);
  background-size: 300% 100%;
  animation: oi-skel-shimmer 1.4s ease-in-out infinite;
  flex: 0 0 auto;
}
.oi-skel__box {
  border: 1px solid var(--oi-border, #232d3d);
  border-radius: var(--oi-radius, 0.75rem);
  background: var(--oi-bg-raised, #131a26);
  padding: 1rem 1.15rem;
  display: flex; flex-direction: column; gap: 0.65rem;
}
.oi-skel__row { display: flex; gap: 0.6rem; align-items: center; flex-wrap: wrap; }
.oi-skel__grow { flex: 1 1 auto; }
.oi-skel__cols { display: grid; grid-template-columns: repeat(auto-fit, minmax(12rem, 1fr)); gap: 0.9rem; }
.oi-skel__round { border-radius: 999px; }
.oi-skel__msg {
  display: flex; align-items: center; gap: 0.55rem;
  color: var(--oi-muted, #8a96aa); font-size: 0.9rem; margin-bottom: 0.25rem;
}
.oi-skel__dot {
  width: 0.55rem; height: 0.55rem; border-radius: 999px;
  background: var(--oi-accent, #4f8cff);
  animation: oi-skel-pulse 1.2s ease-in-out infinite;
}
@keyframes oi-skel-shimmer { 0% { background-position: 100% 0; } 100% { background-position: 0 0; } }
@keyframes oi-skel-pulse { 0%, 100% { opacity: 0.35; } 50% { opacity: 1; } }
@media (prefers-reduced-motion: reduce) {
  .oi-skel__b, .oi-skel__dot { animation: none; }
}
</style>
"""


def _bar(width: str, height: str = "0.9rem", extra: str = "") -> str:
    return f'<div class="oi-skel__b {extra}" style="width:{width};height:{height}"></div>'


def _box(*parts: str) -> str:
    return f'<div class="oi-skel__box">{"".join(parts)}</div>'


def _row(*parts: str) -> str:
    return f'<div class="oi-skel__row">{"".join(parts)}</div>'


def _header() -> str:
    # Eyebrow, title, intro line: matches .oi-page-header.
    return (
        '<div class="oi-skel" style="gap:0.5rem;margin-bottom:0.5rem">'
        + _bar("5.5rem", "0.7rem")
        + _bar("min(22rem, 70%)", "1.9rem")
        + _bar("min(36rem, 90%)", "0.95rem")
        + "</div>"
    )


def _investigate() -> str:
    return "".join(
        [
            _header(),
            _box(_row(_bar("min(14rem, 50%)", "1.1rem"), _bar("4.5rem", "1.3rem", "oi-skel__round")), _bar("11rem", "0.75rem")),
            _bar("7rem", "0.8rem"),
            _row(*[_bar(w, "2rem", "oi-skel__round") for w in ("13rem", "11rem", "12rem", "10rem")]),
            _bar("6.5rem", "0.8rem"),
            _bar("100%", "140px"),
            _bar("8.5rem", "2.4rem"),
        ]
    )


def _repo_card() -> str:
    step = _row(_bar("0.9rem", "0.9rem", "oi-skel__round"), _bar("4rem", "0.75rem"))
    return _box(
        _row(_bar("min(14rem, 45%)", "1.1rem"), _bar("4.5rem", "1.3rem", "oi-skel__round")),
        _bar("min(20rem, 70%)", "0.75rem"),
        _row(step, step, step, step),
        _row(_bar("8rem", "2.2rem"), _bar("6rem", "2.2rem")),
    )


def _repositories() -> str:
    return "".join(
        [
            _header(),
            _box(_bar("11rem", "0.85rem"), _row(f'<div class="oi-skel__grow">{_bar("100%", "2.5rem")}</div>', _bar("7rem", "2.5rem")), _bar("min(26rem, 80%)", "0.75rem")),
            _bar("10rem", "0.75rem"),
            _repo_card(),
            _repo_card(),
        ]
    )


def checklist_html() -> str:
    """The "Your setup" checklist on Getting started."""
    item = _row(_bar("1.9rem", "1.9rem", "oi-skel__round"), '<div class="oi-skel__grow oi-skel" style="gap:0.4rem">' + _bar("min(14rem, 60%)", "0.95rem") + _bar("min(24rem, 85%)", "0.8rem") + "</div>")
    return _CSS + '<div class="oi-skel">' + _box(_bar("6rem", "0.75rem"), _bar("100%", "0.5rem", "oi-skel__round"), item, item, item, item) + "</div>"


def _guide() -> str:
    hero = _box(_bar("6.5rem", "0.7rem"), _bar("min(28rem, 85%)", "2.1rem"), _bar("min(38rem, 95%)", "0.95rem"), _bar("min(30rem, 75%)", "0.95rem"))
    card = _box(_bar("1.8rem", "1.8rem", "oi-skel__round"), _bar("70%", "0.95rem"), _bar("95%", "0.8rem"), _bar("80%", "0.8rem"))
    return hero + checklist_html().replace(_CSS, "") + _bar("7rem", "0.75rem") + f'<div class="oi-skel__cols">{card * 3}</div>'


def status_html() -> str:
    """The health result block on System status."""
    metric = _box(_bar("5rem", "0.75rem"), _bar("7rem", "1.6rem"))
    return _CSS + '<div class="oi-skel">' + _bar("100%", "3rem") + f'<div class="oi-skel__cols">{metric * 2}</div>' + _bar("min(24rem, 80%)", "0.75rem") + "</div>"


def _status() -> str:
    return _header() + status_html().replace(_CSS, "")


_PAGES = {
    "investigate": _investigate,
    "repositories": _repositories,
    "guide": _guide,
    "status": _status,
}


def page_html(kind: str, message: str | None = None) -> str:
    """Skeleton for a whole page; ``kind`` is a page's url path (unknown → investigate)."""
    body = _PAGES.get(kind, _investigate)()
    note = (
        f'<div class="oi-skel__msg" role="status"><span class="oi-skel__dot"></span>{html.escape(message)}</div>'
        if message else ""
    )
    return _CSS + f'<div class="oi-skel" aria-busy="true" aria-label="Loading">{note}{body}</div>'


def sidebar_html() -> str:
    return _CSS + (
        '<div class="oi-skel" aria-hidden="true">'
        + _row(_bar("2.2rem", "2.2rem"), '<div class="oi-skel" style="gap:0.35rem">' + _bar("9rem", "0.9rem") + _bar("7rem", "0.7rem") + "</div>")
        + '<div style="height:0.75rem"></div>'
        + _bar("7rem", "0.7rem")
        + _bar("100%", "2.5rem")
        + '<div style="height:0.75rem"></div>'
        + _bar("6rem", "0.7rem")
        + _bar("90%", "0.85rem")
        + _bar("75%", "0.85rem")
        + _bar("82%", "0.85rem")
        + "</div>"
    )


@contextmanager
def page_skeleton(kind: str, message: str | None = None, sidebar: bool = True) -> Iterator[None]:
    """Show a page-shaped skeleton (and sidebar skeleton) while the block runs."""
    main = st.empty()
    main.html(page_html(kind, message))
    side = None
    if sidebar:
        side = st.sidebar.empty()
        side.html(sidebar_html())
    try:
        yield
    finally:
        main.empty()
        if side is not None:
            side.empty()


@contextmanager
def block_skeleton(markup: str) -> Iterator[None]:
    """Show a section skeleton (e.g. ``checklist_html()``) in place while the block runs."""
    slot = st.empty()
    slot.html(markup)
    try:
        yield
    finally:
        slot.empty()
