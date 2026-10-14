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
from backend.app.services.answer_generation_service import AnswerGenerationService

from backend.app.models.repository import Repository
from backend.app.models.repository_file import RepositoryFile
from backend.app.models.code_symbol import CodeSymbol
from backend.app.models.code_import import CodeImport
from backend.app.models.code_chunk import CodeChunk
from backend.app.models.investigation_answer import InvestigationResponse


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

answer_generation_service = AnswerGenerationService(
    llm_service=llm_service,
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
) -> InvestigationResponse:
    """
    Generate evidence-grounded technical answer to a repository question.
    
    This endpoint:
    1. Retrieves relevant code evidence using the full retrieval pipeline
    2. Generates a natural language answer grounded in that evidence
    3. Returns the answer with citations to specific source locations
    
    Use this endpoint when you need a explained answer.
    Use /retrieve if you only need raw evidence without explanation.
    
    Args:
        repository_id: Repository database ID
        query: Natural language question about the repository
        db: Database session
        
    Returns:
        InvestigationResponse with answer, citations, and evidence summary
        
    Raises:
        404: Repository not found
        400: Invalid query or retrieval failed
        500: LLM service error or unexpected failure
    """
    import time
    start_time = time.time()
    
    try:
        # Use the existing retrieval pipeline to get evidence
        retrieval_result = retrieval_service.retrieve(
            db=db,
            repository_id=repository_id,
            query=query,
            semantic_limit=10,
        )
        
        # Extract relevant data from retrieval result
        evidence = retrieval_result.get('evidence', [])
        question_type = retrieval_result['query_analysis'].get('question_type')
        repository = retrieval_result['repository']
        
        # Generate evidence-grounded answer
        investigation_answer = answer_generation_service.generate_answer(
            question=query,
            evidence=evidence,
            question_type=question_type,
            repository_name=repository.get('name'),
        )
        
        # Add execution time
        execution_time = round(time.time() - start_time, 3)
        investigation_answer.execution_time_seconds = execution_time
        
        # Construct response
        response = InvestigationResponse(
            repository=repository,
            query=query,
            question_type=question_type,
            investigation=investigation_answer,
            evidence_summary=retrieval_result.get('summary', {}),
            metadata={
                'version': '1.0',
                'timestamp': time.time(),
                'retrieval_time_seconds': retrieval_result.get('summary', {}).get('execution_time_seconds', 0),
                'total_time_seconds': execution_time,
            }
        )
        
        return response

    except ValueError as exc:
        raise HTTPException(
            status_code=404 if "not found" in str(exc).lower() else 400,
            detail=str(exc),
        )
    
    except RuntimeError as exc:
        # LLM service errors
        error_msg = str(exc)
        if "GROQ_API_KEY" in error_msg or "not configured" in error_msg:
            raise HTTPException(
                status_code=400,
                detail=f"Configuration error: {error_msg}",
            )
        raise HTTPException(
            status_code=500,
            detail=f"Service error: {error_msg}",
        )
    
    except Exception as exc:
        import logging
        logging.error(f"Unexpected error in investigate endpoint: {exc}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"An unexpected error occurred: {str(exc)}",
        )
        
        
@app.post("/repositories/{repository_id}/investigate/bulk")
def investigate_repository_bulk(
    repository_id: int,
    queries: list[str],
    delay_between_queries: float = 2.0,
    db: Session = Depends(get_db),
) -> dict:
    """
    Generate evidence-grounded answers for multiple queries in one request.
    
    Testing/development endpoint that processes multiple queries sequentially
    and returns all results together.
    
    Args:
        repository_id: Repository database ID
        queries: List of natural language questions about the repository
        delay_between_queries: Delay in seconds between queries (default: 2.0) to avoid rate limits
        db: Database session
        
    Returns:
        Dict with:
        - repository: Repository info
        - results: List of investigation results (one per query)
        - summary: Overall execution statistics
        
    Raises:
        404: Repository not found
        400: Invalid queries or retrieval failed
        500: Service error
        
    Note: This endpoint respects rate limits by adding delays between queries.
    If you hit rate limits, reduce the number of queries or increase delay_between_queries.
    """
    import time
    start_time = time.time()
    
    # Validate repository exists first
    repository = db.get(Repository, repository_id)
    if not repository:
        raise HTTPException(
            status_code=404,
            detail="Repository not found.",
        )
    
    if not queries:
        raise HTTPException(
            status_code=400,
            detail="No queries provided. Please provide at least one query.",
        )
    
    if len(queries) > 10:
        raise HTTPException(
            status_code=400,
            detail=f"Too many queries ({len(queries)}). Maximum 10 queries allowed per bulk request.",
        )
    
    # Validate delay
    if delay_between_queries < 0:
        raise HTTPException(
            status_code=400,
            detail="delay_between_queries must be >= 0",
        )
    
    results = []
    errors = []
    rate_limit_hit = False
    
    for idx, query in enumerate(queries, 1):
        # Add delay between queries (except for the first one)
        if idx > 1 and delay_between_queries > 0:
            time.sleep(delay_between_queries)
        
        query_start = time.time()
        
        try:
            # Use the existing retrieval pipeline
            retrieval_result = retrieval_service.retrieve(
                db=db,
                repository_id=repository_id,
                query=query,
                semantic_limit=10,
            )
            
            # Extract relevant data
            evidence = retrieval_result.get('evidence', [])
            question_type = retrieval_result['query_analysis'].get('question_type')
            repo_info = retrieval_result['repository']
            
            # Generate answer
            investigation_answer = answer_generation_service.generate_answer(
                question=query,
                evidence=evidence,
                question_type=question_type,
                repository_name=repo_info.get('name'),
            )
            
            # Add execution time
            query_execution_time = round(time.time() - query_start, 3)
            investigation_answer.execution_time_seconds = query_execution_time
            
            # Build result
            result = {
                "query_index": idx,
                "query": query,
                "question_type": question_type,
                "investigation": investigation_answer.model_dump(),
                "evidence_summary": retrieval_result.get('summary', {}),
                "execution_time_seconds": query_execution_time,
                "status": "success"
            }
            
            results.append(result)
            
        except Exception as exc:
            import logging
            error_msg = str(exc)
            
            # Check if it's a rate limit error
            is_rate_limit = "rate limit" in error_msg.lower() or "429" in error_msg
            
            if is_rate_limit:
                rate_limit_hit = True
                # Extract wait time if available
                import re
                wait_match = re.search(r'try again in (\d+[msh][\d.]+[msh]?)', error_msg)
                wait_time = wait_match.group(1) if wait_match else "unknown"
                
                error_summary = f"Rate limit exceeded. Please try again in {wait_time}. Consider reducing queries or increasing delay_between_queries parameter."
            else:
                error_summary = error_msg
            
            logging.error(f"Error processing query {idx} ('{query}'): {error_msg}")
            
            # Record error but continue with other queries unless it's rate limit
            error_result = {
                "query_index": idx,
                "query": query,
                "status": "error",
                "error": error_summary,
                "error_type": "rate_limit" if is_rate_limit else "other",
                "execution_time_seconds": round(time.time() - query_start, 3)
            }
            
            results.append(error_result)
            errors.append(error_result)
            
            # If rate limit hit, stop processing remaining queries
            if is_rate_limit:
                # Add remaining queries as skipped
                for remaining_idx in range(idx + 1, len(queries) + 1):
                    results.append({
                        "query_index": remaining_idx,
                        "query": queries[remaining_idx - 1],
                        "status": "skipped",
                        "error": "Skipped due to rate limit on previous query",
                        "error_type": "rate_limit",
                        "execution_time_seconds": 0
                    })
                break
    
    total_time = round(time.time() - start_time, 3)
    
    return {
        "repository": {
            "id": repository.id,
            "name": repository.name,
            "url": repository.url,
        },
        "results": results,
        "summary": {
            "total_queries": len(queries),
            "successful": len([r for r in results if r.get('status') == 'success']),
            "failed": len([r for r in results if r.get('status') == 'error']),
            "skipped": len([r for r in results if r.get('status') == 'skipped']),
            "rate_limit_hit": rate_limit_hit,
            "total_execution_time_seconds": total_time,
        },
        "metadata": {
            "version": "1.0",
            "timestamp": time.time(),
            "endpoint": "bulk_investigate",
            "delay_between_queries": delay_between_queries,
        }
    }
        
        
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
    
    except RuntimeError as exc:
        # LLM service configuration errors
        error_msg = str(exc)
        if "GROQ_API_KEY" in error_msg or "not configured" in error_msg:
            raise HTTPException(
                status_code=400,
                detail=f"Configuration error: {error_msg}",
            )
        raise HTTPException(
            status_code=500,
            detail=f"Service error: {error_msg}",
        )
    
    except OSError as exc:
        # File system errors
        raise HTTPException(
            status_code=500,
            detail=f"File system error: {str(exc)}",
        )
    
    except Exception as exc:
        # Catch all other errors to prevent unhandled 500s
        import logging
        logging.error(f"Unexpected error in retrieve endpoint: {exc}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"An unexpected error occurred during retrieval: {str(exc)}",
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