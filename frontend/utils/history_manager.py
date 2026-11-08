"""
Investigation history management.
Provides persistent storage (JSON on disk) and session state synchronization
so recent queries survive browser reloads and render immediately in the UI.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import time
from typing import Any

import streamlit as st

from frontend.utils.access import demo_mode

logger = logging.getLogger(__name__)

HISTORY_FILE = Path(__file__).resolve().parent.parent / ".investigation_history.json"
MAX_HISTORY_ENTRIES = 20


def get_history_file_path() -> Path:
    """Return the path to the persistent history file."""
    return HISTORY_FILE


def load_history() -> list[dict[str, Any]]:
    """
    Load investigation history from persistent storage into session_state.
    If session_state already has history, returns it.
    If not, loads from disk file.
    """
    if "investigation_history" in st.session_state and st.session_state["investigation_history"]:
        return st.session_state["investigation_history"]

    history: list[dict[str, Any]] = []
    # In the public demo, history stays per visitor and is never shared via disk.
    if HISTORY_FILE.exists() and not demo_mode():
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    history = [item for item in data if isinstance(item, dict) and "query" in item]
        except Exception as exc:
            logger.warning("Failed to load investigation history from %s: %s", HISTORY_FILE, exc)
            history = []

    st.session_state["investigation_history"] = history
    return history


def save_history_entry(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Prepend a new entry to the investigation history, keep the most recent entries,
    and persist to disk and session state.
    """
    current_history = load_history()

    # Deduplicate if exact same query and repository already exists at the top
    filtered = [
        item for item in current_history
        if not (
            item.get("query") == entry.get("query")
            and item.get("repository_id") == entry.get("repository_id")
        )
    ]

    # Ensure timestamp is set
    if not entry.get("timestamp"):
        entry["timestamp"] = time.time()

    new_history = [entry] + filtered[: MAX_HISTORY_ENTRIES - 1]
    st.session_state["investigation_history"] = new_history

    if demo_mode():
        return new_history

    # Persist to disk
    try:
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        # Write atomically using temp file or direct write
        temp_file = HISTORY_FILE.with_suffix(".tmp")
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(new_history, f, indent=2, default=str)
        if temp_file.exists():
            temp_file.replace(HISTORY_FILE)
    except Exception as exc:
        logger.warning("Failed to save investigation history to %s: %s", HISTORY_FILE, exc)

    return new_history


def clear_history() -> None:
    """Clear all investigation history from session state and disk."""
    st.session_state["investigation_history"] = []
    if HISTORY_FILE.exists() and not demo_mode():
        try:
            os.remove(HISTORY_FILE)
        except Exception as exc:
            logger.warning("Failed to remove history file %s: %s", HISTORY_FILE, exc)
