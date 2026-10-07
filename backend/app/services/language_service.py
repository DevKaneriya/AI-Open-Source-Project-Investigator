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
    }

    def detect(self, file_path: str) -> str | None:
        extension = Path(file_path).suffix.lower()

        return self.LANGUAGE_BY_EXTENSION.get(extension)