from enum import Enum
from pydantic import BaseModel, ConfigDict, Field


class QuestionType(str, Enum):
    """Classification of user question types for retrieval strategy selection."""
    
    SYMBOL_LOOKUP = "symbol_lookup"
    """Where is a specific function, class, variable, or constant defined?"""
    
    CALL_SITE_LOOKUP = "call_site_lookup"
    """Where is a specific function or method called/invoked?"""
    
    FILE_EXPLANATION = "file_explanation"
    """What does a particular file or module do?"""
    
    FEATURE_FLOW = "feature_flow"
    """How does a specific feature work across multiple files/components?"""
    
    ARCHITECTURAL_OVERVIEW = "architectural_overview"
    """Explain the repository's overall structure, components, and architecture."""
    
    END_TO_END_FLOW = "end_to_end_flow"
    """Explain a complete execution path from frontend to database."""
    
    CROSS_CUTTING_CONCERN = "cross_cutting_concern"
    """Explain auth, logging, caching, error handling, or config across the app."""
    
    DEPENDENCY_ANALYSIS = "dependency_analysis"
    """Explain module dependencies, integrations, or service relationships."""


class QueryAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: str = Field(
        description="The primary investigation intent of the user."
    )
    
    question_type: QuestionType = Field(
        description="The type of question being asked, which determines retrieval strategy."
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