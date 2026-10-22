from pathlib import Path


class CodeRetrievalService:
    # Minimum lines to return; short symbols are padded with surrounding context.
    MIN_CONTEXT_LINES = 5
    # Extra lines to add above/below when a symbol is shorter than MIN_CONTEXT_LINES.
    CONTEXT_PADDING = 3

    def get_source(
        self,
        repository_path: str,
        file_path: str,
        start_line: int,
        end_line: int,
    ) -> dict:
        root = Path(repository_path).resolve()
        target_file = (root / file_path).resolve()

        if not target_file.is_relative_to(root):
            raise ValueError("Invalid file path.")

        if not target_file.is_file():
            raise FileNotFoundError("Source file not found.")

        lines = target_file.read_text(
            encoding="utf-8"
        ).splitlines()

        start_index = max(start_line - 1, 0)
        end_index = min(end_line, len(lines))

        # Expand range for very short snippets so the caller gets enough
        # context to understand the code (decorators, conditions, etc.)
        if (end_index - start_index) < self.MIN_CONTEXT_LINES:
            start_index = max(0, start_index - self.CONTEXT_PADDING)
            end_index = min(len(lines), end_index + self.CONTEXT_PADDING)

        source_lines = lines[start_index:end_index]

        return {
            "file": file_path,
            "start_line": start_index + 1,
            "end_line": end_index,
            "source": "\n".join(source_lines),
        }

    def get_source_range(
        self,
        repository_path: str,
        file_path: str,
        start_line: int,
        end_line: int,
    ) -> dict:
        """Exact range retrieval without context padding, used for citation validation."""
        root = Path(repository_path).resolve()
        target_file = (root / file_path).resolve()

        if not target_file.is_relative_to(root):
            raise ValueError("Invalid file path.")

        if not target_file.is_file():
            raise FileNotFoundError("Source file not found.")

        lines = target_file.read_text(
            encoding="utf-8"
        ).splitlines()

        start_index = max(start_line - 1, 0)
        end_index = min(end_line, len(lines))
        source_lines = lines[start_index:end_index]

        return {
            "file": file_path,
            "start_line": start_line,
            "end_line": end_line,
            "source": "\n".join(source_lines),
            "total_lines": len(lines),
        }

    def file_exists(self, repository_path: str, file_path: str) -> bool:
        """Check whether a file exists in the repository without reading it."""
        try:
            root = Path(repository_path).resolve()
            target = (root / file_path).resolve()
            return target.is_relative_to(root) and target.is_file()
        except Exception:
            return False

    def get_file_line_count(self, repository_path: str, file_path: str) -> int | None:
        """Return the total number of lines in a file, or None if unreadable."""
        try:
            root = Path(repository_path).resolve()
            target = (root / file_path).resolve()
            if not target.is_relative_to(root) or not target.is_file():
                return None
            return len(target.read_text(encoding="utf-8").splitlines())
        except Exception:
            return None