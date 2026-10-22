"""
Re-index the Crumbs repository (or any repository by ID).
Runs code-indexing, chunking, and optionally vector re-embedding.

Usage:
    python backend/scripts/reindex.py [--repo-id 1] [--skip-vectors]
"""
import sys
import time
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.app.core.database import SessionLocal
from backend.app.models.repository import Repository
from backend.app.services.code_indexer import CodeIndexer
from backend.app.services.code_chunk_service import CodeChunkService


def reindex(repo_id: int = 1, skip_vectors: bool = False) -> None:
    db = SessionLocal()
    try:
        repo = db.get(Repository, repo_id)
        if not repo:
            print(f"Repository {repo_id} not found")
            sys.exit(1)

        print(f"Repository : {repo.name}")
        print(f"Local path : {repo.local_path}")
        print()

        t0 = time.perf_counter()

        # ── Step 1: Code indexing ────────────────────────────────────────
        print("=== Step 1: Code indexing ===")
        indexer = CodeIndexer()
        result = indexer.index_repository(db, repo_id, repo.local_path)
        elapsed = time.perf_counter() - t0
        print(f"  Indexed files   : {result['indexed_files']}")
        print(f"  Indexed symbols : {result['indexed_symbols']}")
        print(f"  Indexed imports : {result['indexed_imports']}")
        print(f"  Indexed calls   : {result['indexed_calls']}")
        print(f"  Unsupported     : {result['unsupported_files']}")
        print(f"  Time            : {elapsed:.2f}s")
        print()

        # ── Step 2: Chunking ─────────────────────────────────────────────
        print("=== Step 2: Chunking ===")
        t1 = time.perf_counter()
        chunk_svc = CodeChunkService()
        chunks = chunk_svc.create_chunks(db, repo_id, repo.local_path)
        elapsed = time.perf_counter() - t1
        print(f"  Created chunks : {len(chunks)}")
        print(f"  Time           : {elapsed:.2f}s")
        print()

        if skip_vectors:
            print("Skipping vector re-embedding (--skip-vectors)")
            return

        # ── Step 3: Vector re-embedding ──────────────────────────────────
        print("=== Step 3: Vector embedding ===")
        from backend.app.services.embedding_service import EmbeddingService
        from backend.app.services.vector_service import VectorService
        from backend.app.services.vector_indexing_service import VectorIndexingService

        t2 = time.perf_counter()
        emb_svc = EmbeddingService()
        vec_svc = VectorService()
        vec_idx = VectorIndexingService(emb_svc, vec_svc)
        vec_result = vec_idx.index_chunks(db, repo_id)
        elapsed = time.perf_counter() - t2
        print(f"  Chunks indexed : {vec_result.get('chunks_indexed', 0)}")
        print(f"  Time           : {elapsed:.2f}s")
        print()

        total = time.perf_counter() - t0
        print(f"Total time: {total:.2f}s")

    finally:
        db.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Re-index a repository")
    parser.add_argument("--repo-id", type=int, default=1)
    parser.add_argument("--skip-vectors", action="store_true")
    args = parser.parse_args()
    reindex(args.repo_id, args.skip_vectors)
