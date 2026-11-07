from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from backend.app.models.code_call import CodeCall
from backend.app.models.code_chunk import CodeChunk
from backend.app.models.code_import import CodeImport
from backend.app.models.code_symbol import CodeSymbol
from backend.app.models.repository import Repository
from backend.app.models.repository_file import RepositoryFile
from backend.app.services.github_service import GitHubService


class RepositoryService:
    # Pipeline stages in order. ``status`` stores the last stage that
    # completed; ``stage`` in list output is derived from the data actually
    # present, so repositories indexed before stages were recorded still
    # report correctly.
    PIPELINE_STAGES = ("cloned", "parsed", "chunked", "indexed")

    def __init__(self):
        self.github_service = GitHubService()

    def list_repositories(self, db: Session, vector_service=None) -> list[dict]:
        stmt = select(Repository).order_by(Repository.id.asc())
        repositories = db.scalars(stmt).all()

        file_counts = dict(
            db.execute(
                select(RepositoryFile.repository_id, func.count(RepositoryFile.id))
                .group_by(RepositoryFile.repository_id)
            ).all()
        )
        chunk_counts = dict(
            db.execute(
                select(CodeChunk.repository_id, func.count(CodeChunk.id))
                .group_by(CodeChunk.repository_id)
            ).all()
        )

        listed = []
        for repo in repositories:
            file_count = file_counts.get(repo.id, 0)
            chunk_count = chunk_counts.get(repo.id, 0)
            vector_count = self._count_vectors(vector_service, repo.id)
            listed.append(
                {
                    "id": repo.id,
                    "url": repo.url,
                    "name": repo.name,
                    "status": repo.status,
                    "local_path": repo.local_path,
                    "file_count": file_count,
                    "chunk_count": chunk_count,
                    "vector_count": vector_count,
                    "stage": self._derive_stage(
                        repo, file_count, chunk_count, vector_count
                    ),
                }
            )
        return listed

    @staticmethod
    def _count_vectors(vector_service, repository_id: int) -> int | None:
        if vector_service is None:
            return None
        try:
            return vector_service.count_repository(repository_id)
        except Exception:
            return None

    @staticmethod
    def _derive_stage(
        repo: Repository,
        file_count: int,
        chunk_count: int,
        vector_count: int | None,
    ) -> str:
        if repo.status in ("cloning", "failed"):
            return repo.status

        # Vector count unknown (Qdrant unavailable) is treated as present.
        has_vectors = vector_count is None or vector_count > 0
        reached = {
            "indexed": bool(chunk_count) and has_vectors,
            "chunked": bool(chunk_count),
            "parsed": bool(file_count),
            "cloned": bool(repo.local_path),
        }

        # A recorded post-clone stage is authoritative (re-chunking leaves
        # stale vectors that would otherwise look indexed) but is downgraded
        # when its data is gone.
        if repo.status in RepositoryService.PIPELINE_STAGES[1:]:
            start = RepositoryService.PIPELINE_STAGES.index(repo.status)
            for stage in reversed(RepositoryService.PIPELINE_STAGES[: start + 1]):
                if reached[stage]:
                    return stage
            return repo.status

        # Legacy rows were never advanced past "cloned"; infer from the data.
        if vector_count and chunk_count:
            return "indexed"
        for stage in ("chunked", "parsed", "cloned"):
            if reached[stage]:
                return stage
        return repo.status

    def set_status(self, db: Session, repository_id: int, status: str) -> None:
        repository = db.get(Repository, repository_id)
        if repository is None:
            return
        repository.status = status
        db.commit()

    def delete_repository(self, db: Session, repository_id: int) -> bool:
        repo = db.get(Repository, repository_id)
        if not repo:
            return False

        file_ids = db.scalars(
            select(RepositoryFile.id).where(RepositoryFile.repository_id == repository_id)
        ).all()

        try:
            db.execute(delete(CodeChunk).where(CodeChunk.repository_id == repository_id))

            if file_ids:
                symbol_ids = select(CodeSymbol.id).where(CodeSymbol.file_id.in_(file_ids))
                db.execute(delete(CodeCall).where(CodeCall.symbol_id.in_(symbol_ids)))
                db.execute(delete(CodeSymbol).where(CodeSymbol.file_id.in_(file_ids)))
                db.execute(delete(CodeImport).where(CodeImport.file_id.in_(file_ids)))

            db.execute(delete(RepositoryFile).where(RepositoryFile.repository_id == repository_id))

            db.delete(repo)
            db.commit()
        except Exception:
            db.rollback()
            raise
        return True

    def delete_repository_everywhere(
        self,
        db: Session,
        repository_id: int,
        vector_service,
    ) -> str:
        """Delete a repository from Qdrant and PostgreSQL.

        Returns ``"not_found"``, ``"vector_delete_failed"`` or ``"deleted"``.

        Order matters because the two stores cannot share a transaction:
        vectors are deleted first (filtered by this repository's id only).
        If that fails, PostgreSQL is left untouched so the request can simply
        be retried. If PostgreSQL then fails, it is rolled back; a retry
        re-runs the idempotent vector delete and the row delete.
        """
        if db.get(Repository, repository_id) is None:
            return "not_found"

        try:
            vector_service.delete_repository(repository_id)
        except Exception:
            return "vector_delete_failed"

        self.delete_repository(db, repository_id)
        return "deleted"

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
