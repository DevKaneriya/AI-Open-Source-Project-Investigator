"""
Formatting and presentation utilities for OpenSource Investigator UI.
"""

from datetime import datetime, timezone
import re
from urllib.parse import urlparse


def format_timestamp(ts: str | float | int | None) -> str:
    """Format an ISO string, epoch float/int, or None into a readable UTC timestamp."""
    if ts is None:
        return "N/A"
    try:
        if isinstance(ts, (int, float)):
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
        if isinstance(ts, str):
            # Parse ISO formatted string
            clean_ts = ts.replace("Z", "+00:00")
            dt = datetime.fromisoformat(clean_ts)
            return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return str(ts)
    return str(ts)


def format_execution_time(seconds: float | int | None) -> str:
    """Format execution duration in seconds to a friendly string."""
    if seconds is None:
        return "N/A"
    try:
        sec = float(seconds)
        if sec < 1.0:
            return f"{sec * 1000:.0f} ms"
        return f"{sec:.2f} s"
    except Exception:
        return f"{seconds} s"


def get_confidence_badge_html(confidence: str | None) -> str:
    """
    Generate an HTML status badge for confidence levels:
    - high: Emerald / Green
    - partial: Amber / Yellow
    - insufficient: Crimson / Red
    """
    conf = (confidence or "unknown").lower().strip()
    colors = {
        "high": {
            "bg": "rgba(35, 134, 54, 0.2)",
            "border": "#238636",
            "text": "#3fb950",
            "label": "HIGH CONFIDENCE",
            "icon": "✓",
        },
        "partial": {
            "bg": "rgba(187, 128, 9, 0.2)",
            "border": "#bb8009",
            "text": "#d29922",
            "label": "PARTIAL EVIDENCE",
            "icon": "▲",
        },
        "insufficient": {
            "bg": "rgba(248, 81, 73, 0.2)",
            "border": "#da3633",
            "text": "#f85149",
            "label": "INSUFFICIENT EVIDENCE",
            "icon": "✕",
        },
    }
    style = colors.get(
        conf,
        {
            "bg": "rgba(110, 118, 129, 0.2)",
            "border": "#6e7681",
            "text": "#8b949e",
            "label": conf.upper(),
            "icon": "•",
        },
    )

    return (
        f'<span style="'
        f'display: inline-flex; align-items: center; gap: 6px; '
        f'background-color: {style["bg"]}; border: 1px solid {style["border"]}; '
        f'color: {style["text"]}; border-radius: 6px; padding: 3px 10px; '
        f'font-size: 0.78rem; font-weight: 700; letter-spacing: 0.05em; text-transform: uppercase;">'
        f'<span>{style["icon"]}</span> {style["label"]}'
        f'</span>'
    )


def generate_github_permalink(
    repo_url: str | None,
    file_path: str | None,
    start_line: int | None = 0,
    end_line: int | None = 0,
    branch: str = "main",
) -> str | None:
    """
    Safely construct a GitHub source link if the repository URL is a valid GitHub URL
    and the file path is safe and non-empty. Never constructs links from untrusted paths.
    """
    if not repo_url or not file_path:
        return None

    clean_file = file_path.strip().replace("\\", "/")
    # Reject paths with directory traversal attempts or absolute windows/posix paths
    if ".." in clean_file or clean_file.startswith(("/", "\\")):
        return None

    parsed = urlparse(repo_url)
    if parsed.netloc.lower() not in ("github.com", "www.github.com"):
        return None

    clean_repo = repo_url.rstrip("/")
    if clean_repo.endswith(".git"):
        clean_repo = clean_repo[:-4]

    s_line = start_line or 0
    e_line = end_line or 0

    if s_line > 0 and e_line > s_line:
        line_ref = f"#L{s_line}-L{e_line}"
    elif s_line > 0:
        line_ref = f"#L{s_line}"
    else:
        line_ref = ""

    return f"{clean_repo}/blob/{branch}/{clean_file}{line_ref}"


def parse_citations_in_answer(answer_text: str | None) -> list[str]:
    """Find all [E#] citation references mentioned in the markdown answer text."""
    if not answer_text:
        return []
    # Match patterns like [E1], [E2], [E12]
    matches = re.findall(r"\[E(\d+)\]", answer_text)
    return [f"E{m}" for m in matches]


def detect_unmapped_citations(
    answer_text: str | None, citations: list[dict] | None
) -> list[str]:
    """
    Check if the answer text references citations that are missing from the returned citation list.
    Returns the list of missing evidence IDs.
    """
    if not answer_text:
        return []

    mentioned_ids = set(parse_citations_in_answer(answer_text))
    if not mentioned_ids:
        return []

    available_ids = set()
    for c in citations or []:
        eid = c.get("evidence_id") or c.get("id")
        if eid:
            available_ids.add(eid)

    unmapped = [eid for eid in mentioned_ids if eid not in available_ids]
    return sorted(unmapped)
