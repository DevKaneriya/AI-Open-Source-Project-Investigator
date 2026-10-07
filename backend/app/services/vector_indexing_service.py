from qdrant_client.models import PointStruct
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.code_chunk import CodeChunk
from backend.app.services.embedding_service import (
    EmbeddingService,
)
from backend.app.services.vector_service import (
    VectorService,
)


class VectorIndexingService:
    EMBEDDING_BATCH_SIZE = 64

    def __init__(
        self,
        embedding_service: EmbeddingService,
        vector_service: VectorService,
    ):
        self.embedding_service = embedding_service
        self.vector_service = vector_service

    def index_chunks(
        self,
        db: Session,
        repository_id: int,
    ) -> dict:
        statement = (
            select(CodeChunk)
            .where(
                CodeChunk.repository_id == repository_id
            )
            .order_by(CodeChunk.id)
        )

        chunks = db.scalars(statement).all()

        if not chunks:
            return {
                "repository_id": repository_id,
                "chunks_indexed": 0,
            }

        total_indexed = 0

        for start in range(
            0,
            len(chunks),
            self.EMBEDDING_BATCH_SIZE,
        ):
            batch = chunks[
                start:
                start + self.EMBEDDING_BATCH_SIZE
            ]

            embedding_texts = [
                self._build_embedding_text(chunk)
                for chunk in batch
            ]

            embeddings = (
                self.embedding_service.embed_many(
                    embedding_texts
                )
            )

            points = []

            for chunk, embedding in zip(
                batch,
                embeddings,
            ):
                points.append(
                    PointStruct(
                        id=chunk.id,
                        vector=embedding,
                        payload={
                            "repository_id": (
                                chunk.repository_id
                            ),
                            "file_id": chunk.file_id,
                            "symbol_id": chunk.symbol_id,
                            "start_line": (
                                chunk.start_line
                            ),
                            "end_line": (
                                chunk.end_line
                            ),
                            "content": chunk.content,
                        },
                    )
                )

            self.vector_service.upsert(points)

            total_indexed += len(points)

        return {
            "repository_id": repository_id,
            "chunks_indexed": total_indexed,
        }

    @staticmethod
    def _build_embedding_text(
        chunk: CodeChunk,
    ) -> str:
        return (
            f"File ID: {chunk.file_id}\n"
            f"Symbol ID: {chunk.symbol_id}\n"
            f"Lines: "
            f"{chunk.start_line}-"
            f"{chunk.end_line}\n\n"
            f"{chunk.content}"
        )