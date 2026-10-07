from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.repository import Repository
from backend.app.services.github_service import GitHubService


class RepositoryService:
    def __init__(self):
        self.github_service = GitHubService()

    def add_repository(self, db: Session, repository_url: str) -> dict:
        repository_url = repository_url.rstrip("/")

        existing_repository = db.scalar(
            select(Repository).where(Repository.url == repository_url)
        )

        if existing_repository:
            return {
                "id": existing_repository.id,
                "url": existing_repository.url,
                "name": existing_repository.name,
                "status": existing_repository.status,
                "local_path": existing_repository.local_path,
            }

        repository_name = self._get_repository_name(repository_url)

        repository = Repository(
            url=repository_url,
            name=repository_name,
            status="cloning",
        )

        db.add(repository)
        db.commit()
        db.refresh(repository)

        try:
            repository_path = self.github_service.clone_repository(
                repository_url
            )

            repository.local_path = repository_path
            repository.status = "cloned"

            db.commit()
            db.refresh(repository)

        except Exception:
            repository.status = "failed"
            db.commit()
            raise

        return {
            "id": repository.id,
            "url": repository.url,
            "name": repository.name,
            "status": repository.status,
            "local_path": repository.local_path,
        }

    @staticmethod
    def _get_repository_name(repository_url: str) -> str:
        parsed_url = urlparse(repository_url)
        path = Path(parsed_url.path)

        name = path.name

        if name.endswith(".git"):
            name = name[:-4]

        if not name:
            raise ValueError("Invalid GitHub repository URL.")

        return name