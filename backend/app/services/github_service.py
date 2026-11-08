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

        # Only the latest files are analysed, so skip the history.
        Repo.clone_from(
            repository_url,
            repository_path,
            depth=1,
        )

        return str(repository_path)