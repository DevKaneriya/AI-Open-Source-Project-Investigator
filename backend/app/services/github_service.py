from pathlib import Path
from tempfile import mkdtemp

from git import Repo


class GitHubService:

    def clone_repository(self, repository_url: str) -> str:
        """
        Clone a public GitHub repository into a temporary directory.

        Returns:
            str: Path to the cloned repository.
        """

        workspace = Path(mkdtemp(prefix="repo_"))
        repository_path = workspace / "repository"

        Repo.clone_from(
            repository_url,
            repository_path,
        )

        return str(repository_path)