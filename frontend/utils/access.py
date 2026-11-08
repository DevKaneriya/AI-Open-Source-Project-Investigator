"""
Public demo mode: visitors can only investigate curated repositories, the
owner unlocks repository management with a password, and investigations are
rate limited to protect the LLM API quota.

Configured through environment variables:
    DEMO_MODE                 "true" to enable (default off for local use)
    ADMIN_PASSWORD            unlocks repository management in demo mode
    DEMO_QUERIES_PER_HOUR     investigations allowed per hour across all visitors (default 40)
    DEMO_SESSION_QUERIES      investigations allowed per visitor session per hour (default 8)
"""

from __future__ import annotations

import hmac
import os
import threading
import time
from collections import deque

import streamlit as st

ADMIN_KEY = "admin_unlocked"
SESSION_HITS_KEY = "demo_query_times"
MAX_QUERY_CHARS = 500
WINDOW_SECONDS = 3600


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, default)))
    except ValueError:
        return default


def demo_mode() -> bool:
    return os.environ.get("DEMO_MODE", "").strip().lower() in ("1", "true", "yes", "on")


def is_admin() -> bool:
    """Everyone manages repositories locally; in demo mode only after unlocking."""
    return not demo_mode() or bool(st.session_state.get(ADMIN_KEY))


def _check_password(candidate: str) -> bool:
    expected = os.environ.get("ADMIN_PASSWORD", "")
    return bool(expected) and hmac.compare_digest(candidate.encode(), expected.encode())


def render_admin_controls() -> None:
    """Sidebar control for the owner to unlock repository management."""
    if not demo_mode():
        return

    if st.session_state.get(ADMIN_KEY):
        st.caption("Admin mode is on.")
        if st.button("Lock admin mode", icon=":material/lock:", type="tertiary", key="admin_lock"):
            st.session_state[ADMIN_KEY] = False
            st.rerun()
        return

    with st.expander("Owner access", icon=":material/admin_panel_settings:"):
        with st.form("admin_unlock_form", border=False):
            password = st.text_input("Admin password", type="password")
            submitted = st.form_submit_button("Unlock")
        if submitted:
            if _check_password(password):
                st.session_state[ADMIN_KEY] = True
                st.rerun()
            else:
                st.error("Incorrect password.")


@st.cache_resource
def _global_query_log() -> tuple[threading.Lock, deque]:
    # Shared by every session in this server process.
    return threading.Lock(), deque()


def _prune(times: deque, now: float) -> None:
    while times and now - times[0] > WINDOW_SECONDS:
        times.popleft()


def _minutes_until_free(times: deque, now: float) -> int:
    return max(1, int((WINDOW_SECONDS - (now - times[0])) // 60) + 1)


def reserve_query(query: str) -> str | None:
    """Count one investigation against the demo limits.

    Returns an explanation when the query is rejected, otherwise None.
    """
    if is_admin():
        return None

    if len(query) > MAX_QUERY_CHARS:
        return f"Please keep questions under {MAX_QUERY_CHARS} characters in the demo."

    now = time.time()
    session_times: deque = st.session_state.setdefault(SESSION_HITS_KEY, deque())
    _prune(session_times, now)
    session_limit = _env_int("DEMO_SESSION_QUERIES", 8)
    if len(session_times) >= session_limit:
        return (
            f"You've reached the demo limit of {session_limit} questions per hour. "
            f"Try again in about {_minutes_until_free(session_times, now)} minutes."
        )

    lock, global_times = _global_query_log()
    with lock:
        _prune(global_times, now)
        if len(global_times) >= _env_int("DEMO_QUERIES_PER_HOUR", 40):
            return (
                "The demo is busy right now and has hit its hourly question limit. "
                f"Please try again in about {_minutes_until_free(global_times, now)} minutes."
            )
        global_times.append(now)

    session_times.append(now)
    return None


def backend_unavailable_message() -> str:
    """What to tell someone when the backend can't be reached."""
    if demo_mode():
        return (
            "**The demo backend is starting up or temporarily unavailable.** "
            "This usually takes under a minute. Please refresh the page shortly."
        )
    return "**Can't reach the backend.** Start it with `.\\start_server.ps1`, then refresh."
