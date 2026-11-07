import re
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
from backend.app.services import evidence_signals
from backend.app.services.repository_workspace import ensure_local_copy


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

        ensure_local_copy(db, repository)

        if not repository.local_path:
            raise ValueError(
                "Repository has not been cloned."
            )

        # ---------------------------------------------------------
        # 1. Analyze user query — use fallback so LLM failure
        #    never aborts the whole investigation
        # ---------------------------------------------------------

        import time as _time
        _qa_start = _time.time()

        try:
            query_analysis = (
                self.query_analyzer_service.analyze_with_fallback(query)
            )
        except Exception as e:
            import logging as _log
            _log.error(f"Query analysis failed completely: {e}", exc_info=True)
            raise RuntimeError(
                f"Query analysis failed: {str(e)}"
            )

        _qa_seconds = round(_time.time() - _qa_start, 3)

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
        # 5b. Targeted follow-up retrieval for flow questions
        #
        # For feature_flow and end_to_end_flow questions the initial
        # symbol search may only surface entry points.  We augment
        # evidence by searching for the functions those symbols CALL
        # so the LLM can see the full execution path.
        # ---------------------------------------------------------

        if query_analysis.question_type in (
            QuestionType.FEATURE_FLOW,
            QuestionType.END_TO_END_FLOW,
        ):
            flow_extra = self._targeted_flow_retrieval(
                db=db,
                repository_id=repository_id,
                repository_path=repository.local_path,
                symbol_results=symbol_results,
                existing_source_context=source_context,
            )
            source_context = source_context + flow_extra

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
                "query_analysis_seconds": _qa_seconds,
                "used_fallback_analysis": query_analysis.intent.startswith("[fallback]"),
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

        # Only MAX_SOURCE_CONTEXTS candidates get source context, so runtime
        # code is served before docs/tests/admin/migrations (stable order).
        candidates = sorted(
            candidates,
            key=lambda result: evidence_signals.is_indirect(result.get("file")),
        )

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
    # TARGETED FLOW RETRIEVAL
    #
    # For feature_flow / end_to_end_flow questions we look up which
    # symbols are CALLED by the already-found symbols and retrieve
    # their source so the LLM can trace the execution path.
    # =============================================================

    def _targeted_flow_retrieval(
        self,
        db: Session,
        repository_id: int,
        repository_path: str,
        symbol_results: list[dict],
        existing_source_context: list[dict],
        max_extra_contexts: int = 6,
    ) -> list[dict]:
        """Fetch source for symbols that are called by the initial hit-set.

        Avoids re-fetching code that is already present in
        *existing_source_context*.

        Returns a list of additional source-context dicts (may be empty).
        """
        import logging
        log = logging.getLogger(__name__)

        # Build set of file+line ranges we already have so we don't duplicate
        existing_keys: set[tuple] = {
            (
                str(ctx.get("file", "")).replace("\\", "/"),
                ctx.get("start_line"),
                ctx.get("end_line"),
            )
            for ctx in existing_source_context
        }

        extra: list[dict] = []

        for hit in symbol_results[:8]:  # cap to avoid combinatorial explosion
            symbol_name = hit.get("name") or hit.get("symbol")
            if not symbol_name:
                continue

            try:
                calls = self.code_relationship_service.get_calls(
                    db=db,
                    repository_id=repository_id,
                    symbol_name=symbol_name,
                )
            except Exception as exc:
                log.debug("flow retrieval: get_calls(%s) failed: %s", symbol_name, exc)
                continue

            for call in calls:
                called_name = call.get("called_name", "")
                # Filter obviously malformed entries
                if (
                    not called_name
                    or len(called_name) <= 1
                    or any(ch in called_name for ch in "{}()[],;")
                ):
                    continue

                # Look up the symbol definition so we get file + line numbers
                try:
                    # Reuse the existing code search service via a local session
                    db2 = SessionLocal()
                    try:
                        svc = CodeSearchService()
                        matches = svc.search(
                            db=db2,
                            repository_id=repository_id,
                            query=called_name,
                        )
                    finally:
                        db2.close()
                except Exception as exc:
                    log.debug(
                        "flow retrieval: symbol search for '%s' failed: %s",
                        called_name,
                        exc,
                    )
                    continue

                for match in matches[:2]:  # at most 2 definitions per called name
                    file_path = match.get("file")
                    start_line = match.get("start_line")
                    end_line = match.get("end_line")

                    if not file_path or start_line is None:
                        continue

                    key = (
                        str(file_path).replace("\\", "/"),
                        start_line,
                        end_line,
                    )
                    if key in existing_keys:
                        continue

                    existing_keys.add(key)

                    try:
                        src = self.code_retrieval_service.get_source(
                            repository_path=repository_path,
                            file_path=file_path,
                            start_line=start_line,
                            end_line=end_line if end_line else start_line + 30,
                        )
                        extra.append(src)
                    except (FileNotFoundError, ValueError, OSError) as exc:
                        log.debug(
                            "flow retrieval: get_source(%s) failed: %s", file_path, exc
                        )

                    if len(extra) >= max_extra_contexts:
                        return extra

        return extra

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
            repository_path=repository_path,
        )
        
        evidence = []
        seen_paths = set()
        
        if question_type == QuestionType.ARCHITECTURAL_OVERVIEW:
            # For architectural overview: README + one entry point per
            # top-level area (e.g. backend/ and frontend/) + configuration +
            # route registrations (urls/routes before views) + models.
            entry_points = []
            seen_areas = set()
            for repo_file in architectural_files["entry_points"]:
                area = evidence_signals.normalize_path(repo_file.path).split("/")[0]
                if area not in seen_areas:
                    seen_areas.add(area)
                    entry_points.append(repo_file)

            routes = sorted(
                architectural_files["routes"],
                key=lambda f: evidence_signals.normalize_path(f.path).lower().endswith("views.py"),
            )

            priority_order = [
                architectural_files["readme"][:1],  # Top README only
                entry_points[:3],
                architectural_files["configuration"][:2],
                routes[:2],
                architectural_files["models"][:1],  # Top model file
                architectural_files["dependencies"][:2],  # Top 2 dependency files
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

                    # Long configuration files get truncated before the LLM
                    # sees their database/auth sections, so add focused
                    # windows around those settings.
                    if file_category == "configuration":
                        evidence.extend(
                            self._focused_config_windows(
                                repository_path,
                                repo_file.path,
                            )
                        )

                except (FileNotFoundError, ValueError, OSError) as e:
                    import logging
                    logging.warning(f"Failed to extract architectural file {repo_file.path}: {e}")
                    continue

        return evidence

    _CONFIG_KEY_RE = re.compile(
        r"^\s*(DATABASES|DATABASE_URL|SQLALCHEMY_DATABASE_URI|REST_FRAMEWORK|"
        r"AUTHENTICATION_BACKENDS|SIMPLE_JWT|INSTALLED_APPS|CORS_ALLOWED_ORIGINS)\b"
    )

    def _focused_config_windows(
        self,
        repository_path: str,
        file_path: str,
        max_windows: int = 3,
    ) -> list[dict]:
        from pathlib import Path

        try:
            lines = (Path(repository_path) / file_path).read_text(
                encoding="utf-8"
            ).splitlines()
        except (OSError, UnicodeDecodeError):
            return []

        priority = (
            "DATABASES", "DATABASE_URL", "SQLALCHEMY_DATABASE_URI",
            "REST_FRAMEWORK", "AUTHENTICATION_BACKENDS", "SIMPLE_JWT",
            "INSTALLED_APPS", "CORS_ALLOWED_ORIGINS",
        )
        hits = []
        for index, line in enumerate(lines, start=1):
            match = self._CONFIG_KEY_RE.match(line)
            if match:
                hits.append((priority.index(match.group(1)), index))

        windows = []
        for _, index in sorted(hits):
            try:
                window = self.code_retrieval_service.get_source(
                    repository_path=repository_path,
                    file_path=file_path,
                    start_line=index,
                    end_line=min(len(lines), index + 14),
                )
            except (FileNotFoundError, ValueError, OSError):
                continue
            window["architectural_category"] = "configuration"
            windows.append(window)
            if len(windows) >= max_windows:
                break

        return windows

    # =============================================================
    # RELATIONSHIP TRAVERSAL
    #
    # Follows concrete code relationships instead of keywords:
    #   frontend HTTP call -> matching backend route registration
    #   route -> handler definition -> serializers / models it uses
    #   symbol -> code that references it (callers)
    # Every lookup is scoped to repository_id / repository_path.
    # =============================================================

    MAX_TRAVERSAL_CONTEXTS = 10
    _FRAMEWORK_NAMES = {
        "Response", "APIView", "View", "IsAuthenticated", "AllowAny", "Http404",
        "HttpResponse", "JsonResponse", "Q", "F", "None", "True", "False",
        "Exception", "ValueError", "Decimal", "Optional", "Any",
    }

    def _relationship_traversal(
        self,
        db: Session,
        repository_id: int,
        repository_path: str,
        evidence_items: list[dict],
        question: str,
        caller_queries: list[str] | None = None,
    ) -> list[dict]:
        """Return extra source contexts reached by following code relationships."""
        import logging
        log = logging.getLogger(__name__)

        terms = evidence_signals.domain_terms(question)
        known = {
            (
                evidence_signals.normalize_path(item.get("file")),
                item.get("start_line"),
            )
            for item in evidence_items
        }
        extra: list[dict] = []

        def add(context: dict | None, reason: str) -> None:
            if not context or len(extra) >= self.MAX_TRAVERSAL_CONTEXTS:
                return
            key = (
                evidence_signals.normalize_path(context.get("file")),
                context.get("start_line"),
            )
            if key in known:
                return
            known.add(key)
            context["traversal"] = reason
            extra.append(context)

        # 1. Callers / references the evaluator explicitly asked for. These
        # fill a specific missing stage, so they run before expansion uses
        # up the context budget.
        for name in (caller_queries or [])[:3]:
            try:
                for context in self._find_references(
                    db, repository_id, repository_path, name, limit=3
                ):
                    add(context, f"reference to {name}")
            except Exception as exc:
                log.warning("Reference lookup for %s failed: %s", name, exc)

        # 2. Frontend HTTP calls -> backend route registrations -> handlers
        handler_names: list[str] = []
        seen_paths: set[tuple] = set()

        calls = [
            call
            for item in evidence_items
            if evidence_signals.is_frontend_runtime(item.get("file"))
            for call in evidence_signals.extract_http_calls(
                evidence_signals.evidence_text(item)
            )
        ]

        for call in evidence_signals.rank_http_calls(calls, terms, question):
            segments = tuple(call["segments"])
            if segments in seen_paths:
                continue
            seen_paths.add(segments)

            if len(seen_paths) > 4:
                break

            for route_hit in self._find_route_registrations(
                repository_path,
                list(segments),
            ):
                add(route_hit["context"], f"route for {call['method'].upper()} {call['path']}")
                if route_hit["handler"] not in handler_names:
                    handler_names.append(route_hit["handler"])

        # Handlers already known from route evidence in the current set.
        for item in evidence_items:
            if evidence_signals.is_frontend_runtime(item.get("file")):
                continue
            for route in evidence_signals.extract_routes(
                evidence_signals.evidence_text(item)
            ):
                joined = "/".join(route["segments"]) + route["handler"].lower()
                if (
                    route["handler"] not in handler_names
                    and (not terms or any(evidence_signals._stem(t) in joined for t in terms))
                ):
                    handler_names.append(route["handler"])

        # 2b. Callers of the client methods that issue those requests (the
        # UI side of the flow), resolved in the same round.
        caller_methods: list[str] = []
        for call in evidence_signals.rank_http_calls(calls, terms, question)[:2]:
            name = call.get("enclosing")
            if name and name[0].islower() and name not in caller_methods and name not in (caller_queries or []):
                caller_methods.append(name)
        for name in caller_methods[:2]:
            try:
                # Many components may call a shared client method; keep the
                # callers most related to the question (e.g. quantity buttons).
                callers = [
                    context for context in self._find_references(
                        db, repository_id, repository_path, name, limit=6
                    )
                    if evidence_signals.is_frontend_runtime(context.get("file"))
                ]
                callers.sort(key=lambda context: -sum(evidence_signals.relevance_hits(context, terms)))
                for context in callers[:2]:
                    add(context, f"caller of {name}")
            except Exception as exc:
                log.warning("Caller lookup for %s failed: %s", name, exc)

        handler_sources: list[str] = []
        for handler in handler_names[:4]:
            context = self._definition_context(
                db, repository_id, repository_path, handler, max_lines=70
            )
            if context:
                handler_sources.append(context.get("source", ""))
                add(context, f"handler {handler}")

        # 3. Handler dependencies (serializers, models, services)
        deps: list[str] = []
        for source in handler_sources:
            for name in re.findall(r"\b[A-Z][A-Za-z0-9]+\b", source):
                if (
                    name not in self._FRAMEWORK_NAMES
                    and name not in handler_names
                    and name not in deps
                ):
                    deps.append(name)

        for name in deps[:8]:
            if len(extra) >= self.MAX_TRAVERSAL_CONTEXTS:
                break
            context = self._definition_context(
                db,
                repository_id,
                repository_path,
                name,
                max_lines=40,
                backend_only=True,
            )
            add(context, f"dependency {name}")

        return extra

    def _find_route_registrations(
        self,
        repository_path: str,
        client_segments: list[str],
    ) -> list[dict]:
        """Locate backend route lines whose pattern matches a client URL."""
        hits = []
        seen = set()

        for term in evidence_signals.route_search_terms(client_segments)[:2]:
            if hits:
                break
            matches = self.repository_search_service.search_text(
                repository_path=repository_path,
                query=term,
                max_results=80,
            )

            for match in matches:
                file_path = match.get("file")
                if (
                    evidence_signals.is_frontend_runtime(file_path)
                    or evidence_signals.is_indirect(file_path)
                ):
                    continue

                for route in evidence_signals.extract_routes(match.get("text", "")):
                    if not evidence_signals.url_matches(client_segments, route["segments"]):
                        continue
                    key = (file_path, match.get("line"))
                    if key in seen:
                        continue
                    seen.add(key)

                    try:
                        context = self.code_retrieval_service.get_source(
                            repository_path=repository_path,
                            file_path=file_path,
                            start_line=max(1, match["line"] - 2),
                            end_line=match["line"] + 2,
                        )
                    except (FileNotFoundError, ValueError, OSError):
                        continue

                    hits.append({
                        "handler": route["handler"],
                        "context": context,
                        "specificity": len(route["segments"]),
                    })

        # Longest route match first (most specific).
        hits.sort(key=lambda hit: -hit["specificity"])
        return hits[:2]

    def _definition_context(
        self,
        db: Session,
        repository_id: int,
        repository_path: str,
        name: str,
        max_lines: int = 60,
        backend_only: bool = False,
    ) -> dict | None:
        """Source of the exact definition of *name* in this repository, if any."""
        try:
            matches = self.code_search_service.search(
                db=db,
                repository_id=repository_id,
                query=name,
            )
        except Exception:
            return None

        exact = [
            m for m in matches
            if m.get("name") == name
            and not evidence_signals.is_indirect(m.get("file"))
            and (not backend_only or not evidence_signals.is_frontend_runtime(m.get("file")))
        ]
        exact.sort(key=lambda m: m.get("symbol_type") not in ("class", "interface"))

        for match in exact[:1]:
            start = match.get("start_line")
            end = match.get("end_line") or start
            if not start:
                continue
            try:
                context = self.code_retrieval_service.get_source(
                    repository_path=repository_path,
                    file_path=match["file"],
                    start_line=start,
                    end_line=min(end, start + max_lines),
                )
            except (FileNotFoundError, ValueError, OSError):
                continue
            context["symbol"] = match.get("name")
            context["symbol_type"] = match.get("symbol_type")
            context["qualified_name"] = match.get("qualified_name")
            return context

        return None

    def _find_references(
        self,
        db: Session,
        repository_id: int,
        repository_path: str,
        identifier: str,
        limit: int = 3,
    ) -> list[dict]:
        """Code that references *identifier*, expanded to its enclosing symbol.

        Uses text search for the references and the repository's symbol table
        to resolve each reference line to the method/function that contains it.
        Works for languages whose call edges are not indexed.
        """
        from sqlalchemy import select

        from backend.app.models.code_symbol import CodeSymbol
        from backend.app.models.repository_file import RepositoryFile

        short = identifier.split(".")[-1]
        if len(short) < 3:
            return []

        word = re.compile(rf"\b{re.escape(short)}\b")
        definition = re.compile(
            rf"^\s*(?:export\s+)?(?:class|interface|def|function|async\s+def)\s+{re.escape(short)}\b"
        )
        import_line = re.compile(r"^\s*(?:from\s+\S+\s+import|import\b)")

        # Method/function names are common words ("login"); search for the
        # member-call form so only actual call sites are returned.
        query = f".{short}(" if short[0].islower() else short

        matches = self.repository_search_service.search_text(
            repository_path=repository_path,
            query=query,
            max_results=200,
        )

        by_file: dict[str, list[int]] = {}
        for match in matches:
            text = match.get("text", "")
            file_path = match.get("file")
            if (
                not word.search(text)
                or definition.search(text)
                or import_line.search(text)
                or evidence_signals.is_indirect(file_path)
            ):
                continue
            by_file.setdefault(file_path, []).append(match["line"])

        contexts: list[dict] = []
        seen: set[tuple] = set()

        for file_path, lines in by_file.items():
            variants = {
                file_path,
                file_path.replace("\\", "/"),
                file_path.replace("/", "\\"),
            }
            symbols = db.execute(
                select(CodeSymbol)
                .join(RepositoryFile, CodeSymbol.file_id == RepositoryFile.id)
                .where(
                    RepositoryFile.repository_id == repository_id,
                    RepositoryFile.path.in_(variants),
                )
            ).scalars().all()

            for line in lines:
                # Definition lines were already excluded, so a caller that
                # shares the callee's name (Login.login -> auth.login) is valid.
                enclosing = [
                    s for s in symbols
                    if s.start_line <= line <= s.end_line
                ]
                # Smallest enclosing range; on ties prefer the qualified method
                # entry (e.g. CartAddView.post) over a bare duplicate (post).
                enclosing.sort(key=lambda s: (
                    s.end_line - s.start_line,
                    "." not in (s.qualified_name or ""),
                ))
                if enclosing:
                    sym = enclosing[0]
                    start, end = sym.start_line, min(sym.end_line, sym.start_line + 50)
                else:
                    sym = None
                    start, end = max(1, line - 5), line + 5

                if (file_path, start) in seen:
                    continue
                seen.add((file_path, start))

                try:
                    context = self.code_retrieval_service.get_source(
                        repository_path=repository_path,
                        file_path=file_path,
                        start_line=start,
                        end_line=end,
                    )
                except (FileNotFoundError, ValueError, OSError):
                    continue

                if sym is not None:
                    context["symbol"] = sym.name
                    context["symbol_type"] = sym.symbol_type
                    context["qualified_name"] = sym.qualified_name

                contexts.append(context)
                if len(contexts) >= limit:
                    return contexts

        return contexts

    @staticmethod
    def _file_symbols(db: Session, repository_id: int, file_path: str) -> list:
        from sqlalchemy import select

        from backend.app.models.code_symbol import CodeSymbol
        from backend.app.models.repository_file import RepositoryFile

        variants = {file_path, file_path.replace("\\", "/"), file_path.replace("/", "\\")}
        return db.execute(
            select(CodeSymbol)
            .join(RepositoryFile, CodeSymbol.file_id == RepositoryFile.id)
            .where(
                RepositoryFile.repository_id == repository_id,
                RepositoryFile.path.in_(variants),
            )
        ).scalars().all()

    @staticmethod
    def _enclosing_symbol(symbols: list, line: int):
        """Smallest symbol containing *line*, preferring qualified method entries on ties."""
        enclosing = [s for s in symbols if s.start_line <= line <= s.end_line]
        enclosing.sort(key=lambda s: (s.end_line - s.start_line, "." not in (s.qualified_name or "")))
        return enclosing[0] if enclosing else None

    MAX_REFERENCE_SITES = 14
    REFERENCE_SEARCH_LIMIT = 2000

    def _reference_search(
        self,
        db: Session,
        repository_id: int,
        repository_path: str,
        identifier: str,
        backend_only: bool = False,
    ) -> tuple[list[dict], dict]:
        """Repository-wide references to *identifier*: compact windows + a coverage record.

        Each runtime reference site (enclosing symbol) yields a small window
        around its reference lines. The coverage record lists every site and
        whether the search was complete (not truncated by any limit).
        """
        short = identifier.split(".")[-1]
        word = re.compile(rf"\b{re.escape(short)}\b")
        definition = re.compile(rf"^\s*(?:export\s+)?(?:class|interface|def|function|type)\s+{re.escape(short)}\b")
        import_line = re.compile(r"^\s*(?:from\s+\S+\s+import|import\b)")

        matches = self.repository_search_service.search_text(
            repository_path=repository_path, query=short, max_results=self.REFERENCE_SEARCH_LIMIT,
        )
        truncated = len(matches) >= self.REFERENCE_SEARCH_LIMIT
        by_file: dict[str, list[int]] = {}
        indirect_lines = total_lines = 0
        for match in matches:
            text, file_path = match.get("text", ""), match.get("file")
            if not word.search(text) or definition.search(text) or import_line.search(text):
                continue
            if evidence_signals.is_indirect(file_path):
                indirect_lines += 1
                continue
            if backend_only and evidence_signals.is_frontend_runtime(file_path):
                continue
            total_lines += 1
            by_file.setdefault(file_path, []).append(match["line"])

        sites: list[dict] = []
        for file_path, lines in by_file.items():
            symbols = self._file_symbols(db, repository_id, file_path)
            grouped: dict[tuple, dict] = {}
            for line in sorted(lines):
                sym = self._enclosing_symbol(symbols, line)
                key = (sym.start_line, sym.end_line) if sym else (line, line)
                site = grouped.setdefault(key, {
                    "file": evidence_signals.normalize_path(file_path),
                    "raw_file": file_path,
                    "line": line,
                    "lines": [],
                    "symbol": (sym.qualified_name or sym.name) if sym else None,
                    "symbol_type": sym.symbol_type if sym else None,
                    "bounds": (sym.start_line, sym.end_line) if sym else (max(1, line - 3), line + 3),
                })
                site["lines"].append(line)
            sites.extend(grouped.values())

        contexts = []
        for site in sites[: self.MAX_REFERENCE_SITES]:
            lo, hi = site["bounds"]
            # Whole method when small (so every operation on the reference,
            # e.g. get_or_create -> save/delete, is visible); otherwise the
            # header (when near) through a few lines after the last reference.
            if hi - lo <= 40:
                start, end = lo, hi
            else:
                start = lo if site["lines"][0] - lo <= 12 else max(lo, site["lines"][0] - 4)
                end = min(hi, site["lines"][-1] + 8)
            try:
                context = self.code_retrieval_service.get_source(
                    repository_path=repository_path, file_path=site["raw_file"], start_line=start, end_line=end,
                )
            except (FileNotFoundError, ValueError, OSError):
                continue
            if site["symbol"]:
                context["symbol"] = site["symbol"].split(".")[-1]
                context["qualified_name"] = site["symbol"]
                context["symbol_type"] = site["symbol_type"]
            context["traversal"] = f"reference to {short}"
            contexts.append(context)

        coverage = {
            "identifier": short,
            "total_lines": total_lines,
            "indirect_lines": indirect_lines,
            "sites": [{"file": s["file"], "line": s["line"], "symbol": s["symbol"]} for s in sites],
            "complete": not truncated and len(sites) <= self.MAX_REFERENCE_SITES,
        }
        return contexts, coverage

    def _symbol_trace_retrieval(
        self,
        db: Session,
        repository_id: int,
        repository_path: str,
        name: str,
    ) -> list[dict]:
        """Full definition of a function, the definitions of helpers it calls, and its callers."""
        contexts: list[dict] = []
        definition = self._definition_context(db, repository_id, repository_path, name, max_lines=150)
        if not definition:
            return contexts
        definition["traversal"] = f"definition of {name}"
        contexts.append(definition)
        body = evidence_signals.method_body(definition.get("source") or definition.get("content") or "", name) or ""
        for helper in [h for h in evidence_signals.helper_calls(body) if h != name][:4]:
            context = self._definition_context(db, repository_id, repository_path, helper, max_lines=80)
            if context:
                context["traversal"] = f"helper {helper} called by {name}"
                contexts.append(context)
        for context in self._find_references(db, repository_id, repository_path, name, limit=2):
            context["traversal"] = f"caller of {name}"
            contexts.append(context)
        return contexts

    def _computation_retrieval(
        self,
        db: Session,
        repository_id: int,
        repository_path: str,
        terms: list[str],
        max_contexts: int = 8,
    ) -> tuple[list[dict], dict]:
        """Code computing each quantity (subtotal, total ...), frontend and backend, repository-wide."""
        coverage: dict[str, dict] = {}
        hits: list[tuple[str, int, str]] = []
        for term in terms[:4]:
            matches = self.repository_search_service.search_text(
                repository_path=repository_path, query=term, max_results=self.REFERENCE_SEARCH_LIMIT,
            )
            record = {"frontend": [], "backend": [], "complete": len(matches) < self.REFERENCE_SEARCH_LIMIT}
            for match in matches:
                file_path = match.get("file")
                if evidence_signals.is_indirect(file_path) or not evidence_signals.computations(match.get("text", ""), term):
                    continue
                side = "frontend" if evidence_signals.is_frontend_runtime(file_path) else "backend"
                record[side].append({"file": evidence_signals.normalize_path(file_path), "line": match["line"]})
                hits.append((file_path, match["line"], side))
            coverage[term] = record

        contexts: list[dict] = []
        seen: set[tuple] = set()
        symbols_cache: dict[str, list] = {}
        # Alternate sides so both frontend and backend calculations are kept.
        ordered = [h for h in hits if h[2] == "frontend"], [h for h in hits if h[2] == "backend"]
        merged = [h for pair in zip(*ordered) for h in pair] + ordered[0][len(ordered[1]):] + ordered[1][len(ordered[0]):]
        for file_path, line, _ in merged:
            if len(contexts) >= max_contexts:
                break
            symbols = symbols_cache.setdefault(file_path, self._file_symbols(db, repository_id, file_path))
            sym = self._enclosing_symbol(symbols, line)
            start, end = (sym.start_line, min(sym.end_line, sym.start_line + 60)) if sym else (max(1, line - 6), line + 6)
            if (file_path, start) in seen:
                continue
            seen.add((file_path, start))
            try:
                context = self.code_retrieval_service.get_source(
                    repository_path=repository_path, file_path=file_path, start_line=start, end_line=end,
                )
            except (FileNotFoundError, ValueError, OSError):
                continue
            if sym is not None:
                context["symbol"] = sym.name
                context["symbol_type"] = sym.symbol_type
                context["qualified_name"] = sym.qualified_name
            context["traversal"] = "computation"
            contexts.append(context)
        return contexts, coverage

    def _complete_client_methods(
        self,
        db: Session,
        repository_id: int,
        repository_path: str,
        evidence_items: list[dict],
        question: str,
    ) -> list[dict]:
        """Whole enclosing methods of the flow's client HTTP calls seen only in partial windows."""
        terms = evidence_signals.domain_terms(question)
        out = []
        for item in evidence_items:
            if not evidence_signals.is_frontend_runtime(item.get("file")):
                continue
            text = evidence_signals.evidence_text(item)
            calls = evidence_signals.rank_http_calls(evidence_signals.extract_http_calls(text), terms, question)
            for call in calls:
                # Complete when the enclosing method's header or end is outside the window.
                if call.get("enclosing") and evidence_signals.method_is_complete(text, call["enclosing"]):
                    continue
                try:
                    line = int(item.get("start_line") or 1) + text[: call["position"]].count("\n")
                except (TypeError, ValueError):
                    continue
                sym = self._enclosing_symbol(self._file_symbols(db, repository_id, item["file"]), line)
                if not sym:
                    continue
                try:
                    context = self.code_retrieval_service.get_source(
                        repository_path=repository_path, file_path=item["file"],
                        start_line=sym.start_line, end_line=min(sym.end_line, sym.start_line + 50),
                    )
                except (FileNotFoundError, ValueError, OSError):
                    continue
                context["symbol"], context["symbol_type"], context["qualified_name"] = sym.name, sym.symbol_type, sym.qualified_name
                context["traversal"] = f"enclosing method of {call['method'].upper()} {call['path']}"
                out.append(context)
        return out[:3]

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
