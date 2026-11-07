"""
Repository setup pipeline: stage model, readiness helpers, and a runner that
executes the remaining indexing steps with live progress.
"""

from __future__ import annotations

import html
import time
from dataclasses import dataclass
from typing import Any, Callable

import streamlit as st

from frontend.api_client import ApiClient, ApiClientError


@dataclass(frozen=True)
class PipelineStep:
    stage: str  # stage reached once this step completes
    label: str
    description: str


# Order matters: each step's stage is the next step's starting point.
PIPELINE_STEPS: tuple[PipelineStep, ...] = (
    PipelineStep("cloned", "Clone", "Download the repository from GitHub"),
    PipelineStep("parsed", "Parse code", "Extract files, symbols, imports and calls"),
    PipelineStep("chunked", "Chunk", "Split source into searchable code chunks"),
    PipelineStep("indexed", "Embed", "Create semantic vectors for search"),
)
STAGE_ORDER = [step.stage for step in PIPELINE_STEPS]

STAGE_LABELS = {
    "pending": "Pending",
    "cloning": "Cloning…",
    "cloned": "Cloned",
    "parsed": "Parsed",
    "chunked": "Chunked",
    "indexed": "Ready",
    "failed": "Failed",
}


OUTDATED_BACKEND_MESSAGE = (
    "**The backend is running an older version** that doesn't report indexing progress, "
    "so the app can't tell whether a repository is indexed. Restart it with "
    "`.\\start_server.ps1` and refresh this page. Investigations still work in the meantime."
)


def stage_unknown(repo: dict[str, Any] | None) -> bool:
    """True when the backend predates stage tracking (no ``stage`` field)."""
    return isinstance(repo, dict) and "stage" not in repo


def backend_outdated(repos: list[dict[str, Any]] | None) -> bool:
    return any(stage_unknown(r) for r in repos or [])


def repo_stage(repo: dict[str, Any] | None) -> str:
    """Return the repository's pipeline stage, falling back to its stored status."""
    if not isinstance(repo, dict):
        return "pending"
    return str(repo.get("stage") or repo.get("status") or "pending")


def is_ready(repo: dict[str, Any] | None) -> bool:
    return repo_stage(repo) == "indexed"


def completed_steps(repo: dict[str, Any] | None) -> int:
    """Number of pipeline steps finished (0–4)."""
    stage = repo_stage(repo)
    if stage in STAGE_ORDER:
        return STAGE_ORDER.index(stage) + 1
    return 0


def stage_label(repo: dict[str, Any] | None) -> str:
    if stage_unknown(repo):
        return "Status unknown"
    stage = repo_stage(repo)
    return STAGE_LABELS.get(stage, stage.capitalize())


def stage_tone(repo: dict[str, Any] | None) -> str:
    """CSS tone modifier for stage chips: ready / progress / failed / idle."""
    if stage_unknown(repo):
        return "idle"
    stage = repo_stage(repo)
    if stage == "indexed":
        return "ready"
    if stage == "failed":
        return "failed"
    if stage in ("cloning", "cloned", "parsed", "chunked"):
        return "progress"
    return "idle"


def stage_chip_html(repo: dict[str, Any] | None) -> str:
    return (
        f'<span class="oi-chip oi-chip--{stage_tone(repo)}">'
        f'<span class="oi-chip__dot"></span>{html.escape(stage_label(repo))}</span>'
    )


def stepper_html(repo: dict[str, Any] | None) -> str:
    """Compact horizontal stepper showing which pipeline steps are done."""
    done = completed_steps(repo)
    failed = repo_stage(repo) == "failed"
    items = []
    for i, step in enumerate(PIPELINE_STEPS):
        if i < done:
            state = "done"
        elif i == done:
            state = "failed" if failed else "next"
        else:
            state = "todo"
        items.append(
            f'<li class="oi-stepper__item oi-stepper__item--{state}">'
            f'<span class="oi-stepper__dot">{"✓" if state == "done" else i + 1}</span>'
            f'<span class="oi-stepper__label">{html.escape(step.label)}</span></li>'
        )
    return f'<ol class="oi-stepper">{"".join(items)}</ol>'


def remaining_steps(repo: dict[str, Any] | None) -> list[PipelineStep]:
    """Indexing steps still to run (cloning is handled when the repo is added)."""
    done = max(completed_steps(repo), 1)
    return list(PIPELINE_STEPS[done:])


def pick_default_repo(repos: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Prefer the first ready repository, otherwise the first one listed."""
    valid = [r for r in repos if isinstance(r, dict) and "id" in r]
    for repo in valid:
        if is_ready(repo):
            return repo
    return valid[0] if valid else None


def _step_call(api_client: ApiClient, stage: str) -> Callable[[int], dict[str, Any]]:
    return {
        "parsed": api_client.index_repository,
        "chunked": api_client.create_chunks,
        "indexed": api_client.index_vectors,
    }[stage]


def _summarize(stage: str, result: dict[str, Any]) -> str:
    if stage == "parsed":
        files = result.get("indexed_files")
        symbols = result.get("indexed_symbols")
        if files is not None:
            return f"{files:,} files · {symbols or 0:,} symbols"
    if stage == "chunked" and result.get("chunks_created") is not None:
        return f"{result['chunks_created']:,} chunks"
    if stage == "indexed" and result.get("chunks_indexed") is not None:
        return f"{result['chunks_indexed']:,} vectors"
    return "done"


def run_pipeline(
    api_client: ApiClient,
    repo: dict[str, Any],
    clone_url: str | None = None,
) -> tuple[bool, dict[str, Any] | None, str]:
    """
    Run every remaining setup step for a repository with a live status panel.

    When ``clone_url`` is given the repository is registered (cloned) first and
    ``repo`` may be an empty dict. Returns (success, repository_record, message).
    """
    name = repo.get("name") or (clone_url or "").rstrip("/").split("/")[-1] or "repository"
    steps = list(PIPELINE_STEPS) if clone_url else remaining_steps(repo)
    if not steps:
        return True, repo, f"{name} is already indexed."

    started = time.time()
    with st.status(f"Setting up **{name}**…", expanded=True) as status:
        progress = st.progress(0.0)
        for i, step in enumerate(steps):
            st.write(f"**{i + 1}/{len(steps)} · {step.label}** — {step.description}")
            try:
                if step.stage == "cloned":
                    repo = api_client.add_repository(clone_url or "")
                    name = repo.get("name") or name
                    summary = "cloned"
                else:
                    result = _step_call(api_client, step.stage)(int(repo["id"]))
                    summary = _summarize(step.stage, result)
            except ApiClientError as exc:
                status.update(label=f"{step.label} failed for {name}", state="error", expanded=True)
                st.error(exc.message)
                return False, repo or None, f"{step.label} failed: {exc.message}"
            except Exception as exc:  # pragma: no cover - defensive
                status.update(label=f"{step.label} failed for {name}", state="error", expanded=True)
                st.error(str(exc))
                return False, repo or None, f"{step.label} failed: {exc}"

            st.caption(f"✓ {summary}")
            progress.progress((i + 1) / len(steps))

        elapsed = time.time() - started
        status.update(
            label=f"{name} is ready to investigate ({elapsed:.0f}s)",
            state="complete",
            expanded=False,
        )
    return True, repo, f"{name} is indexed and ready to investigate."
