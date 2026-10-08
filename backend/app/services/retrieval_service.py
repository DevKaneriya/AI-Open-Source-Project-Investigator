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
from backend.app.services.import_search_service import (
    ImportSearchService,
)
from backend.app.services.repository_search_service import (
    RepositorySearchService,
)
from backend.app.services.semantic_search_service import (
    SemanticSearchService,
)


class RetrievalService:

    def __init__(
        self,
        semantic_search_service: SemanticSearchService,
    ):
        self.code_search_service = CodeSearchService()

        self.repository_search_service = (
            RepositorySearchService()
        )

        self.code_retrieval_service = (
            CodeRetrievalService()
        )

        self.code_relationship_service = (
            CodeRelationshipService()
        )

        self.import_search_service = (
            ImportSearchService()
        )

        self.semantic_search_service = (
            semantic_search_service
        )

    def retrieve(
        self,
        db: Session,
        repository_id: int,
        query: str,
        semantic_limit: int = 10,
    ) -> dict:

        query = query.strip()

        if not query:
            raise ValueError(
                "Retrieval query cannot be empty."
            )

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

        # ---------------------------------------------------------
        # 1. Deterministic symbol search
        # ---------------------------------------------------------

        symbol_results = (
            self.code_search_service.search(
                db=db,
                repository_id=repository_id,
                query=query,
            )
        )

        # ---------------------------------------------------------
        # 2. Repository text search
        # ---------------------------------------------------------

        text_results = (
            self.repository_search_service.search_text(
                repository_path=repository.local_path,
                query=query,
            )
        )

        # ---------------------------------------------------------
        # 3. Import / dependency search
        # ---------------------------------------------------------

        import_results = (
            self.import_search_service.search(
                db=db,
                repository_id=repository_id,
                query=query,
            )
        )

        # ---------------------------------------------------------
        # 4. Semantic search
        # ---------------------------------------------------------

        semantic_results = (
            self.semantic_search_service.search(
                db=db,
                repository_id=repository_id,
                query=query,
                limit=semantic_limit,
            )
        )

        # ---------------------------------------------------------
        # 5. Exact source retrieval
        # ---------------------------------------------------------

        source_context = (
            self._build_source_context(
                repository.local_path,
                symbol_results,
            )
        )

        # ---------------------------------------------------------
        # 6. Code relationships
        # ---------------------------------------------------------

        relationships = (
            self._build_relationships(
                db=db,
                repository_id=repository_id,
                symbol_results=symbol_results,
            )
        )

        return {
            "query": query,

            "repository": {
                "id": repository.id,
                "name": repository.name,
                "url": repository.url,
            },

            "results": {
                "symbols": symbol_results,
                "text_matches": text_results,
                "imports": import_results,
                "semantic": semantic_results,
                "source_context": source_context,
                "relationships": relationships,
            },

            "summary": {
                "symbol_matches": len(
                    symbol_results
                ),
                "text_matches": len(
                    text_results
                ),
                "imports": len(
                    import_results
                ),
                "semantic_matches": len(
                    semantic_results
                ),
                "source_contexts": len(
                    source_context
                ),
                "relationships": len(
                    relationships
                ),
            },
        }

    def _build_source_context(
        self,
        repository_path: str,
        symbol_results: list[dict],
    ) -> list[dict]:

        contexts = []

        seen = set()

        for result in symbol_results[:10]:

            file_path = result["file"]
            start_line = result["start_line"]
            end_line = result["end_line"]

            key = (
                file_path,
                start_line,
                end_line,
            )

            if key in seen:
                continue

            seen.add(key)

            try:
                source = (
                    self.code_retrieval_service.get_source(
                        repository_path=repository_path,
                        file_path=file_path,
                        start_line=start_line,
                        end_line=end_line,
                    )
                )

                contexts.append(source)

            except (
                FileNotFoundError,
                OSError,
                UnicodeDecodeError,
                ValueError,
            ):
                continue

        return contexts

    def _build_relationships(
        self,
        db: Session,
        repository_id: int,
        symbol_results: list[dict],
    ) -> list[dict]:

        relationships = []

        seen_symbols = set()

        for result in symbol_results[:10]:

            symbol_name = result["name"]

            if symbol_name in seen_symbols:
                continue

            seen_symbols.add(symbol_name)

            calls = (
                self.code_relationship_service
                .get_calls(
                    db=db,
                    repository_id=repository_id,
                    symbol_name=symbol_name,
                )
            )

            relationships.extend(calls)

        return relationships