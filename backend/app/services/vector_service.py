from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
)


class VectorService:
    COLLECTION_NAME = "repository_code"
    VECTOR_SIZE = 384

    def __init__(self):
        self.client = QdrantClient(":memory:")
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

    def upsert(
        self,
        points: list[PointStruct],
    ):
        if not points:
            return

        self.client.upsert(
            collection_name=self.COLLECTION_NAME,
            points=points,
        )

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
                    match=MatchValue(
                        value=repository_id
                    ),
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