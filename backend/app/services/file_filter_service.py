import re
from pathlib import Path

# Pattern for build artifact filenames (e.g., main-HIOYHFQA.js, chunk.3a4f5b.js)
BUILD_ARTIFACT_PATTERN = re.compile(
    r'[\w-]+[-\.][A-Z0-9]{6,}\.js$|'  # main-HIOYHFQA.js or chunk-ABC123.js
    r'[\w-]+\.[a-f0-9]{8,}\.js$|'     # chunk.3a4f5b6c.js
    r'polyfills.*\.js$|'               # polyfills-*.js
    r'runtime.*\.js$|'                 # runtime-*.js
    r'vendor.*\.js$',                  # vendor-*.js
    re.IGNORECASE
)

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
    "public",  # Exclude frontend build output directories
    ".output",  # Nuxt output
    "out",  # Next.js output
}

EXCLUDED_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    "database_backup.json",
    # Note: .env.example is NOT excluded - it contains configuration documentation
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

    # Exclude build artifact files with hash patterns
    if BUILD_ARTIFACT_PATTERN.search(name):
        return False

    try:
        if not path.is_file():
            return False
        if path.stat().st_size > MAX_SOURCE_FILE_BYTES:
            return False
    except OSError:
        return False

    return True
