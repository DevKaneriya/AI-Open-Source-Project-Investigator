"""
Investigation Answer Schema

Defines the structure for evidence-grounded technical answers
generated from repository analysis.
"""

from pydantic import BaseModel, Field


class Citation(BaseModel):
    """Source code citation supporting a claim in the answer."""
    
    evidence_id: str = Field(
        description="Evidence identifier (E1, E2, etc.)"
    )
    
    file: str = Field(
        description="Repository file path"
    )
    
    start_line: int = Field(
        description="Starting line number of the cited code (0 if not available)"
    )
    
    end_line: int = Field(
        description="Ending line number of the cited code (0 if not available)"
    )
    
    symbol: str = Field(
        description="Symbol name (function, class, method) if applicable (empty string if not available)"
    )
    
    snippet: str = Field(
        description="Short code snippet supporting the claim (max 200 chars)"
    )
    
    relevance: str = Field(
        description="Brief explanation of how this source supports the associated claim"
    )


class InvestigationAnswer(BaseModel):
    """Evidence-grounded answer to a technical repository question."""
    
    question: str = Field(
        description="The original user question"
    )
    
    answer: str = Field(
        description="Natural language technical explanation grounded in evidence"
    )
    
    citations: list[Citation] = Field(
        default_factory=list,
        description="Source code references supporting claims in the answer"
    )
    
    confidence: str = Field(
        description="Assessment of answer completeness: 'high', 'partial', or 'insufficient'",
        pattern="^(high|partial|insufficient)$"
    )
    
    limitations: list[str] = Field(
        default_factory=list,
        description="Missing information or unverified connections in the requested flow"
    )
    
    execution_time_seconds: float | None = Field(
        default=None,
        description="Total time to generate the investigation answer"
    )


class InvestigationResponse(BaseModel):
    """Complete investigation response including metadata."""
    
    repository: dict = Field(
        description="Repository metadata (id, name, url)"
    )
    
    query: str = Field(
        description="Original user question"
    )
    
    question_type: str | None = Field(
        default=None,
        description="Classified question type from query analysis"
    )
    
    investigation: InvestigationAnswer = Field(
        description="Evidence-grounded answer with citations"
    )
    
    evidence_summary: dict = Field(
        description="Summary of evidence used (symbol count, source contexts, etc.)"
    )
    
    metadata: dict = Field(
        default_factory=dict,
        description="Additional metadata (version, timestamp, etc.)"
    )
