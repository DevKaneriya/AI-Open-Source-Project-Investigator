from pydantic import BaseModel, ConfigDict, Field


class QueryAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: str = Field(
        description="The primary investigation intent of the user."
    )

    concepts: list[str] = Field(
        description="Important concepts from the user's question."
    )

    symbol_queries: list[str] = Field(
        description="Queries useful for exact code symbol search."
    )

    text_queries: list[str] = Field(
        description="Queries useful for repository text search."
    )

    import_queries: list[str] = Field(
        description="Queries useful for import/module search."
    )