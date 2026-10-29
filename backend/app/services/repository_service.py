from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.app.models.code_chunk import CodeChunk
from backend.app.models.code_import import CodeImport
from backend.app.models.code_symbol import CodeSymbol
from backend.app.models.repository import Repository
from backend.app.models.repository_file import RepositoryFile
from backend.app.services.github_service import GitHubService


class RepositoryService:
    def __init__(self):
        self.github_service = GitHubService()

    def list_repositories(self, db: Session) -> list[dict]:
        stmt = select(Repository).order_by(Repository.id.asc())
        repositories = db.scalars(stmt).all()
        return [
            {
                "id": repo.id,
                "url": repo.url,
                "name": repo.name,
                "status": repo.status,
                "local_path": repo.local_path,
            }
            for repo in repositories
        ]

    def delete_repository(self, db: Session, repository_id: int) -> bool:
        repo = db.get(Repository, repository_id)
        if not repo:
            return False

        file_ids = db.scalars(
            select(RepositoryFile.id).where(RepositoryFile.repository_id == repository_id)
        ).all()

        if file_ids:
            db.execute(delete(CodeSymbol).where(CodeSymbol.file_id.in_(file_ids)))
            db.execute(delete(CodeImport).where(CodeImport.file_id.in_(file_ids)))

        db.execute(delete(CodeChunk).where(CodeChunk.repository_id == repository_id))
        db.execute(delete(RepositoryFile).where(RepositoryFile.repository_id == repository_id))

        db.delete(repo)
        db.commit()
        return True

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
