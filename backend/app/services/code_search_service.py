from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from backend.app.models.code_symbol import CodeSymbol
from backend.app.models.repository_file import RepositoryFile
from backend.app.services.search_normalizer import SearchNormalizer


class CodeSearchService:

    def search(
        self,
        db: Session,
        repository_id: int,
        query: str,
    ) -> list[dict]:

        normalized_query = SearchNormalizer.normalize(query)

        if not normalized_query:
            return []

        search_terms = [
            query.strip(),
            normalized_query.replace(" ", "_"),
            normalized_query.replace(" ", ""),
        ]

        conditions = []

        for term in search_terms:
            search_term = f"%{term}%"

            conditions.extend(
                [
                    CodeSymbol.name.ilike(search_term),
                    CodeSymbol.qualified_name.ilike(search_term),
                ]
            )

        statement = (
            select(CodeSymbol, RepositoryFile)
            .join(
                RepositoryFile,
                CodeSymbol.file_id == RepositoryFile.id,
            )
            .where(
                RepositoryFile.repository_id == repository_id,
                or_(*conditions),
            )
        )

        results = db.execute(statement).all()

        return [
            {
                "id": symbol.id,
                "name": symbol.name,
                "symbol_type": symbol.symbol_type,
                "qualified_name": symbol.qualified_name,
                "file": repository_file.path,
                "language": repository_file.language,
                "start_line": symbol.start_line,
                "end_line": symbol.end_line,
                "signature": symbol.signature,
                "search_query": query,
            }
            for symbol, repository_file in results
        ]