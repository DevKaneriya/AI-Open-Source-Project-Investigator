from concurrent.futures import ThreadPoolExecutor, as_completed

from sqlalchemy.orm import Session

from backend.app.core.database import SessionLocal
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

from backend.app.services.query_analyzer_service import (
    QueryAnalyzerService,
)

from backend.app.services.evidence_collector_service import (
    EvidenceCollectorService,
)


class RetrievalService:

    MAX_SYMBOL_QUERIES = 5
    MAX_TEXT_QUERIES = 5
    MAX_IMPORT_QUERIES = 3

    def __init__(
        self,
        semantic_search_service: SemanticSearchService,
        query_analyzer_service: QueryAnalyzerService,
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

        self.query_analyzer_service = (
            query_analyzer_service
        )

        self.evidence_collector_service = (
            EvidenceCollectorService()
        )

    # =============================================================
    # MAIN RETRIEVAL PIPELINE
    # =============================================================

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
        # 1. Analyze user query
        # ---------------------------------------------------------

        query_analysis = (
            self.query_analyzer_service.analyze(query)
        )

        symbol_queries = self._prepare_queries(
            query_analysis.symbol_queries,
            self.MAX_SYMBOL_QUERIES,
        )

        text_queries = self._prepare_queries(
            query_analysis.text_queries,
            self.MAX_TEXT_QUERIES,
        )

        import_queries = self._prepare_queries(
            query_analysis.import_queries,
            self.MAX_IMPORT_QUERIES,
        )

        # ---------------------------------------------------------
        # 2. Run deterministic searches independently
        # ---------------------------------------------------------

        symbol_results = []
        text_results = []
        import_results = []

        with ThreadPoolExecutor(
            max_workers=3
        ) as executor:

            futures = {}

            if symbol_queries:

                futures[
                    executor.submit(
                        self._search_symbols,
                        repository_id,
                        symbol_queries,
                    )
                ] = "symbols"

            if text_queries:

                futures[
                    executor.submit(
                        self._search_text,
                        repository.local_path,
                        text_queries,
                    )
                ] = "text"

            if import_queries:

                futures[
                    executor.submit(
                        self._search_imports,
                        repository_id,
                        import_queries,
                    )
                ] = "imports"

            for future in as_completed(futures):

                result_type = futures[future]

                result = future.result()

                if result_type == "symbols":
                    symbol_results = result

                elif result_type == "text":
                    text_results = result

                elif result_type == "imports":
                    import_results = result

        # ---------------------------------------------------------
        # 3. Semantic search
        #
        # IMPORTANT:
        # Use the original user query unchanged.
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
        # 4. Exact source retrieval
        # ---------------------------------------------------------

        source_context = (
            self._build_source_context(
                repository.local_path,
                symbol_results,
            )
        )

        # ---------------------------------------------------------
        # 5. Code relationships
        #
        # TEMPORARILY DISABLED.
        #
        # Current Tree-sitter call extraction is producing
        # malformed values such as:
        #
        #     o.ject({
        #     s.ring()
        #     i.o.d.tetime()
        #
        # We must not expose corrupted relationships to the
        # investigation/evidence pipeline.
        # ---------------------------------------------------------

        relationships = []

        # ---------------------------------------------------------
        # 6. Build raw retrieval result
        # ---------------------------------------------------------

        retrieval_result = {

            "query": query,

            "repository": {
                "id": repository.id,
                "name": repository.name,
                "url": repository.url,
            },

            "query_analysis": {
                "intent": query_analysis.intent,
                "concepts": query_analysis.concepts,
                "symbol_queries": symbol_queries,
                "text_queries": text_queries,
                "import_queries": import_queries,
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

        # ---------------------------------------------------------
        # 7. Collect and rank evidence
        # ---------------------------------------------------------

        evidence = (
            self.evidence_collector_service.collect(
                retrieval_results=retrieval_result,
            )
        )

        # ---------------------------------------------------------
        # 8. Add curated evidence to response
        # ---------------------------------------------------------

        retrieval_result["evidence"] = evidence

        return retrieval_result

    # =============================================================
    # QUERY PREPARATION
    # =============================================================

    @staticmethod
    def _prepare_queries(
        queries: list[str],
        maximum: int,
    ) -> list[str]:

        prepared = []

        seen = set()

        for query in queries:

            query = query.strip()

            if not query:
                continue

            normalized = query.lower()

            if normalized in seen:
                continue

            seen.add(normalized)

            prepared.append(query)

            if len(prepared) >= maximum:
                break

        return prepared

    # =============================================================
    # PARALLEL SYMBOL SEARCH
    # =============================================================

    @staticmethod
    def _search_symbols(
        repository_id: int,
        queries: list[str],
    ) -> list[dict]:

        db = SessionLocal()

        try:

            results = []

            seen = set()

            service = CodeSearchService()

            for query in queries:

                matches = service.search(
                    db=db,
                    repository_id=repository_id,
                    query=query,
                )

                for result in matches:

                    key = (
                        result["file"],
                        result["name"],
                        result["start_line"],
                        result["end_line"],
                    )

                    if key in seen:
                        continue

                    seen.add(key)

                    # Preserve which analyzer-generated query
                    # produced this result.
                    results.append(
                        {
                            **result,
                            "search_query": query,
                        }
                    )

            return results

        finally:

            db.close()

    # =============================================================
    # PARALLEL TEXT SEARCH
    # =============================================================

    @staticmethod
    def _search_text(
        repository_path: str,
        queries: list[str],
    ) -> list[dict]:

        service = RepositorySearchService()

        results = []

        seen = set()

        for query in queries:

            matches = service.search_text(
                repository_path=repository_path,
                query=query,
            )

            for result in matches:

                key = (
                    result.get("file"),
                    result.get("line"),
                    result.get("text"),
                )

                if key in seen:
                    continue

                seen.add(key)

                # Preserve which analyzer-generated query
                # produced this result.
                results.append(
                    {
                        **result,
                        "search_query": query,
                    }
                )

        return results

    # =============================================================
    # PARALLEL IMPORT SEARCH
    # =============================================================

    @staticmethod
    def _search_imports(
        repository_id: int,
        queries: list[str],
    ) -> list[dict]:

        db = SessionLocal()

        try:

            results = []

            seen = set()

            service = ImportSearchService()

            for query in queries:

                matches = service.search(
                    db=db,
                    repository_id=repository_id,
                    query=query,
                )

                for result in matches:

                    key = (
                        result["file"],
                        result["import"],
                    )

                    if key in seen:
                        continue

                    seen.add(key)

                    # Preserve which analyzer-generated query
                    # produced this result.
                    results.append(
                        {
                            **result,
                            "search_query": query,
                        }
                    )

            return results

        finally:

            db.close()

    # =============================================================
    # SOURCE RETRIEVAL
    # =============================================================

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

                contexts.append(
                    {
                        **source,
                        "symbol": result.get("name"),
                        "symbol_type": result.get("type"),
                        "qualified_name": result.get(
                            "qualified_name"
                        ),
                        "search_query": result.get(
                            "search_query"
                        ),
                    }
                )

            except (
                FileNotFoundError,
                OSError,
                UnicodeDecodeError,
                ValueError,
            ):
                continue

        return contexts

    # =============================================================
    # CODE RELATIONSHIPS
    #
    # KEPT FOR FUTURE USE.
    #
    # Do not call this until Tree-sitter extraction has been fixed.
    # =============================================================

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