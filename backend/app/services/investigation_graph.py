"""
Investigation Graph

Orchestrates the complete autonomous code investigation workflow using LangGraph.
Implements typed state, nodes, conditional edges, bounded iterative retrieval loops,
and citation validation.

Workflow:
1. analyze_query: Classify question type, extract concepts & initial queries
2. plan_retrieval: Plan or refine search instructions for the current round
3. retrieve_evidence: Perform deterministic + semantic searches (enforcing repository isolation)
4. collect_rank_evidence: Merge, deduplicate, and score evidence items
5. evaluate_evidence: Evaluate evidence sufficiency and decide if another round is needed
   -- CONDITIONAL EDGE: if retrieval_needed and round < max_rounds -> loop to plan_retrieval (max 2 extra rounds)
                        else -> proceed to generate_answer
6. generate_answer: Produce evidence-grounded technical answer with inline citations
7. validate_citations: Verify all citations and inline [E#] references against actual code
   -- CONDITIONAL EDGE: if repair_needed and repair_attempts < 1 -> repair_answer
                        else -> finalize_response
8. repair_answer: Fix hallucinated or mismatched citations (max 1 attempt)
9. finalize_response: Format InvestigationResponse model with complete audit metadata
"""

import logging
import time
from typing import Any, Optional, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy.orm import Session

from backend.app.core.database import SessionLocal
from backend.app.models.investigation_answer import (
    Citation,
    InvestigationAnswer,
    InvestigationResponse,
)
from backend.app.models.query_analysis import QueryAnalysis, QuestionType
from backend.app.models.repository import Repository
from backend.app.services.answer_generation_service import AnswerGenerationService
from backend.app.services.citation_validation_service import (
    CitationValidationResult,
    CitationValidationService,
)
from backend.app.services.evidence_collector_service import EvidenceCollectorService
from backend.app.services.evidence_evaluation_service import (
    EvidenceEvaluation,
    EvidenceEvaluationService,
)
from backend.app.services.query_analyzer_service import QueryAnalyzerService
from backend.app.services.retrieval_service import RetrievalService

logger = logging.getLogger(__name__)


class InvestigationState(TypedDict, total=False):
    """Typed state passed through the LangGraph investigation workflow."""

    # Input parameters
    query: str
    repository_id: int
    repository_info: dict
    repository_path: Optional[str]

    # Retrieval loop control
    current_round: int
    max_rounds: int  # max additional rounds (default 2 -> total up to 3 rounds)

    # Query analysis
    query_analysis: Optional[dict]
    question_type: Optional[str]

    # Search planning & execution
    planned_queries: dict  # {"symbol_queries": [...], "text_queries": [...], "import_queries": [...]}
    raw_search_results: dict

    # Evidence collection & ranking
    accumulated_evidence: list[dict]
    evidence_summary: dict

    # Sufficiency evaluation
    evidence_evaluation: Optional[dict]
    retrieval_needed: bool

    # Answer generation
    answer: Optional[InvestigationAnswer]

    # Citation validation & repair
    citation_validation: Optional[dict]
    repair_attempts: int
    max_repair_attempts: int

    # Final outputs & timing
    response: Optional[InvestigationResponse]
    stage_times: dict
    start_time: float
    total_execution_time_seconds: float
    error: Optional[str]


class InvestigationGraphService:
    """Builds and executes the LangGraph workflow for repository investigations."""

    MAX_ADDITIONAL_ROUNDS = 2  # Guaranteed termination: initial round 0 + at most 2 extra rounds
    MAX_REPAIR_ATTEMPTS = 1   # Guaranteed termination: at most 1 repair attempt

    def __init__(
        self,
        retrieval_service: RetrievalService,
        query_analyzer_service: QueryAnalyzerService,
        answer_generation_service: AnswerGenerationService,
        evidence_evaluation_service: EvidenceEvaluationService,
        citation_validation_service: CitationValidationService,
        evidence_collector_service: Optional[EvidenceCollectorService] = None,
    ):
        self.retrieval_service = retrieval_service
        self.query_analyzer_service = query_analyzer_service
        self.answer_generation_service = answer_generation_service
        self.evidence_evaluation_service = evidence_evaluation_service
        self.citation_validation_service = citation_validation_service
        self.evidence_collector_service = (
            evidence_collector_service or EvidenceCollectorService()
        )

        # Build and compile the LangGraph workflow
        self.graph = self._build_graph()

    def _build_graph(self):
        """Construct the LangGraph StateGraph with nodes and conditional edges."""
        builder = StateGraph(InvestigationState)

        # 1. Add nodes
        builder.add_node("analyze_query", self._analyze_query_node)
        builder.add_node("plan_retrieval", self._plan_retrieval_node)
        builder.add_node("retrieve_evidence", self._retrieve_evidence_node)
        builder.add_node("collect_rank_evidence", self._collect_rank_evidence_node)
        builder.add_node("evaluate_evidence", self._evaluate_evidence_node)
        builder.add_node("generate_answer", self._generate_answer_node)
        builder.add_node("validate_citations", self._validate_citations_node)
        builder.add_node("repair_answer", self._repair_answer_node)
        builder.add_node("finalize_response", self._finalize_response_node)

        # 2. Add standard edges
        builder.add_edge(START, "analyze_query")
        builder.add_edge("analyze_query", "plan_retrieval")
        builder.add_edge("plan_retrieval", "retrieve_evidence")
        builder.add_edge("retrieve_evidence", "collect_rank_evidence")
        builder.add_edge("collect_rank_evidence", "evaluate_evidence")

        # 3. Add conditional edge for retrieval loop
        builder.add_conditional_edges(
            "evaluate_evidence",
            self._route_after_evaluation,
            {
                "continue_retrieval": "plan_retrieval",
                "proceed_to_answer": "generate_answer",
            },
        )

        builder.add_edge("generate_answer", "validate_citations")

        # 4. Add conditional edge for citation repair
        builder.add_conditional_edges(
            "validate_citations",
            self._route_after_citation_validation,
            {
                "repair": "repair_answer",
                "finalize": "finalize_response",
            },
        )

        builder.add_edge("repair_answer", "finalize_response")
        builder.add_edge("finalize_response", END)

        return builder.compile()

    # =========================================================================
    # NODE IMPLEMENTATIONS
    # =========================================================================

    def _analyze_query_node(self, state: InvestigationState) -> dict:
        """Node 1: Analyze query using query_analyzer_service."""
        t0 = time.time()
        query = state["query"]

        try:
            qa = self.query_analyzer_service.analyze_with_fallback(query)
            qa_dict = {
                "intent": qa.intent,
                "question_type": qa.question_type.value,
                "concepts": qa.concepts,
                "symbol_queries": qa.symbol_queries,
                "text_queries": qa.text_queries,
                "import_queries": qa.import_queries,
            }
            q_type = qa.question_type.value
        except Exception as exc:
            logger.error(f"Query analysis node failed: {exc}")
            qa_dict = {
                "intent": f"[fallback] {query}",
                "question_type": "feature_flow",
                "concepts": query.split()[:4],
                "symbol_queries": [],
                "text_queries": query.split()[:3],
                "import_queries": [],
            }
            q_type = "feature_flow"

        stage_times = dict(state.get("stage_times", {}))
        stage_times["query_analysis_seconds"] = round(time.time() - t0, 3)

        return {
            "query_analysis": qa_dict,
            "question_type": q_type,
            "stage_times": stage_times,
            "current_round": 0,
            "max_rounds": state.get("max_rounds", self.MAX_ADDITIONAL_ROUNDS),
            "repair_attempts": 0,
            "max_repair_attempts": self.MAX_REPAIR_ATTEMPTS,
            "accumulated_evidence": [],
        }

    def _plan_retrieval_node(self, state: InvestigationState) -> dict:
        """Node 2: Formulate search instructions for current round."""
        t0 = time.time()
        current_round = state.get("current_round", 0)
        qa = state.get("query_analysis", {})

        if current_round == 0:
            # Round 0: Use queries generated by QueryAnalyzer
            planned = {
                "symbol_queries": qa.get("symbol_queries", [])[:5],
                "text_queries": qa.get("text_queries", [])[:5],
                "import_queries": qa.get("import_queries", [])[:3],
            }
        else:
            # Subsequent rounds: Use recommended queries from evidence evaluation
            eval_data = state.get("evidence_evaluation", {})
            rec_queries = eval_data.get("recommended_queries", {})
            missing_stages = eval_data.get("missing_stages", [])

            sym_queries = list(rec_queries.get("symbol_queries", []))
            txt_queries = list(rec_queries.get("text_queries", []))
            imp_queries = list(rec_queries.get("import_queries", []))

            # Augment targeted searches if key stages are missing
            if "url_routing" in missing_stages and not any("urls.py" in q for q in txt_queries):
                txt_queries.append("urls.py")
            if "ui_event_handler" in missing_stages and not any("cart.ts" in q for q in txt_queries):
                txt_queries.append("cart.ts")

            planned = {
                "symbol_queries": sym_queries[:5],
                "text_queries": txt_queries[:5],
                "import_queries": imp_queries[:3],
            }

        stage_times = dict(state.get("stage_times", {}))
        stage_times[f"plan_retrieval_round_{current_round}_seconds"] = round(time.time() - t0, 3)

        return {
            "planned_queries": planned,
            "stage_times": stage_times,
        }

    def _retrieve_evidence_node(self, state: InvestigationState) -> dict:
        """Node 3: Execute searches strictly bounded by repository_id."""
        t0 = time.time()
        repo_id = state["repository_id"]
        repo_path = state.get("repository_path")
        planned = state.get("planned_queries", {})
        current_round = state.get("current_round", 0)
        question_type = state.get("question_type")

        # Open dedicated DB session with safety
        db: Session = SessionLocal()
        try:
            symbol_results = []
            text_results = []
            import_results = []
            semantic_results = []
            arch_results = []

            # 1. Symbol searches
            sym_queries = planned.get("symbol_queries", [])
            if sym_queries:
                symbol_results = self.retrieval_service._search_symbols(repo_id, sym_queries)

            # 2. Text searches (only if repo_path exists)
            txt_queries = planned.get("text_queries", [])
            if txt_queries and repo_path:
                text_results = self.retrieval_service._search_text(repo_path, txt_queries)

            # 3. Import searches
            imp_queries = planned.get("import_queries", [])
            if imp_queries:
                import_results = self.retrieval_service._search_imports(repo_id, imp_queries)

            # 4. Semantic search (use primary user query in round 0; targeted in later rounds)
            try:
                sem_query = state["query"] if current_round == 0 else " ".join(txt_queries or [state["query"]])
                semantic_results = self.retrieval_service.semantic_search_service.search(
                    db=db,
                    repository_id=repo_id,
                    query=sem_query,
                    limit=8 if current_round == 0 else 5,
                )
            except Exception as exc:
                logger.warning(f"Semantic search failed in round {current_round}: {exc}")
                semantic_results = []

            # 5. Architectural files (only on round 0 for architectural / config questions)
            if current_round == 0 and question_type in (
                QuestionType.ARCHITECTURAL_OVERVIEW.value,
                QuestionType.CROSS_CUTTING_CONCERN.value,
                QuestionType.DEPENDENCY_ANALYSIS.value,
            ) and repo_path:
                try:
                    q_enum = QuestionType(question_type)
                    arch_results = self.retrieval_service._staged_architectural_retrieval(
                        db=db,
                        repository_id=repo_id,
                        repository_path=repo_path,
                        question_type=q_enum,
                    )
                except Exception as exc:
                    logger.warning(f"Architectural retrieval failed: {exc}")
                    arch_results = []

            # 6. Extract source context for found candidates
            source_candidates = symbol_results + text_results + semantic_results
            source_context = []
            if repo_path and source_candidates:
                source_context = self.retrieval_service._build_source_context(
                    repo_path,
                    source_candidates,
                )

            # 7. Targeted flow retrieval for flow questions
            if current_round == 0 and question_type in (
                QuestionType.FEATURE_FLOW.value,
                QuestionType.END_TO_END_FLOW.value,
            ) and repo_path:
                try:
                    flow_extra = self.retrieval_service._targeted_flow_retrieval(
                        db=db,
                        repository_id=repo_id,
                        repository_path=repo_path,
                        symbol_results=symbol_results,
                        existing_source_context=source_context,
                    )
                    source_context.extend(flow_extra)
                except Exception as exc:
                    logger.warning(f"Flow retrieval failed: {exc}")

            # 8. Relationships
            relationships = []
            if symbol_results:
                try:
                    relationships = self.retrieval_service._build_relationships(
                        db=db,
                        repository_id=repo_id,
                        symbol_results=symbol_results[:8],
                    )
                except Exception as exc:
                    logger.warning(f"Relationship build failed: {exc}")

            raw_results = {
                "symbols": symbol_results,
                "text_matches": text_results,
                "imports": import_results,
                "semantic": semantic_results,
                "source_context": source_context,
                "relationships": relationships,
                "architectural_files": arch_results,
            }

        finally:
            db.close()

        stage_times = dict(state.get("stage_times", {}))
        stage_times[f"retrieve_round_{current_round}_seconds"] = round(time.time() - t0, 3)

        return {
            "raw_search_results": raw_results,
            "stage_times": stage_times,
        }

    def _collect_rank_evidence_node(self, state: InvestigationState) -> dict:
        """Node 4: Collect, merge, and rank evidence from current round into accumulated evidence."""
        t0 = time.time()
        current_round = state.get("current_round", 0)
        raw_results = state.get("raw_search_results", {})
        qa = state.get("query_analysis", {})
        q_type = state.get("question_type")

        # Prepare retrieval_results format for EvidenceCollectorService
        retrieval_dict = {
            "query": state["query"],
            "query_analysis": qa,
            "results": raw_results,
        }

        q_enum = None
        if q_type:
            try:
                q_enum = QuestionType(q_type)
            except ValueError:
                q_enum = None

        collector_res = self.evidence_collector_service.collect(
            retrieval_results=retrieval_dict,
            max_evidence=12,
            question_type=q_enum,
        )

        new_evidence_raw = collector_res.get("evidence", [])
        new_evidence = self.retrieval_service._transform_evidence_for_response(new_evidence_raw)

        # Merge new evidence with accumulated evidence (deduplicating by file + start_line)
        accumulated = list(state.get("accumulated_evidence", []))
        seen = {(item.get("file"), item.get("start_line")) for item in accumulated}

        for item in new_evidence:
            key = (item.get("file"), item.get("start_line"))
            if key not in seen:
                seen.add(key)
                accumulated.append(item)

        # Re-assign stable evidence IDs: E1, E2, ...
        for idx, item in enumerate(accumulated, start=1):
            item["evidence_id"] = f"E{idx}"

        stage_times = dict(state.get("stage_times", {}))
        stage_times[f"collect_rank_round_{current_round}_seconds"] = round(time.time() - t0, 3)

        evidence_summary = {
            "total_accumulated_evidence": len(accumulated),
            "round_evidence_count": len(new_evidence),
            "current_round": current_round,
        }

        return {
            "accumulated_evidence": accumulated,
            "evidence_summary": evidence_summary,
            "stage_times": stage_times,
        }

    def _evaluate_evidence_node(self, state: InvestigationState) -> dict:
        """Node 5: Evaluate evidence sufficiency and decide on conditional follow-up."""
        t0 = time.time()
        current_round = state.get("current_round", 0)
        max_rounds = state.get("max_rounds", self.MAX_ADDITIONAL_ROUNDS)
        evidence = state.get("accumulated_evidence", [])
        q_type = state.get("question_type")

        evaluation: EvidenceEvaluation = self.evidence_evaluation_service.evaluate(
            question=state["query"],
            evidence=evidence,
            question_type=q_type,
            current_round=current_round,
            max_rounds=max_rounds,
        )

        # Bug-1 fix: Boundary enforcement must be STRICT.
        # If current_round has already reached max_rounds, retrieval_needed MUST be False
        # regardless of what the evaluator returned. This prevents looping past the limit.
        retrieval_needed = evaluation.retrieval_needed and (current_round < max_rounds)

        stage_times = dict(state.get("stage_times", {}))
        stage_times[f"evaluate_round_{current_round}_seconds"] = round(time.time() - t0, 3)

        # Only increment round counter when we are actually going to loop.
        # The routing edge reads the updated current_round so it must reflect
        # the round index that will execute next, not the one just completed.
        next_round = current_round + 1 if retrieval_needed else current_round

        return {
            "evidence_evaluation": evaluation.model_dump(),
            "retrieval_needed": retrieval_needed,
            "current_round": next_round,
            "stage_times": stage_times,
        }

    def _generate_answer_node(self, state: InvestigationState) -> dict:
        """Node 6: Generate grounded answer using answer_generation_service."""
        t0 = time.time()
        evidence = state.get("accumulated_evidence", [])
        q_type = state.get("question_type")
        repo_name = state.get("repository_info", {}).get("name")

        try:
            answer = self.answer_generation_service.generate_answer(
                question=state["query"],
                evidence=evidence,
                question_type=q_type,
                repository_name=repo_name,
                evaluation_context=state.get("evidence_evaluation"),
            )
        except Exception as exc:
            logger.error(f"Answer generation failed: {exc}", exc_info=True)
            answer = InvestigationAnswer(
                question=state["query"],
                answer=f"Unable to generate answer due to an internal error: {str(exc)}",
                citations=[],
                confidence="insufficient",
                limitations=[str(exc)],
            )

        stage_times = dict(state.get("stage_times", {}))
        stage_times["answer_generation_seconds"] = round(time.time() - t0, 3)

        return {
            "answer": answer,
            "stage_times": stage_times,
        }

    def _validate_citations_node(self, state: InvestigationState) -> dict:
        """Node 7: Validate citations and inline [E#] references against actual evidence and repository files."""
        t0 = time.time()
        answer = state["answer"]
        evidence = state.get("accumulated_evidence", [])
        repo_path = state.get("repository_path")

        val_result: CitationValidationResult = self.citation_validation_service.validate_answer_citations(
            answer=answer,
            evidence=evidence,
            repository_path=repo_path,
        )

        stage_times = dict(state.get("stage_times", {}))
        stage_times["citation_validation_seconds"] = round(time.time() - t0, 3)

        return {
            "citation_validation": val_result.to_dict(),
            "stage_times": stage_times,
        }

    def _repair_answer_node(self, state: InvestigationState) -> dict:
        """Node 8: Repair answer if citation validation failed (max 1 attempt)."""
        t0 = time.time()
        answer = state["answer"]
        evidence = state.get("accumulated_evidence", [])
        val_dict = state.get("citation_validation", {})
        attempts = state.get("repair_attempts", 0)

        # Reconstruct CitationValidationResult
        val_result = CitationValidationResult(
            is_valid=val_dict.get("is_valid", False),
            valid_citations=answer.citations,
            invalid_citations=[],
            hallucinated_ids=val_dict.get("hallucinated_ids", []),
            unsupported_claims=val_dict.get("unsupported_claims", []),
            repair_needed=True,
            details=val_dict.get("details", ""),
        )

        repaired_answer = self.citation_validation_service.repair_answer(
            answer=answer,
            evidence=evidence,
            validation_result=val_result,
        )

        stage_times = dict(state.get("stage_times", {}))
        stage_times["citation_repair_seconds"] = round(time.time() - t0, 3)

        return {
            "answer": repaired_answer,
            "repair_attempts": attempts + 1,
            "stage_times": stage_times,
        }

    def _finalize_response_node(self, state: InvestigationState) -> dict:
        """Node 9: Assemble final InvestigationResponse matching the required API model."""
        start_time = state.get("start_time", time.time())
        total_time = round(time.time() - start_time, 3)

        answer = state["answer"]
        answer.execution_time_seconds = total_time

        repo_info = state.get("repository_info", {})
        qa = state.get("query_analysis", {})
        q_type = state.get("question_type")

        evidence_summary = {
            "evidence_count": len(state.get("accumulated_evidence", [])),
            "rounds_executed": state.get("current_round", 0) + 1,
            "evidence_evaluation": state.get("evidence_evaluation"),
            "citation_validation": state.get("citation_validation"),
            "stage_times": state.get("stage_times", {}),
        }

        response = InvestigationResponse(
            repository=repo_info,
            query=state["query"],
            question_type=q_type,
            investigation=answer,
            evidence_summary=evidence_summary,
            metadata={
                "engine": "LangGraph",
                "version": "2.0",
                "rounds_executed": state.get("current_round", 0) + 1,
                "repair_attempts": state.get("repair_attempts", 0),
                "total_time_seconds": total_time,
                "timestamp": time.time(),
            },
        )

        return {
            "response": response,
            "total_execution_time_seconds": total_time,
        }

    # =========================================================================
    # CONDITIONAL ROUTING EDGES
    # =========================================================================

    def _route_after_evaluation(self, state: InvestigationState) -> str:
        """Conditional routing: loop back for more retrieval or proceed to answer."""
        retrieval_needed = state.get("retrieval_needed", False)
        current_round = state.get("current_round", 0)
        max_rounds = state.get("max_rounds", self.MAX_ADDITIONAL_ROUNDS)

        # Bug-1 fix: use strict less-than so we don't loop past the maximum.
        # current_round is already incremented in _evaluate_evidence_node when
        # retrieval_needed is True, so it reflects the *next* round's index.
        # We must not loop if current_round has already reached max_rounds.
        if retrieval_needed and current_round < max_rounds:
            logger.info(f"Routing to continue_retrieval (round {current_round} of {max_rounds})")
            return "continue_retrieval"

        logger.info("Routing to proceed_to_answer")
        return "proceed_to_answer"

    def _route_after_citation_validation(self, state: InvestigationState) -> str:
        """Conditional routing: repair invalid citations or finalize."""
        val = state.get("citation_validation", {})
        repair_needed = val.get("repair_needed", False)
        attempts = state.get("repair_attempts", 0)
        max_attempts = state.get("max_repair_attempts", self.MAX_REPAIR_ATTEMPTS)

        if repair_needed and attempts < max_attempts:
            logger.info("Routing to repair answer citations")
            return "repair"

        logger.info("Routing to finalize response")
        return "finalize"

    # =========================================================================
    # PUBLIC EXECUTION API
    # =========================================================================

    def run(
        self,
        db: Session,
        repository_id: int,
        query: str,
        max_additional_rounds: int = 2,
    ) -> InvestigationResponse:
        """
        Execute the full LangGraph investigation workflow.

        Args:
            db: SQLAlchemy session
            repository_id: Database ID of repository (enforcing strict repository isolation)
            query: User's technical question
            max_additional_rounds: Max extra retrieval rounds (default 2)

        Returns:
            InvestigationResponse with grounded answer, citations, and full audit metadata
        """
        repo = db.get(Repository, repository_id)
        if not repo:
            raise ValueError(f"Repository with ID {repository_id} not found.")

        if not repo.local_path:
            raise ValueError(f"Repository '{repo.name}' has not been cloned.")

        repo_info = {
            "id": repo.id,
            "name": repo.name,
            "url": repo.url,
        }

        initial_state: InvestigationState = {
            "query": query.strip(),
            "repository_id": repo.id,
            "repository_info": repo_info,
            "repository_path": repo.local_path,
            "current_round": 0,
            "max_rounds": max_additional_rounds,
            "repair_attempts": 0,
            "max_repair_attempts": self.MAX_REPAIR_ATTEMPTS,
            "stage_times": {},
            "start_time": time.time(),
        }

        # Execute LangGraph
        result_state = self.graph.invoke(initial_state)

        final_response: Optional[InvestigationResponse] = result_state.get("response")
        if not final_response:
            raise RuntimeError("LangGraph execution finished without producing a response.")

        return final_response
