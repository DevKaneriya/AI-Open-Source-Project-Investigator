"""
Error pop-ups: every failure in the UI is reported here and shown in a dialog
with a plain-language explanation, what to try next and the technical details.

Usage:
    report_error("Couldn't delete the repository", exc, hint="Refresh and try again.")

`render_error_dialog()` runs once per script run (from the app entry point) and
opens a dialog for the oldest unseen error. Errors are kept in session state, so
an error reported just before `st.rerun()` is still shown on the next run.

Each error is logged with a short ID that also appears in the dialog, so it can
be matched with the server logs. Technical details (endpoint, HTTP status,
backend response, traceback) are shown only to the owner in demo mode.
"""

from __future__ import annotations

import json
import logging
import time
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import streamlit as st

logger = logging.getLogger("investigator_ui_errors")

QUEUE_KEY = "error_queue"
ACTIVE_KEYS = "error_active_keys"
MAX_QUEUE = 5


@dataclass
class ErrorReport:
    title: str
    message: str
    hint: str | None = None
    error_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    occurred_at: float = field(default_factory=time.time)
    details: dict[str, Any] = field(default_factory=dict)
    traceback_text: str | None = None
    key: str | None = None


def _default_hint(exc: BaseException | None) -> str | None:
    # Imported lazily to avoid a circular import with api_client users.
    from frontend.api_client import (
        BackendConnectionError,
        BackendTimeoutError,
        EndpointNotImplementedError,
        RepositoryNotFoundError,
    )
    from frontend.utils.access import demo_mode

    if isinstance(exc, BackendConnectionError):
        if demo_mode():
            return "The backend may still be starting. Wait a minute, then refresh the page."
        return "Start the backend with `.\\start_server.ps1`, then try again. Check the System status page."
    if isinstance(exc, BackendTimeoutError):
        return "The server took too long. Try again, or for questions, ask something more specific."
    if isinstance(exc, RepositoryNotFoundError):
        return "The repository may have been deleted. Pick another one in the sidebar."
    if isinstance(exc, EndpointNotImplementedError):
        return "The frontend and backend versions don't match. Restart the backend so it runs the latest code."
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and status >= 500:
        return "Something failed on the server. Try again; if it keeps happening, check the server logs for this error ID."
    if isinstance(status, int) and 400 <= status < 500:
        return "The request was rejected. Check the input and try again."
    return None


def _exception_details(exc: BaseException) -> dict[str, Any]:
    details: dict[str, Any] = {"Error type": type(exc).__name__}
    for attr, label in (
        ("method", "Request"),
        ("endpoint", "Endpoint"),
        ("status_code", "HTTP status"),
        ("elapsed_seconds", "Elapsed (s)"),
    ):
        value = getattr(exc, attr, None)
        if value is not None:
            details[label] = value
    body = getattr(exc, "response_body", None)
    if body:
        details["Backend response"] = body
        if isinstance(body, dict) and body.get("error_id"):
            details["Backend error ID"] = body["error_id"]
    cause = exc.__cause__ or exc.__context__
    if cause is not None:
        details["Underlying cause"] = f"{type(cause).__name__}: {cause}"
    return details


def report_error(
    title: str,
    error: BaseException | str,
    *,
    hint: str | None = None,
    context: dict[str, Any] | None = None,
    key: str | None = None,
) -> ErrorReport | None:
    """Queue an error pop-up and log it.

    ``key`` marks a recurring condition (for example "backend unreachable"):
    it is shown once and not again until ``clear_error(key)`` is called after
    the condition recovers. Returns the report, or None when suppressed.
    """
    active: set[str] = st.session_state.setdefault(ACTIVE_KEYS, set())
    if key and key in active:
        return None

    if isinstance(error, BaseException):
        message = getattr(error, "message", None) or str(error) or type(error).__name__
        status = getattr(error, "status_code", None)
        from frontend.api_client import BackendConnectionError, BackendTimeoutError

        safe = isinstance(error, (BackendConnectionError, BackendTimeoutError)) or (
            isinstance(status, int) and 400 <= status < 500
        )
        if not safe and not _can_see_details():
            # Public visitors get a plain explanation; raw server errors can name internals.
            message = "The server hit an error while handling this request. It has been logged with the ID below."
        details = _exception_details(error)
        tb = "".join(traceback.format_exception(type(error), error, error.__traceback__))
        hint = hint or _default_hint(error)
    else:
        message, details, tb = str(error), {}, None

    if context:
        details = {**context, **details}

    report = ErrorReport(title=title, message=message, hint=hint, details=details, traceback_text=tb, key=key)
    logger.error(
        "[UI error %s] %s: %s | details=%s",
        report.error_id, title, message, json.dumps(details, default=str)[:2000],
        exc_info=error if isinstance(error, BaseException) else None,
    )

    queue: list[ErrorReport] = st.session_state.setdefault(QUEUE_KEY, [])
    queue.append(report)
    del queue[:-MAX_QUEUE]
    if key:
        active.add(key)
    return report


def clear_error(key: str) -> None:
    """Allow a recurring error to pop up again (call when the condition recovers)."""
    st.session_state.setdefault(ACTIVE_KEYS, set()).discard(key)


def _dismiss_current() -> None:
    queue: list[ErrorReport] = st.session_state.get(QUEUE_KEY, [])
    if queue:
        queue.pop(0)


def _can_see_details() -> bool:
    from frontend.utils.access import is_admin
    return is_admin()


@st.dialog("Something went wrong", width="large", icon=":material/error:", on_dismiss=_dismiss_current)
def _error_dialog(report: ErrorReport, remaining: int) -> None:
    st.markdown(f"#### {report.title}")
    st.error(report.message, icon=":material/error:")
    if report.hint:
        st.info(f"**What you can try:** {report.hint}", icon=":material/lightbulb:")

    when = datetime.fromtimestamp(report.occurred_at, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    st.caption(f"Error ID `{report.error_id}` · {when}")

    if _can_see_details():
        if report.details:
            with st.expander("Technical details", icon=":material/data_object:", expanded=True):
                for label, value in report.details.items():
                    if isinstance(value, (dict, list)):
                        st.markdown(f"**{label}**")
                        st.json(value, expanded=False)
                    else:
                        st.markdown(f"**{label}:** `{value}`")
        if report.traceback_text:
            with st.expander("Traceback", icon=":material/code:"):
                st.code(report.traceback_text, language="python")

    label = f"Next error ({remaining} more)" if remaining else "Close"
    if st.button(label, type="primary", key=f"error_close_{report.error_id}", use_container_width=True):
        _dismiss_current()
        st.rerun()


def render_error_dialog() -> None:
    """Open a dialog for the oldest queued error, if any. Call once per run."""
    queue: list[ErrorReport] = st.session_state.get(QUEUE_KEY, [])
    if queue:
        _error_dialog(queue[0], len(queue) - 1)
