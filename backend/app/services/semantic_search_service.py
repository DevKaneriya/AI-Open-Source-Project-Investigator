from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.code_symbol import CodeSymbol
from backend.app.models.repository_file import RepositoryFile
from backend.app.services.embedding_service import (
    EmbeddingService,
)
from backend.app.services.vector_service import (
    VectorService,
)


class SemanticSearchService:

    def __init__(
        self,
        embedding_service: EmbeddingService,
        vector_service: VectorService,
    ):
        self.embedding_service = embedding_service
        self.vector_service = vector_service

    def search(
        self,
        db: Session,
        repository_id: int,
        query: str,
        limit: int = 10,
    ) -> list[dict]:

        query = query.strip()

        if not query:
            return []

        # ---------------------------------------------------------
        # 1. Convert user query into an embedding
        # ---------------------------------------------------------

        query_vector = self.embedding_service.embed(
            query
        )

        # ---------------------------------------------------------
        # 2. Search Qdrant
        # ---------------------------------------------------------

        points = self.vector_service.search(
            vector=query_vector,
            repository_id=repository_id,
            limit=limit,
        )

        # Defence in depth: the Qdrant filter already scopes by
        # repository_id, but never trust a payload from another repository.
        points = [
            point
            for point in points or []
            if point.payload
            and point.payload.get("repository_id") == repository_id
        ]

        if not points:
            return []

        # ---------------------------------------------------------
        # 3. Collect database IDs
        # ---------------------------------------------------------

        file_ids = {
            point.payload.get("file_id")
            for point in points
            if point.payload
            and point.payload.get("file_id") is not None
        }

        symbol_ids = {
            point.payload.get("symbol_id")
            for point in points
            if point.payload
            and point.payload.get("symbol_id") is not None
        }

        # ---------------------------------------------------------
        # 4. Load file metadata
        # ---------------------------------------------------------

        files = db.scalars(
            select(RepositoryFile).where(
                RepositoryFile.id.in_(file_ids),
                RepositoryFile.repository_id == repository_id,
            )
        ).all()

        file_map = {
            file.id: file
            for file in files
        }

        # ---------------------------------------------------------
        # 5. Load symbol metadata
        # ---------------------------------------------------------

        symbols = db.scalars(
            select(CodeSymbol).where(
                CodeSymbol.id.in_(symbol_ids)
            )
        ).all()

        symbol_map = {
            symbol.id: symbol
            for symbol in symbols
        }

        # ---------------------------------------------------------
        # 6. Build search results
        # ---------------------------------------------------------

        results = []

        for point in points:

            payload = point.payload or {}

            file_id = payload.get("file_id")
            symbol_id = payload.get("symbol_id")

            file = file_map.get(file_id)
            symbol = symbol_map.get(symbol_id)

            # A chunk whose file is not part of this repository is a stale
            # or foreign vector; drop it rather than return a wrong path.
            if file is None:
                continue

            if symbol is not None and symbol.file_id != file.id:
                symbol = None

            results.append(
                {
                    "score": point.score,
                    "chunk_id": point.id,
                    "file": (
                        file.path
                        if file
                        else None
                    ),
                    "language": (
                        file.language
                        if file
                        else None
                    ),
                    "symbol": (
                        symbol.name
                        if symbol
                        else None
                    ),
                    "symbol_type": (
                        symbol.symbol_type
                        if symbol
                        else None
                    ),
                    "start_line": payload.get(
                        "start_line"
                    ),
                    "end_line": payload.get(
                        "end_line"
                    ),
                    "content": payload.get(
                        "content"
                    ),
                }
            )

        return results