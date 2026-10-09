from fastapi import FastAPI
from pydantic import BaseModel

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app.core.config import settings

from backend.app.core.database import Base, engine
from backend.app.core.database import get_db

from backend.app.services.github_service import GitHubService
from backend.app.services.repository_scanner import RepositoryScanner
from backend.app.services.code_indexer import CodeIndexer
from backend.app.services.code_search_service import CodeSearchService
from backend.app.services.repository_service import RepositoryService
from backend.app.services.code_retrieval_service import CodeRetrievalService
from backend.app.services.repository_search_service import RepositorySearchService
from backend.app.services.code_relationship_service import CodeRelationshipService
from backend.app.services.repository_investigation_service import RepositoryInvestigationService
from backend.app.services.embedding_service import EmbeddingService
from backend.app.services.vector_service import VectorService
from backend.app.services.vector_indexing_service import VectorIndexingService
from backend.app.services.code_chunk_service import CodeChunkService
from backend.app.services.semantic_search_service import SemanticSearchService
from backend.app.services.retrieval_service import RetrievalService
from backend.app.services.llm_service import LLMService
from backend.app.services.query_analyzer_service import QueryAnalyzerService

from backend.app.models.repository import Repository
from backend.app.models.repository_file import RepositoryFile
from backend.app.models.code_symbol import CodeSymbol
from backend.app.models.code_import import CodeImport
from backend.app.models.code_chunk import CodeChunk


Base.metadata.create_all(bind=engine)

app = FastAPI(
    title=settings.app_name,
    description="Agentic AI platform for investigating open-source software repositories.",
    version="0.1.0",
)


github_service = GitHubService()
repository_scanner = RepositoryScanner()
code_indexer = CodeIndexer()
code_search_service = CodeSearchService()
repository_service = RepositoryService()
code_retrieval_service = CodeRetrievalService()
repository_search_service = RepositorySearchService()
code_relationship_service = CodeRelationshipService()
repository_investigation_service = RepositoryInvestigationService()
code_chunk_service = CodeChunkService()

embedding_service = EmbeddingService()

vector_service = VectorService()

vector_indexing_service = VectorIndexingService(
    embedding_service=embedding_service,
    vector_service=vector_service,
)

semantic_search_service = SemanticSearchService(
    embedding_service=embedding_service,
    vector_service=vector_service,
)

llm_service = LLMService()

query_analyzer_service = QueryAnalyzerService(
    llm_service=llm_service,
)

retrieval_service = RetrievalService(
    semantic_search_service=semantic_search_service,
    query_analyzer_service=query_analyzer_service,
)


class RepositoryRequest(BaseModel):
    repository_url: str



@app.post("/repositories/scan")
def scan_repository(request: RepositoryRequest):
    repository_path = github_service.clone_repository(
        request.repository_url
    )

    scan_result = repository_scanner.scan(
        repository_path
    )

    return scan_result

@app.post("/repositories/{repository_id}/index")
def index_repository(
    repository_id: int,
    db: Session = Depends(get_db),
):
    repository = db.get(Repository, repository_id)

    if not repository:
        raise HTTPException(
            status_code=404,
            detail="Repository not found.",
        )

    if not repository.local_path:
        raise HTTPException(
            status_code=400,
            detail="Repository has not been cloned.",
        )

    return code_indexer.index_repository(
        db,
        repository.id,
        repository.local_path,
    )


@app.get("/repositories/{repository_id}/search")
def search_repository(
    repository_id: int,
    query: str,
    db: Session = Depends(get_db),
):
    repository = db.get(Repository, repository_id)

    if not repository:
        raise HTTPException(
            status_code=404,
            detail="Repository not found.",
        )

    return {
        "query": query,
        "results": code_search_service.search(
            db,
            repository_id,
            query,
        ),
    }
    
    
@app.post("/repositories")
def add_repository(
    request: RepositoryRequest,
    db: Session = Depends(get_db),
):
    return repository_service.add_repository(
        db,
        request.repository_url,
    )
    
    
@app.get("/repositories/{repository_id}/source")
def get_source(
    repository_id: int,
    file_path: str,
    start_line: int,
    end_line: int,
    db: Session = Depends(get_db),
):
    repository = db.get(Repository, repository_id)

    if not repository:
        raise HTTPException(
            status_code=404,
            detail="Repository not found.",
        )

    if not repository.local_path:
        raise HTTPException(
            status_code=400,
            detail="Repository has not been cloned.",
        )

    try:
        return code_retrieval_service.get_source(
            repository_path=repository.local_path,
            file_path=file_path,
            start_line=start_line,
            end_line=end_line,
        )

    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail="Source file not found.",
        )

    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid file path.",
        )
        
        
@app.get("/repositories/{repository_id}/search-text")
def search_repository_text(
    repository_id: int,
    query: str,
    db: Session = Depends(get_db),
):
    repository = db.get(Repository, repository_id)

    if not repository:
        raise HTTPException(
            status_code=404,
            detail="Repository not found.",
        )

    if not repository.local_path:
        raise HTTPException(
            status_code=400,
            detail="Repository has not been cloned.",
        )

    return {
        "query": query,
        "results": repository_search_service.search_text(
            repository.local_path,
            query,
        ),
    }
    
    
@app.get("/repositories/{repository_id}/relationships/calls")
def get_symbol_calls(
    repository_id: int,
    symbol_name: str,
    db: Session = Depends(get_db),
):
    repository = db.get(Repository, repository_id)

    if not repository:
        raise HTTPException(
            status_code=404,
            detail="Repository not found.",
        )

    return {
        "symbol": symbol_name,
        "calls": code_relationship_service.get_calls(
            db,
            repository_id,
            symbol_name,
        ),
    }
    
    
@app.get("/repositories/{repository_id}/investigate")
def investigate_repository(
    repository_id: int,
    query: str,
    db: Session = Depends(get_db),
):
    try:
        return repository_investigation_service.investigate(
            db=db,
            repository_id=repository_id,
            query=query,
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )
        
        
@app.post(
    "/repositories/{repository_id}/vector-index"
)
def index_repository_vectors(
    repository_id: int,
    db: Session = Depends(get_db),
):
    try:
        return vector_indexing_service.index_chunks(
            db=db,
            repository_id=repository_id,
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )
        
        
@app.post(
    "/repositories/{repository_id}/chunks"
)
def create_repository_chunks(
    repository_id: int,
    db: Session = Depends(get_db),
):
    repository = db.get(
        Repository,
        repository_id,
    )

    if not repository:
        raise HTTPException(
            status_code=404,
            detail="Repository not found.",
        )

    if not repository.local_path:
        raise HTTPException(
            status_code=400,
            detail="Repository has not been cloned.",
        )

    try:
        chunks = code_chunk_service.create_chunks(
            db=db,
            repository_id=repository_id,
            repository_path=repository.local_path,
        )

        return {
            "repository_id": repository_id,
            "chunks_created": len(chunks),
        }

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )
        
        
@app.get(
    "/repositories/{repository_id}/semantic-search"
)
def semantic_search(
    repository_id: int,
    query: str,
    limit: int = 10,
    db: Session = Depends(get_db),
):
    repository = db.get(
        Repository,
        repository_id,
    )

    if not repository:
        raise HTTPException(
            status_code=404,
            detail="Repository not found.",
        )

    if not query.strip():
        raise HTTPException(
            status_code=400,
            detail="Search query cannot be empty.",
        )

    if limit < 1 or limit > 50:
        raise HTTPException(
            status_code=400,
            detail="Limit must be between 1 and 50.",
        )

    results = semantic_search_service.search(
        db=db,
        repository_id=repository_id,
        query=query,
        limit=limit,
    )

    return {
        "repository_id": repository_id,
        "query": query,
        "results": results,
        "count": len(results),
    }
    
    
@app.get(
    "/repositories/{repository_id}/retrieve"
)
def retrieve_repository(
    repository_id: int,
    query: str,
    limit: int = 10,
    db: Session = Depends(get_db),
):
    if not query.strip():
        raise HTTPException(
            status_code=400,
            detail="Retrieval query cannot be empty.",
        )

    if limit < 1 or limit > 50:
        raise HTTPException(
            status_code=400,
            detail="Limit must be between 1 and 50.",
        )

    try:
        result = retrieval_service.retrieve(
            db=db,
            repository_id=repository_id,
            query=query,
            semantic_limit=limit,
        )

        return result

    except ValueError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        )
        
        
        
@app.get("/query-analyze")
def analyze_query(query: str):
    if not query.strip():
        raise HTTPException(
            status_code=400,
            detail="Query cannot be empty."
        )

    try:
        result = query_analyzer_service.analyze(query)

        return result.model_dump()

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc)
        )

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=str(exc)
        )