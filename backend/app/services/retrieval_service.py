from concurrent.futures import ThreadPoolExecutor, as_completed

from sqlalchemy.orm import Session

from backend.app.core.database import SessionLocal
from backend.app.models.repository import Repository

from backend.app.services.code_relationship_service import (
    CodeRelationshipService,
)

from backend.app.services.code_retrieval_service import (
    CodeRetrievalService,
)

from backend.app.services.code_search_service import (
    CodeSearchService,
)

from backend.app.services.import_search_service import (
    ImportSearchService,
)

from backend.app.services.repository_search_service import (
    RepositorySearchService,
)

from backend.app.services.semantic_search_service import (
    SemanticSearchService,
)

from backend.app.services.query_analyzer_service import (
    QueryAnalyzerService,
)

from backend.app.services.evidence_collector_service import (
    EvidenceCollectorService,
)

from backend.app.services.architectural_file_service import (
    ArchitecturalFileService,
)

from backend.app.models.query_analysis import QuestionType


class RetrievalService:

    MAX_SYMBOL_QUERIES = 5
    MAX_TEXT_QUERIES = 5
    MAX_IMPORT_QUERIES = 3
    MAX_SOURCE_CONTEXTS = 10  # Maximum number of source code contexts to retrieve

    def __init__(
        self,
        semantic_search_service: SemanticSearchService,
        query_analyzer_service: QueryAnalyzerService,
    ):
        self.code_search_service = CodeSearchService()

        self.repository_search_service = (
            RepositorySearchService()
        )

        self.code_retrieval_service = (
            CodeRetrievalService()
        )

        self.code_relationship_service = (
            CodeRelationshipService()
        )

        self.import_search_service = (
            ImportSearchService()
        )

        self.semantic_search_service = (
            semantic_search_service
        )

        self.query_analyzer_service = (
            query_analyzer_service
        )

        self.evidence_collector_service = (
            EvidenceCollectorService()
        )

        self.architectural_file_service = (
            ArchitecturalFileService()
        )

    # =============================================================
    # MAIN RETRIEVAL PIPELINE
    # =============================================================

    def retrieve(
        self,
        db: Session,
        repository_id: int,
        query: str,
        semantic_limit: int = 10,
    ) -> dict:

        import time
        start_time = time.time()

        query = query.strip()

        if not query:
            raise ValueError(
                "Retrieval query cannot be empty."
            )

        repository = db.get(
            Repository,
            repository_id,
        )

        if not repository:
            raise ValueError(
                "Repository not found."
            )

        if not repository.local_path:
            raise ValueError(
                "Repository has not been cloned."
            )

        # ---------------------------------------------------------
        # 1. Analyze user query
        # ---------------------------------------------------------

        try:
            query_analysis = (
                self.query_analyzer_service.analyze(query)
            )
        except RuntimeError as e:
            # LLM service configuration error
            raise RuntimeError(
                f"Query analysis failed due to LLM service configuration: {str(e)}"
            )
        except Exception as e:
            # Other LLM-related errors (network, rate limits, parsing)
            import logging
            logging.error(f"Query analysis error: {e}", exc_info=True)
            raise RuntimeError(
                f"Query analysis failed: {str(e)}"
            )

        symbol_queries = self._prepare_queries(
            query_analysis.symbol_queries,
            self.MAX_SYMBOL_QUERIES,
        )

        text_queries = self._prepare_queries(
            query_analysis.text_queries,
            self.MAX_TEXT_QUERIES,
        )

        import_queries = self._prepare_queries(
            query_analysis.import_queries,
            self.MAX_IMPORT_QUERIES,
        )

        # ---------------------------------------------------------
        # 2. Multi-Stage Retrieval for Architectural Questions
        #
        # For broad architectural questions, we use a staged approach:
        # 1. Discover key architectural files first
        # 2. Extract their contents for immediate evidence
        # 3. Then run standard searches with reduced semantic limit
        # ---------------------------------------------------------

        architectural_evidence = []
        adjusted_semantic_limit = semantic_limit

        if query_analysis.question_type in (
            QuestionType.ARCHITECTURAL_OVERVIEW,
            QuestionType.CROSS_CUTTING_CONCERN,
            QuestionType.DEPENDENCY_ANALYSIS,
        ):
            # Discover and extract key architectural files
            architectural_evidence = self._staged_architectural_retrieval(
                db=db,
                repository_id=repository_id,
                repository_path=repository.local_path,
                question_type=query_analysis.question_type,
            )

            # Reduce semantic search limit since we already have targeted evidence
            adjusted_semantic_limit = max(5, semantic_limit // 2)

        # ---------------------------------------------------------
        # 3. Run deterministic searches independently
        # ---------------------------------------------------------

        symbol_results = []
        text_results = []
        import_results = []

        with ThreadPoolExecutor(
            max_workers=3
        ) as executor:

            futures = {}

            if symbol_queries:

                futures[
                    executor.submit(
                        self._search_symbols,
                        repository_id,
                        symbol_queries,
                    )
                ] = "symbols"

            if text_queries:

                futures[
                    executor.submit(
                        self._search_text,
                        repository.local_path,
                        text_queries,
                    )
                ] = "text"

            if import_queries:

                futures[
                    executor.submit(
                        self._search_imports,
                        repository_id,
                        import_queries,
                    )
                ] = "imports"

            for future in as_completed(futures):

                result_type = futures[future]

                try:
                    result = future.result()

                    if result_type == "symbols":
                        symbol_results = result

                    elif result_type == "text":
                        text_results = result

                    elif result_type == "imports":
                        import_results = result

                except Exception as e:
                    # Log error but continue with other searches
                    import logging
                    logging.error(
                        f"Error in parallel {result_type} search: {e}",
                        exc_info=True
                    )
                    # Leave results as empty list for this search type
                    continue

        # ---------------------------------------------------------
        # 4. Semantic search
        #
        # IMPORTANT:
        # Use the original user query unchanged.
        # Use adjusted limit for architectural questions.
        # ---------------------------------------------------------

        semantic_results = []
        
        try:
            semantic_results = (
                self.semantic_search_service.search(
                    db=db,
                    repository_id=repository_id,
                    query=query,
                    limit=adjusted_semantic_limit,
                )
            )
        except Exception as e:
            # Log error but continue without semantic results
            import logging
            logging.warning(f"Semantic search failed: {e}")
            semantic_results = []

        # ---------------------------------------------------------
        # 5. Exact source retrieval
        # ---------------------------------------------------------

        source_candidates = (
            symbol_results
            + text_results
            + semantic_results
        )

        source_context = self._build_source_context(
            repository.local_path,
            source_candidates,
        )

        # ---------------------------------------------------------
        # 6. Code relationships
        #
        # Call graph relationships show which functions call which.
        # Critical for understanding execution flows and dependencies.
        # ---------------------------------------------------------

        relationships = []
        
        # Only extract relationships for questions that benefit from call graphs
        extract_relationships = query_analysis.question_type in (
            query_analysis.question_type.CALL_SITE_LOOKUP,
            query_analysis.question_type.FEATURE_FLOW,
            query_analysis.question_type.END_TO_END_FLOW,
            query_analysis.question_type.DEPENDENCY_ANALYSIS,
            query_analysis.question_type.CROSS_CUTTING_CONCERN,  # Added: helps trace patterns
        )
        
        if extract_relationships and symbol_results:
            relationships = self._build_relationships(
                db=db,
                repository_id=repository_id,
                symbol_results=symbol_results[:10],  # Limit to top 10 symbols
            )

        # ---------------------------------------------------------
        # 7. Build raw retrieval result
        # ---------------------------------------------------------

        retrieval_result = {

            "query": query,

            "repository": {
                "id": repository.id,
                "name": repository.name,
                "url": repository.url,
            },

            "query_analysis": {
                "intent": query_analysis.intent,
                "question_type": query_analysis.question_type.value,  # Include question type
                "concepts": query_analysis.concepts,
                "symbol_queries": symbol_queries,
                "text_queries": text_queries,
                "import_queries": import_queries,
            },

            "results": {

                "symbols": symbol_results,

                "text_matches": text_results,

                "imports": import_results,

                "semantic": semantic_results,

                "source_context": source_context,

                "relationships": relationships,

                "architectural_files": architectural_evidence,  # New: staged architectural evidence
            },

            "summary": {

                "symbol_matches": len(
                    symbol_results
                ),

                "text_matches": len(
                    text_results
                ),

                "imports": len(
                    import_results
                ),

                "semantic_matches": len(
                    semantic_results
                ),

                "source_contexts": len(
                    source_context
                ),

                "relationships": len(
                    relationships
                ),

                "architectural_files": len(
                    architectural_evidence
                ),
            },
        }

        # ---------------------------------------------------------
        # 8. Collect and rank evidence
        # ---------------------------------------------------------

        evidence_data = {}
        
        try:
            evidence_data = (
                self.evidence_collector_service.collect(
                    retrieval_results=retrieval_result,
                    question_type=query_analysis.question_type,  # Pass question type for architectural bonuses
                )
            )
        except Exception as e:
            # Log error but return results without ranked evidence
            import logging
            logging.error(f"Evidence collection failed: {e}", exc_info=True)
            evidence_data = {
                "query": query,
                "evidence": [],
                "summary": {
                    "candidate_count": 0,
                    "unique_count": 0,
                    "selected_count": 0,
                },
                "error": f"Evidence ranking failed: {str(e)}"
            }

        # ---------------------------------------------------------
        # 9. Build final optimized response
        # ---------------------------------------------------------

        execution_time = round(time.time() - start_time, 3)

        # Remove duplicate query from evidence data
        ranked_evidence = evidence_data.get("evidence", [])
        evidence_summary = evidence_data.get("summary", {})
        evidence_error = evidence_data.get("error")

        # Transform evidence for API response
        transformed_evidence = self._transform_evidence_for_response(ranked_evidence)

        final_response = {
            # Core query information
            "query": query,
            "repository": retrieval_result["repository"],
            
            # Query understanding
            "query_analysis": retrieval_result["query_analysis"],
            
            # Primary ranked evidence (most important for UI)
            "evidence": transformed_evidence,
            
            # Detailed raw results (for debugging or advanced use)
            "raw_results": retrieval_result["results"],
            
            # Summary statistics
            "summary": {
                **retrieval_result["summary"],
                "evidence_candidates": evidence_summary.get("candidate_count", 0),
                "evidence_selected": evidence_summary.get("selected_count", 0),
                "execution_time_seconds": execution_time,
            },
            
            # Metadata
            "metadata": {
                "version": "1.0",
                "timestamp": time.time(),
                "relationships_enabled": True,  # Re-enabled with question-type filtering
            }
        }

        # Add error information if evidence ranking failed
        if evidence_error:
            final_response["warnings"] = [{
                "type": "evidence_ranking_failure",
                "message": evidence_error
            }]

        return final_response

    # =============================================================
    # QUERY PREPARATION
    # =============================================================

    @staticmethod
    def _prepare_queries(
        queries: list[str],
        maximum: int,
    ) -> list[str]:

        prepared = []

        seen = set()

        for query in queries:

            query = query.strip()

            if not query:
                continue

            normalized = query.lower()

            if normalized in seen:
                continue

            seen.add(normalized)

            prepared.append(query)

            if len(prepared) >= maximum:
                break

        return prepared

    # =============================================================
    # PARALLEL SYMBOL SEARCH
    # =============================================================

    @staticmethod
    def _search_symbols(
        repository_id: int,
        queries: list[str],
    ) -> list[dict]:

        db = SessionLocal()

        try:

            results = []

            seen = set()

            service = CodeSearchService()

            for query in queries:

                try:
                    matches = service.search(
                        db=db,
                        repository_id=repository_id,
                        query=query,
                    )

                    for result in matches:

                        key = (
                            result["file"],
                            result["name"],
                            result["start_line"],
                            result["end_line"],
                        )

                        if key in seen:
                            continue

                        seen.add(key)

                        # Preserve which analyzer-generated query
                        # produced this result.
                        results.append(
                            {
                                **result,
                                "search_query": query,
                            }
                        )
                
                except Exception as e:
                    # Log error but continue with other queries
                    import logging
                    logging.warning(f"Error searching symbols for query '{query}': {e}")
                    continue

            return results

        except Exception as e:
            # Log critical error and return empty results
            import logging
            logging.error(f"Critical error in _search_symbols: {e}", exc_info=True)
            return []

        finally:

            db.close()

    # =============================================================
    # PARALLEL TEXT SEARCH
    # =============================================================

    @staticmethod
    def _search_text(
        repository_path: str,
        queries: list[str],
    ) -> list[dict]:

        try:
            service = RepositorySearchService()

            results = []

            seen = set()

            for query in queries:

                try:
                    matches = service.search_text(
                        repository_path=repository_path,
                        query=query,
                    )

                    for result in matches:

                        key = (
                            result.get("file"),
                            result.get("line"),
                            result.get("text"),
                        )

                        if key in seen:
                            continue

                        seen.add(key)

                        # Preserve which analyzer-generated query
                        # produced this result.
                        results.append(
                            {
                                **result,
                                "search_query": query,
                            }
                        )
                
                except Exception as e:
                    # Log error but continue with other queries
                    import logging
                    logging.warning(f"Error searching text for query '{query}': {e}")
                    continue

            return results

        except Exception as e:
            # Log critical error and return empty results
            import logging
            logging.error(f"Critical error in _search_text: {e}", exc_info=True)
            return []

    # =============================================================
    # PARALLEL IMPORT SEARCH
    # =============================================================

    @staticmethod
    def _search_imports(
        repository_id: int,
        queries: list[str],
    ) -> list[dict]:

        db = SessionLocal()

        try:

            results = []

            seen = set()

            service = ImportSearchService()

            for query in queries:

                try:
                    matches = service.search(
                        db=db,
                        repository_id=repository_id,
                        query=query,
                    )

                    for result in matches:

                        key = (
                            result["file"],
                            result["import"],
                        )

                        if key in seen:
                            continue

                        seen.add(key)

                        # Preserve which analyzer-generated query
                        # produced this result.
                        results.append(
                            {
                                **result,
                                "search_query": query,
                            }
                        )
                
                except Exception as e:
                    # Log error but continue with other queries
                    import logging
                    logging.warning(f"Error searching imports for query '{query}': {e}")
                    continue

            return results

        except Exception as e:
            # Log critical error and return empty results
            import logging
            logging.error(f"Critical error in _search_imports: {e}", exc_info=True)
            return []

        finally:

            db.close()

    # =============================================================
    # SOURCE RETRIEVAL
    # =============================================================

    def _build_source_context(
    self,
    repository_path: str,
    candidates: list[dict],
    ) -> list[dict]:
        """Extract source code for candidates from multiple search types.
        
        Handles results from:
        - Symbol search (has start_line, end_line)
        - Text search (has line, text)
        - Semantic search (has start_line, end_line, content)
        """
        contexts = []
        seen = set()

        for result in candidates:
            file_path = result.get("file")

            # Normalize line number fields from different search types
            start_line = result.get("start_line")
            if start_line is None:
                start_line = result.get("line")  # Text search uses "line"

            end_line = result.get("end_line")
            if end_line is None:
                end_line = start_line  # Text search has single line

            if not file_path or start_line is None:
                continue

            try:
                start_line = int(start_line)
                end_line = int(end_line) if end_line else start_line
            except (TypeError, ValueError):
                continue

            if start_line < 1 or end_line < start_line:
                continue

            # Expand context for single-line matches (from text search)
            if start_line == end_line:
                start_line = max(1, start_line - 3)
                end_line = end_line + 10

            key = (
                str(file_path).replace("\\", "/"),
                start_line,
                end_line,
            )

            if key in seen:
                continue

            seen.add(key)

            try:
                source = self.code_retrieval_service.get_source(
                    repository_path=repository_path,
                    file_path=file_path,
                    start_line=start_line,
                    end_line=end_line,
                )
                contexts.append(source)

            except (FileNotFoundError, ValueError, OSError):
                continue

            if len(contexts) >= self.MAX_SOURCE_CONTEXTS:
                break

        return contexts

    # =============================================================
    # CODE RELATIONSHIPS
    #
    # KEPT FOR FUTURE USE.
    #
    # Do not call this until Tree-sitter extraction has been fixed.
    # =============================================================

    def _build_relationships(
        self,
        db: Session,
        repository_id: int,
        symbol_results: list[dict],
    ) -> list[dict]:
        """
        Build call relationships for top symbol results AND retrieve source for called symbols.
        
        For feature flows, this helps trace execution paths by:
        1. Finding what each symbol calls
        2. Retrieving the actual implementation of called symbols
        
        Shows which functions call which other functions.
        Critical for understanding execution flows.
        """
        relationships = []

        seen_symbols = set()

        for result in symbol_results[:10]:

            symbol_name = result["name"]

            if symbol_name in seen_symbols:
                continue

            seen_symbols.add(symbol_name)

            try:
                calls = (
                    self.code_relationship_service
                    .get_calls(
                        db=db,
                        repository_id=repository_id,
                        symbol_name=symbol_name,
                    )
                )

                # Filter out malformed call names (e.g., single char like '$')
                valid_calls = [
                    call for call in calls
                    if call.get("called_name") 
                    and len(call["called_name"]) > 1
                    and not any(
                        char in call["called_name"] 
                        for char in ['{', '}', '(', ')', '[', ']', ',', ';']
                    )
                ]

                relationships.extend(valid_calls)
                
            except Exception as e:
                # Log but continue if relationship lookup fails
                import logging
                logging.warning(f"Failed to get relationships for {symbol_name}: {e}")
                continue

        return relationships

    # =============================================================
    # MULTI-STAGE ARCHITECTURAL RETRIEVAL
    # =============================================================

    def _staged_architectural_retrieval(
        self,
        db: Session,
        repository_id: int,
        repository_path: str,
        question_type: QuestionType,
    ) -> list[dict]:
        """
        Multi-stage retrieval for architectural questions.
        
        Discovers and extracts key architectural files based on question type:
        - ARCHITECTURAL_OVERVIEW: README → entry points → dependencies → routes → models
        - CROSS_CUTTING_CONCERN: configuration files → dependencies → relevant code
        
        Returns list of file evidence with source content.
        """
        architectural_files = self.architectural_file_service.discover_key_files(
            db=db,
            repository_id=repository_id,
        )
        
        evidence = []
        seen_paths = set()
        
        if question_type == QuestionType.ARCHITECTURAL_OVERVIEW:
            # For architectural overview: README + entry points + dependencies
            priority_order = [
                architectural_files["readme"][:1],  # Top README only
                architectural_files["entry_points"][:2],  # Top 2 entry points
                architectural_files["dependencies"][:2],  # Top 2 dependency files
                architectural_files["routes"][:2],  # Top 2 route files
                architectural_files["models"][:1],  # Top model file
            ]
            
        elif question_type == QuestionType.DEPENDENCY_ANALYSIS:
            # For dependency analysis: ALL dependencies + package manifests + config
            # Retrieve FULL content (not limited to 500 lines) for manifests
            priority_order = [
                architectural_files["dependencies"],  # ALL dependency files (not limited)
                architectural_files["configuration"][:2],  # Top 2 config files
            ]
            
        elif question_type == QuestionType.CROSS_CUTTING_CONCERN:
            # For cross-cutting concerns: config + dependencies + routes
            priority_order = [
                architectural_files["configuration"][:3],  # Top 3 config files
                architectural_files["dependencies"][:1],  # Top dependency file
                architectural_files["routes"][:2],  # Routes may show middleware
            ]
        
        else:
            # Fallback: just README and entry point
            priority_order = [
                architectural_files["readme"][:1],
                architectural_files["entry_points"][:1],
            ]
        
        # Extract source content for prioritized files
        for file_group in priority_order:
            for repo_file in file_group:
                if repo_file.path in seen_paths:
                    continue
                
                seen_paths.add(repo_file.path)
                
                try:
                    # Read entire file and get actual line count
                    from pathlib import Path
                    full_path = Path(repository_path) / repo_file.path
                    with open(full_path, 'r', encoding='utf-8') as f:
                        line_count = sum(1 for _ in f)
                    
                    # For dependency manifests, retrieve FULL content (no 500-line limit)
                    # For other files, limit to 500 lines
                    is_dependency_manifest = (
                        question_type == QuestionType.DEPENDENCY_ANALYSIS and
                        any(repo_file.path.endswith(ext) for ext in [
                            'package.json', 'package-lock.json', 'yarn.lock', 'pnpm-lock.yaml',
                            'requirements.txt', 'pyproject.toml', 'Pipfile', 'Pipfile.lock',
                            'pom.xml', 'build.gradle', 'Cargo.toml', 'go.mod', 'go.sum'
                        ])
                    )
                    
                    if is_dependency_manifest:
                        max_lines = min(line_count, 2000)  # Allow up to 2000 lines for manifests
                    else:
                        max_lines = min(line_count, 500)  # Limit other files to 500 lines
                    
                    source = self.code_retrieval_service.get_source(
                        repository_path=repository_path,
                        file_path=repo_file.path,
                        start_line=1,
                        end_line=max_lines,
                    )
                    
                    # Add architectural file category
                    file_category = self.architectural_file_service.get_file_category(repo_file.path)
                    source["architectural_category"] = file_category
                    source["total_lines"] = line_count  # Add total line count for reference
                    if line_count > max_lines:
                        source["is_truncated"] = True  # Mark if content was truncated
                    
                    evidence.append(source)
                    
                except (FileNotFoundError, ValueError, OSError) as e:
                    import logging
                    logging.warning(f"Failed to extract architectural file {repo_file.path}: {e}")
                    continue
        
        return evidence

    # =============================================================
    # EVIDENCE TRANSFORMATION
    # =============================================================
    
    def _transform_evidence_for_response(
        self,
        evidence: list[dict],
    ) -> list[dict]:
        """
        Transform internal evidence format to API response format.
        
        Maps:
        - content → snippet (with the actual source code)
        - evidence_score → relevance_score
        - Ensures all fields are present
        """
        transformed = []
        
        for item in evidence:
            transformed_item = {
                "file": item.get("file"),
                "language": item.get("language"),
                "symbol": item.get("symbol"),
                "symbol_type": item.get("symbol_type"),
                "qualified_name": item.get("qualified_name"),
                "start_line": item.get("start_line"),
                "end_line": item.get("end_line"),
                "snippet": item.get("content", ""),  # Map content → snippet
                "relevance_score": item.get("evidence_score", 0.0),  # Map evidence_score → relevance_score
                "types": item.get("types", []),
                "exact_match": item.get("exact_identifier_match", False),
            }
            
            transformed.append(transformed_item)
        
        return transformed
