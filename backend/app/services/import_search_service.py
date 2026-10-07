from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.code_import import CodeImport
from backend.app.models.repository_file import RepositoryFile


class ImportSearchService:
    def search(
        self,
        db: Session,
        repository_id: int,
        query: str,
    ) -> list[dict]:
        search_term = f"%{query.lower()}%"

        statement = (
            select(CodeImport, RepositoryFile)
            .join(
                RepositoryFile,
                CodeImport.file_id == RepositoryFile.id,
            )
            .where(
                RepositoryFile.repository_id == repository_id,
                CodeImport.imported_module.ilike(
                    search_term
                ),
            )
        )

        results = db.execute(statement).all()

        return [
            {
                "import": code_import.imported_module,
                "file": repository_file.path,
                "language": repository_file.language,
            }
            for code_import, repository_file in results
        ]