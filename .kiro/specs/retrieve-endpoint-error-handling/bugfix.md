# Bugfix Requirements Document

## Introduction

The `/retrieve` API endpoint at `/repositories/{repository_id}/retrieve` is returning internal server errors (HTTP 500) most of the time. This bug prevents users from successfully retrieving code evidence from repositories using the integrated LLM-powered query analysis and multi-method search pipeline.

The root causes include:
- Missing exception handling for various failure modes (LLM service failures, database errors, file I/O errors, JSON parsing errors)
- Unhandled LLM service initialization failures when GROQ_API_KEY is missing or invalid
- Database session management issues in parallel search operations
- Thread safety issues with ThreadPoolExecutor creating multiple independent database sessions
- Missing validation in evidence collector service
- Configuration validation issues

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN the /retrieve endpoint is called AND the LLM service (query_analyzer_service.analyze()) throws a RuntimeError due to missing or invalid GROQ_API_KEY THEN the system returns HTTP 500 internal server error instead of a proper error response

1.2 WHEN the /retrieve endpoint is called AND the LLM service throws a network connectivity error or rate limit error THEN the system returns HTTP 500 internal server error instead of a proper error response

1.3 WHEN the /retrieve endpoint is called AND the LLM service returns invalid JSON or empty response content THEN the system returns HTTP 500 internal server error instead of a proper error response

1.4 WHEN parallel search methods (_search_symbols, _search_imports) create database sessions in separate threads AND a database connection pool is exhausted or a connection error occurs THEN the system returns HTTP 500 internal server error instead of a proper error response

1.5 WHEN parallel search threads encounter exceptions AND those exceptions are not properly propagated from ThreadPoolExecutor THEN the system returns HTTP 500 internal server error instead of a proper error response

1.6 WHEN the code_retrieval_service.get_source() method throws OSError during file operations THEN the system returns HTTP 500 internal server error instead of a proper error response

1.7 WHEN the evidence_collector_service receives malformed data with missing dict keys or invalid data types THEN the system returns HTTP 500 internal server error instead of a proper error response

1.8 WHEN database sessions created in parallel threads are not properly cleaned up after exceptions THEN database connection leaks occur and subsequent requests fail with HTTP 500 errors

1.9 WHEN the LLMService is instantiated without GROQ_API_KEY being set THEN it raises RuntimeError during initialization causing service startup or request handling to fail with HTTP 500

1.10 WHEN ThreadPoolExecutor futures encounter exceptions in parallel search operations THEN those exceptions may not be properly caught and logged making debugging difficult

### Expected Behavior (Correct)

2.1 WHEN the /retrieve endpoint is called AND the LLM service throws a RuntimeError due to missing or invalid GROQ_API_KEY THEN the system SHALL return HTTP 400 Bad Request with a meaningful error message explaining the configuration issue

2.2 WHEN the /retrieve endpoint is called AND the LLM service throws a network connectivity error or rate limit error THEN the system SHALL return HTTP 503 Service Unavailable with a meaningful error message explaining the temporary service issue

2.3 WHEN the /retrieve endpoint is called AND the LLM service returns invalid JSON or empty response content THEN the system SHALL return HTTP 500 with a meaningful error message explaining the LLM response parsing failure

2.4 WHEN parallel search methods (_search_symbols, _search_imports) create database sessions in separate threads AND a database connection error occurs THEN the system SHALL properly catch the exception, log it, return empty results for that search method, and continue with other search methods

2.5 WHEN parallel search threads encounter exceptions THEN the system SHALL properly catch those exceptions from ThreadPoolExecutor futures, log them with context, and return partial results from successful searches

2.6 WHEN the code_retrieval_service.get_source() method throws OSError during file operations THEN the system SHALL catch the exception, skip that source context, and continue processing other results

2.7 WHEN the evidence_collector_service receives malformed data THEN the system SHALL validate input structure, use safe default values for missing fields, and continue processing valid evidence

2.8 WHEN database sessions are created in parallel threads THEN the system SHALL ensure proper session cleanup in finally blocks even when exceptions occur

2.9 WHEN the LLMService is instantiated without GROQ_API_KEY being set THEN it SHALL raise RuntimeError with a clear message during initialization, and this SHALL be caught at the endpoint level to return HTTP 400 with configuration guidance

2.10 WHEN ThreadPoolExecutor futures encounter exceptions in parallel search operations THEN the system SHALL catch, log, and handle each future's exception independently without failing the entire retrieval request

### Unchanged Behavior (Regression Prevention)

3.1 WHEN the /retrieve endpoint is called with valid parameters AND all services operate successfully THEN the system SHALL CONTINUE TO return HTTP 200 with properly structured retrieval results including query analysis, search results, and ranked evidence

3.2 WHEN parallel search methods execute successfully THEN the system SHALL CONTINUE TO return combined results from symbol search, text search, import search, and semantic search

3.3 WHEN the semantic search service returns results THEN the system SHALL CONTINUE TO include those results in the retrieval response with proper scoring

3.4 WHEN source context is successfully retrieved for symbols THEN the system SHALL CONTINUE TO include that source context in the response with file path, line numbers, and content

3.5 WHEN the evidence collector service processes valid retrieval results THEN the system SHALL CONTINUE TO properly rank and merge evidence based on type scores, query matching, and corroboration

3.6 WHEN the query analyzer service successfully analyzes a query THEN the system SHALL CONTINUE TO return structured query analysis with intent, concepts, symbol queries, text queries, and import queries

3.7 WHEN database sessions are properly managed THEN the system SHALL CONTINUE TO execute queries efficiently and return results without connection leaks

3.8 WHEN the /retrieve endpoint validates input parameters (empty query, invalid limit) THEN the system SHALL CONTINUE TO return HTTP 400 for validation errors

3.9 WHEN the repository does not exist or has not been cloned THEN the system SHALL CONTINUE TO return HTTP 404 or HTTP 400 with appropriate error messages (currently raises ValueError which is caught)

3.10 WHEN multiple retrieval requests are made concurrently THEN the system SHALL CONTINUE TO handle them independently without thread interference or shared state corruption
