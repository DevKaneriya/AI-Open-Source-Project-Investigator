
from pathlib import Path

EXCLUDED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "__pycache__",
    ".angular",
    ".next",
    ".nuxt",
    "dist",
    "build",
    "coverage",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "site-packages",
}

EXCLUDED_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    "database_backup.json",
}

EXCLUDED_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".map",
    ".min.js",
    ".min.css",
    ".log",
    ".sqlite",
    ".db",
    ".dump",
    ".bak",
    ".zip",
    ".gz",
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
    ".ico",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
}

EXCLUDED_FILENAMES_CONTAINS = {
    "database_backup",
    "backup_",
}

MAX_SOURCE_FILE_BYTES = 1_000_000


def should_include_file(root: str | Path, file_path: str | Path) -> bool:
    root = Path(root).resolve()
    path = Path(file_path).resolve()

    # Never allow a path outside the repository root.
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False

    parts = {part.casefold() for part in relative.parts}
    name = path.name.casefold()
    suffixes = path.suffixes
    compound_suffix = "".join(suffixes[-2:]).casefold()

    if parts & EXCLUDED_DIRS:
        return False

    if name in EXCLUDED_NAMES:
        return False

    if any(token in name for token in EXCLUDED_FILENAMES_CONTAINS):
        return False

    if path.suffix.casefold() in EXCLUDED_SUFFIXES:
        return False

    if compound_suffix in EXCLUDED_SUFFIXES:
        return False

    try:
        if not path.is_file():
            return False
        if path.stat().st_size > MAX_SOURCE_FILE_BYTES:
            return False
    except OSError:
        return False

    return True
