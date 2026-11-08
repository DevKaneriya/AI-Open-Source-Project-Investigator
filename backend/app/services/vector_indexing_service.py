"""
Embeds a repository's code chunks and stores the vectors.

Indexing is resumable: chunks that already have a vector are skipped, so an
interrupted run continues where it stopped instead of starting over. With a
time budget, one call embeds batches until the budget is used and reports how
many chunks remain; callers repeat the call until ``done`` is true. This keeps
every HTTP request short on slow hosts (a large repository on a shared CPU can
take many minutes to embed in total).
"""

import logging
import threading
import time

from qdrant_client.models import PointStruct
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.code_chunk import CodeChunk
from backend.app.services.embedding_service import EmbeddingService
from backend.app.services.vector_service import VectorService

logger = logging.getLogger(__name__)

_locks_guard = threading.Lock()
_locks: dict[int, threading.Lock] = {}


def _lock_for(repository_id: int) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(repository_id, threading.Lock())


class VectorIndexingService:
    # Small batches so a time budget is checked often; throughput on CPU is
    # about the same as with larger batches.
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
        time_budget_seconds: float | None = None,
    ) -> dict:
        """Embed chunks that have no vector yet.

        With ``time_budget_seconds`` the call stops after the batch that uses
        up the budget; without it, it runs until every chunk is embedded.
        Returns progress counts, including ``remaining`` and ``done``.
        """
        # One run per repository at a time: a retry that arrives while an
        # earlier request is still embedding waits, then skips its work.
        with _lock_for(repository_id):
            return self._index(db, repository_id, time_budget_seconds)

    def _index(self, db: Session, repository_id: int, time_budget_seconds: float | None) -> dict:
        started = time.perf_counter()

        chunks = db.scalars(
            select(CodeChunk)
            .where(CodeChunk.repository_id == repository_id)
            .order_by(CodeChunk.id)
        ).all()
        chunk_ids = {chunk.id for chunk in chunks}

        existing = self.vector_service.existing_ids(repository_id)
        # Vectors of chunks that no longer exist (the repository was re-chunked).
        stale = existing - chunk_ids
        if stale:
            self.vector_service.delete_ids(stale)
            existing -= stale

        pending = [chunk for chunk in chunks if chunk.id not in existing]
        indexed_now = 0

        for start in range(0, len(pending), self.EMBEDDING_BATCH_SIZE):
            if time_budget_seconds is not None and indexed_now and (
                time.perf_counter() - started >= time_budget_seconds
            ):
                break
            batch = pending[start:start + self.EMBEDDING_BATCH_SIZE]
            embeddings = self.embedding_service.embed_many(
                [self._build_embedding_text(chunk) for chunk in batch]
            )
            self.vector_service.upsert(
                [
                    PointStruct(
                        id=chunk.id,
                        vector=embedding,
                        payload={
                            "repository_id": chunk.repository_id,
                            "file_id": chunk.file_id,
                            "symbol_id": chunk.symbol_id,
                            "start_line": chunk.start_line,
                            "end_line": chunk.end_line,
                            "content": chunk.content,
                        },
                    )
                    for chunk, embedding in zip(batch, embeddings)
                ]
            )
            indexed_now += len(batch)

        vectors_total = len(existing) + indexed_now
        remaining = len(chunks) - vectors_total
        elapsed = time.perf_counter() - started
        logger.info(
            "Repository %s: embedded %d chunks in %.1fs (%d/%d done, %d remaining)",
            repository_id, indexed_now, elapsed, vectors_total, len(chunks), remaining,
        )
        return {
            "repository_id": repository_id,
            "chunks_indexed": indexed_now,
            "vectors_total": vectors_total,
            "chunks_total": len(chunks),
            "remaining": remaining,
            "done": remaining == 0,
            "total_time": round(elapsed, 2),
        }

    @staticmethod
    def _build_embedding_text(chunk):
        return (
            f"File ID: {chunk.file_id}\n"
            f"Symbol ID: {chunk.symbol_id}\n"
            f"Lines: {chunk.start_line}-{chunk.end_line}\n\n"
            f"{chunk.content}"
        )
