from pathlib import Path


class CodeRetrievalService:
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

        source_lines = lines[start_index:end_index]

        return {
            "file": file_path,
            "start_line": start_line,
            "end_line": end_line,
            "source": "\n".join(source_lines),
        }