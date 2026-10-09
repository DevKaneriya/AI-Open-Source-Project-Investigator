import time

from qdrant_client.models import PointStruct
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.code_chunk import CodeChunk
from backend.app.services.embedding_service import EmbeddingService
from backend.app.services.vector_service import VectorService


class VectorIndexingService:
    EMBEDDING_BATCH_SIZE = 256

    def __init__(
        self,
        embedding_service: EmbeddingService,
        vector_service: VectorService,
    ):
        self.embedding_service = embedding_service
        self.vector_service = vector_service

    def index_chunks(self, db: Session, repository_id: int) -> dict:
        overall_start = time.perf_counter()

        print("\n" + "=" * 70)
        print("VECTOR INDEXING STARTED")
        print("=" * 70)
        print(f"Repository ID: {repository_id}")
        print(f"Batch size: {self.EMBEDDING_BATCH_SIZE}")

        # ---------------------------------------------------------
        # STEP 1: Load chunks from PostgreSQL
        # ---------------------------------------------------------

        step_start = time.perf_counter()

        statement = (
            select(CodeChunk)
            .where(CodeChunk.repository_id == repository_id)
            .order_by(CodeChunk.id)
        )

        chunks = db.scalars(statement).all()

        step_time = time.perf_counter() - step_start

        print(f"\n[1] PostgreSQL chunk loading")
        print(f"    Chunks found: {len(chunks)}")
        print(f"    Time: {step_time:.3f} seconds")
        
        self.vector_service.delete_repository(repository_id)

        if not chunks:
            print("\nNo chunks found.")
            return {
                "repository_id": repository_id,
                "chunks_indexed": 0,
            }

        total_batches = (
            len(chunks) + self.EMBEDDING_BATCH_SIZE - 1
        ) // self.EMBEDDING_BATCH_SIZE

        print(f"    Total batches: {total_batches}")

        # ---------------------------------------------------------
        # STEP 2: Process batches
        # ---------------------------------------------------------

        total_indexed = 0

        total_embedding_time = 0
        total_point_creation_time = 0
        total_qdrant_time = 0

        for batch_number, start in enumerate(
            range(0, len(chunks), self.EMBEDDING_BATCH_SIZE),
            start=1,
        ):
            batch_start = time.perf_counter()

            batch = chunks[
                start:start + self.EMBEDDING_BATCH_SIZE
            ]

            print("\n" + "-" * 70)
            print(
                f"BATCH {batch_number}/{total_batches}"
            )
            print(
                f"Chunks: {start + 1} - "
                f"{start + len(batch)} "
                f"({len(batch)} chunks)"
            )

            # -----------------------------------------------------
            # STEP 2A: Build embedding text
            # -----------------------------------------------------

            text_start = time.perf_counter()

            embedding_texts = [
                self._build_embedding_text(chunk)
                for chunk in batch
            ]

            text_time = time.perf_counter() - text_start

            print(
                f"  [A] Build embedding texts: "
                f"{text_time:.3f}s"
            )

            # -----------------------------------------------------
            # STEP 2B: Generate embeddings
            # -----------------------------------------------------

            embedding_start = time.perf_counter()

            embeddings = self.embedding_service.embed_many(
                embedding_texts
            )

            embedding_time = (
                time.perf_counter() - embedding_start
            )

            total_embedding_time += embedding_time

            print(
                f"  [B] Generate embeddings: "
                f"{embedding_time:.3f}s"
            )

            print(
                f"      Embeddings generated: "
                f"{len(embeddings)}"
            )

            if embeddings:
                print(
                    f"      Vector dimension: "
                    f"{len(embeddings[0])}"
                )

            # -----------------------------------------------------
            # STEP 2C: Create Qdrant points
            # -----------------------------------------------------

            point_start = time.perf_counter()

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
                            "repository_id": chunk.repository_id,
                            "file_id": chunk.file_id,
                            "symbol_id": chunk.symbol_id,
                            "start_line": chunk.start_line,
                            "end_line": chunk.end_line,
                            "content": chunk.content,
                        },
                    )
                )

            point_time = time.perf_counter() - point_start

            total_point_creation_time += point_time

            print(
                f"  [C] Create Qdrant points: "
                f"{point_time:.3f}s"
            )

            print(
                f"      Points created: {len(points)}"
            )

            # -----------------------------------------------------
            # STEP 2D: Qdrant upsert
            # -----------------------------------------------------

            qdrant_start = time.perf_counter()

            self.vector_service.upsert(points)

            qdrant_time = (
                time.perf_counter() - qdrant_start
            )

            total_qdrant_time += qdrant_time

            print(
                f"  [D] Qdrant upsert: "
                f"{qdrant_time:.3f}s"
            )

            # -----------------------------------------------------
            # Batch total
            # -----------------------------------------------------

            batch_time = (
                time.perf_counter() - batch_start
            )

            total_indexed += len(points)

            print(
                f"  [TOTAL] Batch time: "
                f"{batch_time:.3f}s"
            )

            print(
                f"  Progress: "
                f"{total_indexed}/{len(chunks)} "
                f"({total_indexed / len(chunks) * 100:.1f}%)"
            )

        # ---------------------------------------------------------
        # FINAL SUMMARY
        # ---------------------------------------------------------

        total_time = (
            time.perf_counter() - overall_start
        )

        print("\n" + "=" * 70)
        print("VECTOR INDEXING COMPLETED")
        print("=" * 70)

        print(f"Repository ID: {repository_id}")
        print(f"Chunks indexed: {total_indexed}")
        print(f"Total time: {total_time:.3f}s")

        print("\nTIME BREAKDOWN")
        print("-" * 70)
        print(
            f"Embedding time:       "
            f"{total_embedding_time:.3f}s"
        )
        print(
            f"Point creation time:   "
            f"{total_point_creation_time:.3f}s"
        )
        print(
            f"Qdrant upsert time:    "
            f"{total_qdrant_time:.3f}s"
        )

        print("=" * 70 + "\n")

        return {
            "repository_id": repository_id,
            "chunks_indexed": total_indexed,
            "total_time": total_time,
        }

    @staticmethod
    def _build_embedding_text(chunk):
        return (
            f"File ID: {chunk.file_id}\n"
            f"Symbol ID: {chunk.symbol_id}\n"
            f"Lines: {chunk.start_line}-{chunk.end_line}\n\n"
            f"{chunk.content}"
        )