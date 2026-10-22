from pathlib import Path


class LanguageService:
    LANGUAGE_BY_EXTENSION = {
        ".py": "python",
        ".js": "javascript",
        ".jsx": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".java": "java",
        ".go": "go",
        ".cs": "csharp",
        ".cpp": "cpp",
        ".cc": "cpp",
        ".c": "c",
        ".rs": "rust",
        ".php": "php",
        ".rb": "ruby",
        # Dependency manifests and plain-text config files.
        # These have no parseable symbols, but the CodeIndexer will
        # create a file record and CodeChunkService will use the
        # fallback chunker so their content is embedded and searchable.
        ".json": "json",
        ".txt": "text",
        ".toml": "toml",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".env": "text",
    }

    def detect(self, file_path: str) -> str | None:
        extension = Path(file_path).suffix.lower()

        return self.LANGUAGE_BY_EXTENSION.get(extension)