
from pathlib import Path

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    FilterSelector,
    MatchValue,
    PointStruct,
    VectorParams,
)


class VectorService:
    COLLECTION_NAME = "repository_code"
    VECTOR_SIZE = 384

    def __init__(self):
        qdrant_path = (
            Path(__file__).resolve().parents[3]
            / "data"
            / "qdrant"
        )

        qdrant_path.mkdir(parents=True, exist_ok=True)

        self.client = QdrantClient(path=str(qdrant_path))
        self._ensure_collection()

    def _ensure_collection(self):
        collections = self.client.get_collections()

        collection_names = {
            collection.name
            for collection in collections.collections
        }

        if self.COLLECTION_NAME in collection_names:
            return

        self.client.create_collection(
            collection_name=self.COLLECTION_NAME,
            vectors_config=VectorParams(
                size=self.VECTOR_SIZE,
                distance=Distance.COSINE,
            ),
        )

    def upsert(self, points: list[PointStruct]):
        if not points:
            return

        self.client.upsert(
            collection_name=self.COLLECTION_NAME,
            points=points,
        )

    def delete_repository(self, repository_id: int):
        repository_filter = Filter(
            must=[
                FieldCondition(
                    key="repository_id",
                    match=MatchValue(value=repository_id),
                )
            ]
        )

        self.client.delete(
            collection_name=self.COLLECTION_NAME,
            points_selector=FilterSelector(
                filter=repository_filter,
            ),
        )

    def existing_ids(self, repository_id: int) -> set[int]:
        """IDs (chunk IDs) of every vector stored for the repository."""
        repository_filter = Filter(
            must=[FieldCondition(key="repository_id", match=MatchValue(value=repository_id))]
        )
        ids: set[int] = set()
        offset = None
        while True:
            points, offset = self.client.scroll(
                collection_name=self.COLLECTION_NAME,
                scroll_filter=repository_filter,
                limit=1000,
                offset=offset,
                with_payload=False,
                with_vectors=False,
            )
            ids.update(int(point.id) for point in points)
            if offset is None:
                return ids

    def delete_ids(self, ids) -> None:
        ids = [int(i) for i in ids]
        if ids:
            self.client.delete(collection_name=self.COLLECTION_NAME, points_selector=ids)

    def count_repository(self, repository_id: int) -> int:
        repository_filter = Filter(
            must=[
                FieldCondition(
                    key="repository_id",
                    match=MatchValue(value=repository_id),
                )
            ]
        )

        result = self.client.count(
            collection_name=self.COLLECTION_NAME,
            count_filter=repository_filter,
            exact=True,
        )

        return result.count

    def search(
        self,
        vector: list[float],
        repository_id: int,
        limit: int = 10,
    ):
        query_filter = Filter(
            must=[
                FieldCondition(
                    key="repository_id",
                    match=MatchValue(value=repository_id),
                )
            ]
        )

        result = self.client.query_points(
            collection_name=self.COLLECTION_NAME,
            query=vector,
            query_filter=query_filter,
            limit=limit,
            with_payload=True,
        )

        return result.points


def create_vector_service():
    """Return the vector store selected by the VECTOR_BACKEND setting."""
    from backend.app.core.config import settings

    if settings.vector_backend == "pgvector":
        from backend.app.services.pgvector_service import PgVectorService

        return PgVectorService()

    return VectorService()
