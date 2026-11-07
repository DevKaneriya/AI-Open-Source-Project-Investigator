"""
PostgreSQL + pgvector implementation of the vector store.

Drop-in replacement for the Qdrant-backed ``VectorService`` (same method
names and result shape) for hosting environments where the local disk is
wiped on restart: vectors live in the same database as the code index, so
nothing has to be rebuilt after a restart.
"""

import json
from types import SimpleNamespace

from sqlalchemy import text
from sqlalchemy.engine import Engine


class PgVectorService:
    TABLE_NAME = "chunk_embeddings"
    VECTOR_SIZE = 384

    def __init__(self, engine: Engine | None = None):
        if engine is None:
            from backend.app.core.database import engine as default_engine

            engine = default_engine

        self.engine = engine
        self._ensure_schema()

    def _ensure_schema(self):
        # Created here rather than in an Alembic migration because the
        # extension only exists where this backend is enabled; local
        # development on Qdrant must not require pgvector.
        with self.engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            conn.execute(
                text(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self.TABLE_NAME} (
                        id BIGINT PRIMARY KEY
                            REFERENCES code_chunks (id) ON DELETE CASCADE,
                        repository_id INTEGER NOT NULL,
                        embedding vector({self.VECTOR_SIZE}) NOT NULL,
                        payload JSONB NOT NULL DEFAULT '{{}}'::jsonb
                    )
                    """
                )
            )
            # Searches are always scoped to one repository and an exact scan
            # over its rows is fast at this scale. An ANN index would
            # post-filter by repository and could return too few results.
            conn.execute(
                text(
                    f"CREATE INDEX IF NOT EXISTS ix_{self.TABLE_NAME}_repository_id "
                    f"ON {self.TABLE_NAME} (repository_id)"
                )
            )

    @staticmethod
    def _vector_literal(vector) -> str:
        return "[" + ",".join(repr(float(value)) for value in vector) + "]"

    def upsert(self, points):
        if not points:
            return

        rows = [
            {
                "id": point.id,
                "repository_id": point.payload["repository_id"],
                "embedding": self._vector_literal(point.vector),
                "payload": json.dumps(point.payload),
            }
            for point in points
        ]

        with self.engine.begin() as conn:
            conn.execute(
                text(
                    f"""
                    INSERT INTO {self.TABLE_NAME} (id, repository_id, embedding, payload)
                    VALUES (:id, :repository_id, CAST(:embedding AS vector), CAST(:payload AS jsonb))
                    ON CONFLICT (id) DO UPDATE SET
                        repository_id = EXCLUDED.repository_id,
                        embedding = EXCLUDED.embedding,
                        payload = EXCLUDED.payload
                    """
                ),
                rows,
            )

    def delete_repository(self, repository_id: int):
        with self.engine.begin() as conn:
            conn.execute(
                text(f"DELETE FROM {self.TABLE_NAME} WHERE repository_id = :repository_id"),
                {"repository_id": repository_id},
            )

    def count_repository(self, repository_id: int) -> int:
        with self.engine.connect() as conn:
            return conn.execute(
                text(f"SELECT count(*) FROM {self.TABLE_NAME} WHERE repository_id = :repository_id"),
                {"repository_id": repository_id},
            ).scalar_one()

    def search(
        self,
        vector: list[float],
        repository_id: int,
        limit: int = 10,
    ):
        # Cosine similarity (1 - cosine distance) to match Qdrant's scores.
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    f"""
                    SELECT id, payload, 1 - (embedding <=> CAST(:query AS vector)) AS score
                    FROM {self.TABLE_NAME}
                    WHERE repository_id = :repository_id
                    ORDER BY embedding <=> CAST(:query AS vector)
                    LIMIT :limit
                    """
                ),
                {
                    "query": self._vector_literal(vector),
                    "repository_id": repository_id,
                    "limit": limit,
                },
            ).all()

        return [
            SimpleNamespace(id=row.id, score=float(row.score), payload=row.payload)
            for row in rows
        ]
