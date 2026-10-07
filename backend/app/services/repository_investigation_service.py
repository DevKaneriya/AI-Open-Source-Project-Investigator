from sqlalchemy.orm import Session

from backend.app.models.repository import Repository
from backend.app.services.code_relationship_service import (
    CodeRelationshipService,
)
from backend.app.services.code_retrieval_service import (
    CodeRetrievalService,
)
from backend.app.services.code_search_service import (
    CodeSearchService,
)
from backend.app.services.repository_search_service import (
    RepositorySearchService,
)
from backend.app.services.import_search_service import (
    ImportSearchService,
)


class RepositoryInvestigationService:
    def __init__(self):
        self.code_search_service = CodeSearchService()
        self.repository_search_service = RepositorySearchService()
        self.code_retrieval_service = CodeRetrievalService()
        self.code_relationship_service = CodeRelationshipService()
        self.import_search_service = ImportSearchService()

    def investigate(
        self,
        db: Session,
        repository_id: int,
        query: str,
    ) -> dict:
        repository = db.get(
            Repository,
            repository_id,
        )

        if not repository:
            raise ValueError(
                "Repository not found."
            )

        if not repository.local_path:
            raise ValueError(
                "Repository has not been cloned."
            )

        if not query.strip():
            raise ValueError(
                "Investigation query cannot be empty."
            )

        query = query.strip()

        symbol_results = (
            self.code_search_service.search(
                db=db,
                repository_id=repository_id,
                query=query,
            )
        )

        text_results = (
            self.repository_search_service.search_text(
                repository_path=repository.local_path,
                query=query,
            )
        )

        import_results = (
            self.import_search_service.search(
                db=db,
                repository_id=repository_id,
                query=query,
            )
        )

        source_context = self._build_source_context(
            repository_path=repository.local_path,
            symbol_results=symbol_results,
        )

        relationships = self._build_relationships(
            db=db,
            repository_id=repository_id,
            symbol_results=symbol_results,
        )

        return {
            "repository": {
                "id": repository.id,
                "name": repository.name,
                "url": repository.url,
            },
            "query": query,
            "results": {
                "symbols": symbol_results,
                "text_matches": text_results,
                "imports": import_results,
                "source_context": source_context,
                "relationships": relationships,
            },
            "summary": {
                "symbol_matches": len(symbol_results),
                "text_matches": len(text_results),
                "imports": len(import_results),
                "source_contexts": len(source_context),
                "relationships": len(relationships),
            },
        }

    def _build_source_context(
        self,
        repository_path: str,
        symbol_results: list[dict],
        max_results: int = 10,
    ) -> list[dict]:
        source_context = []

        for symbol in symbol_results[:max_results]:
            try:
                source = (
                    self.code_retrieval_service.get_source(
                        repository_path=repository_path,
                        file_path=symbol["file"],
                        start_line=symbol["start_line"],
                        end_line=symbol["end_line"],
                    )
                )

                source_context.append(
                    {
                        "type": symbol["type"],
                        "name": symbol["name"],
                        "qualified_name": symbol[
                            "qualified_name"
                        ],
                        "file": source["file"],
                        "start_line": source[
                            "start_line"
                        ],
                        "end_line": source[
                            "end_line"
                        ],
                        "source": source["source"],
                    }
                )

            except (
                FileNotFoundError,
                OSError,
                UnicodeDecodeError,
                ValueError,
            ):
                continue

        return source_context

    def _build_relationships(
        self,
        db: Session,
        repository_id: int,
        symbol_results: list[dict],
        max_results: int = 10,
    ) -> list[dict]:
        relationships = []

        processed_symbols = set()

        for symbol in symbol_results[:max_results]:
            symbol_name = symbol["name"]

            if symbol_name in processed_symbols:
                continue

            processed_symbols.add(symbol_name)

            calls = (
                self.code_relationship_service.get_calls(
                    db=db,
                    repository_id=repository_id,
                    symbol_name=symbol_name,
                )
            )

            relationships.extend(calls)

        return relationships