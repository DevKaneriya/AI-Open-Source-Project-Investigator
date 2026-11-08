"""
Hosted entry point for Streamlit Community Cloud.

Community Cloud runs only a Streamlit script, so this file starts the FastAPI
backend as a background process on first load (migrations first), then runs
the normal UI. Local development keeps using:
    streamlit run frontend/streamlit_app.py

Configuration comes from the app's Secrets in the Community Cloud dashboard
(see docs/DEPLOYMENT.md). Required: DATABASE_URL, GROQ_API_KEY, ADMIN_PASSWORD.
"""

from __future__ import annotations

import os
import runpy
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import streamlit as st

ROOT_DIR = Path(__file__).resolve().parent
API_HOST, API_PORT = "127.0.0.1", 8000
API_URL = f"http://{API_HOST}:{API_PORT}"
STARTUP_TIMEOUT_SECONDS = 300

HOSTED_DEFAULTS = {
    "VECTOR_BACKEND": "pgvector",  # the container disk is not persistent
    "DEMO_MODE": "true",
    "API_BASE_URL": API_URL,
}
REQUIRED = ("DATABASE_URL", "GROQ_API_KEY", "ADMIN_PASSWORD")


def _load_secrets_into_env() -> None:
    """Expose top-level string secrets as environment variables for both processes."""
    try:
        items = dict(st.secrets)
    except Exception:  # no secrets file (e.g. a local run)
        items = {}
    for key, value in items.items():
        if isinstance(value, (str, int, float, bool)):
            os.environ.setdefault(key, str(value))
    for key, value in HOSTED_DEFAULTS.items():
        os.environ.setdefault(key, value)


def _api_healthy() -> bool:
    try:
        with urllib.request.urlopen(f"{API_URL}/health", timeout=3) as response:
            return response.status == 200
    except Exception:
        return False


@st.cache_resource(show_spinner="Starting the backend. The first start takes a minute or two...")
def _start_backend() -> subprocess.Popen:
    migrate = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT_DIR, capture_output=True, text=True,
    )
    if migrate.returncode != 0:
        raise RuntimeError(f"Database migration failed:\n{migrate.stderr[-3000:]}")

    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.app.main:app",
         "--host", API_HOST, "--port", str(API_PORT), "--workers", "1"],
        cwd=ROOT_DIR,
    )
    deadline = time.time() + STARTUP_TIMEOUT_SECONDS
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError("The API process exited during startup. Check the app logs.")
        if _api_healthy():
            return process
        time.sleep(2)
    process.terminate()
    raise RuntimeError("The API did not become healthy within 5 minutes.")


def ensure_backend() -> None:
    if _api_healthy():
        return
    process = _start_backend()
    if process.poll() is not None:  # the API crashed since it was started: start it again
        _start_backend.clear()
        _start_backend()


def _fail(title: str, error: BaseException | str, hint: str) -> None:
    """Show a startup failure inline and as an error pop-up, then stop this run."""
    from frontend.utils.errors import render_error_dialog, report_error

    st.error(f"**{title}.** {error}")
    report_error(title, error, hint=hint, key=f"startup:{title}")
    render_error_dialog()
    st.stop()


_load_secrets_into_env()
missing = [name for name in REQUIRED if not os.environ.get(name)]
if missing:
    _fail(
        "The app is missing required secrets",
        f"Not set: {', '.join(missing)}.",
        "Add them under the app's Settings → Secrets on share.streamlit.io; the app restarts on save.",
    )

try:
    ensure_backend()
except RuntimeError as error:
    _start_backend.clear()
    _fail(
        "The backend couldn't start",
        error,
        "Refresh the page to try again. If it keeps failing, open Manage app → logs and check DATABASE_URL.",
    )

runpy.run_path(str(ROOT_DIR / "frontend" / "streamlit_app.py"), run_name="__main__")
