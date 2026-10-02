from pathlib import Path
from collections import Counter


class RepositoryScanner:

    def scan(self, repository_path: str) -> dict:
        """
        Scan a cloned repository and return basic structural information.
        """

        root = Path(repository_path)

        files = [
            path
            for path in root.rglob("*")
            if path.is_file()
        ]

        directories = [
            path
            for path in root.rglob("*")
            if path.is_dir()
        ]

        extensions = Counter(
            path.suffix.lower()
            for path in files
            if path.suffix
        )

        file_names = {
            path.name.lower()
            for path in files
        }

        return {
            "repository_path": str(root),
            "total_files": len(files),
            "total_directories": len(directories),
            "file_extensions": dict(extensions),
            "has_readme": any(
                name.startswith("readme")
                for name in file_names
            ),
            "has_pyproject": "pyproject.toml" in file_names,
            "has_requirements": "requirements.txt" in file_names,
        }