from pathlib import Path


class RepositorySearchService:
    def search_text(
        self,
        repository_path: str,
        query: str,
        max_results: int = 50,
    ) -> list[dict]:
        root = Path(repository_path).resolve()
        query_lower = query.lower()

        results = []

        ignored_directories = {
            ".git",
            "node_modules",
            ".venv",
            "venv",
            "__pycache__",
        }

        for file_path in root.rglob("*"):
            if not file_path.is_file():
                continue

            if any(
                part in ignored_directories
                for part in file_path.parts
            ):
                continue

            try:
                lines = file_path.read_text(
                    encoding="utf-8"
                ).splitlines()
            except (UnicodeDecodeError, OSError):
                continue

            for line_number, line in enumerate(lines, start=1):
                if query_lower in line.lower():
                    results.append(
                        {
                            "file": str(
                                file_path.relative_to(root)
                            ),
                            "line": line_number,
                            "content": line.strip(),
                        }
                    )

                    if len(results) >= max_results:
                        return results

        return results