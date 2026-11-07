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
import re
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
from backend.app.services.repository_workspace import ensure_local_copy

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
    executed_queries: list[str]  # "kind:query" keys already run in earlier rounds
    raw_search_results: dict

    # Evidence collection & ranking
    accumulated_evidence: list[dict]
    evidence_summary: dict
    search_coverage: dict  # repository-wide searches: {"references": {...}, "computations": {...}}

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
            # Subsequent rounds: use the evaluator's targeted queries, which are
            # derived from the specific missing evidence (route fragments of
            # observed client URLs, routed handler names, referenced models,
            # callers of client methods) rather than generic keywords.
            eval_data = state.get("evidence_evaluation", {})
            rec_queries = eval_data.get("recommended_queries", {})

            planned = self._unexecuted_queries(rec_queries, state.get("executed_queries", []))

        executed = list(state.get("executed_queries", []))
        for kind, queries in planned.items():
            executed.extend(f"{kind}:{query}" for query in queries)

        stage_times = dict(state.get("stage_times", {}))
        stage_times[f"plan_retrieval_round_{current_round}_seconds"] = round(time.time() - t0, 3)

        return {
            "planned_queries": planned,
            "executed_queries": executed,
            "stage_times": stage_times,
        }

    @staticmethod
    def _unexecuted_queries(recommended: dict, executed: list[str]) -> dict:
        """Recommended follow-up queries minus those already run in earlier rounds."""
        done = set(executed)
        limits = {"symbol_queries": 5, "text_queries": 5, "import_queries": 3, "caller_queries": 3}
        return {
            kind: [q for q in recommended.get(kind, []) if f"{kind}:{q}" not in done][:limit]
            for kind, limit in limits.items()
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

            # 6. Extract source context for found candidates. Only a bounded
            # number get context, so interleave the search kinds; otherwise
            # broad symbol matches use every slot and targeted text hits
            # (e.g. the client URL a follow-up searched for) are dropped.
            source_candidates = self._interleave(symbol_results, text_results, semantic_results)
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

            # 7a. Profile-specific repository-wide retrieval (round 0 only):
            # every reference site of a call-site target, the full trace of a
            # named function, or every computation of an asked quantity. Each
            # records its coverage so completeness/absence claims can be checked.
            coverage = dict(state.get("search_coverage") or {})
            if current_round == 0 and repo_path:
                try:
                    profile_context, coverage = self._profile_retrieval(db, repo_id, repo_path, state["query"], question_type, coverage)
                    # Placed first: these windows are the question's direct answer.
                    source_context = profile_context + source_context
                except Exception as exc:
                    logger.warning(f"Profile retrieval failed: {exc}")

            # 7b. Relationship traversal (bounded): client URL -> route ->
            # handler -> serializers/models, plus callers of requested symbols.
            caller_queries = planned.get("caller_queries", [])
            if repo_path and (
                caller_queries
                or question_type not in (
                    QuestionType.SYMBOL_LOOKUP.value,
                    QuestionType.DEPENDENCY_ANALYSIS.value,
                )
            ):
                # Client calls seen only in partial windows are completed to
                # their whole method first, so traversal knows which client
                # method issues the request and can find its callers.
                try:
                    completed = self.retrieval_service._complete_client_methods(
                        db, repo_id, repo_path, source_context + text_results, state["query"],
                    )
                    if isinstance(completed, list):
                        source_context.extend(completed)
                except Exception as exc:
                    logger.warning(f"Client method completion failed: {exc}")
                try:
                    traversal = self.retrieval_service._relationship_traversal(
                        db=db,
                        repository_id=repo_id,
                        repository_path=repo_path,
                        evidence_items=list(state.get("accumulated_evidence", [])) + source_context + text_results,
                        question=state["query"],
                        caller_queries=caller_queries,
                    )
                    if isinstance(traversal, list):
                        source_context.extend(traversal)
                except Exception as exc:
                    logger.warning(f"Relationship traversal failed: {exc}")

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
            "search_coverage": coverage,
            "stage_times": stage_times,
        }

    def _profile_retrieval(
        self, db: Session, repo_id: int, repo_path: str, query: str, question_type: Optional[str], coverage: dict,
    ) -> tuple[list[dict], dict]:
        from backend.app.services import evidence_signals

        evaluator = type(self.evidence_evaluation_service)
        if not hasattr(evaluator, "evaluation_profile"):
            return [], coverage
        profile = evaluator.evaluation_profile(query, question_type)
        contexts: list[dict] = []
        if profile == "call_site":
            backend_only = bool(re.search(r"\bmodels?\b|\btable\b|\borm\b|\bcrud\b", query, re.IGNORECASE))
            references = dict(coverage.get("references", {}))
            for target in evaluator._question_identifiers(query)[:2]:
                found, record = self.retrieval_service._reference_search(db, repo_id, repo_path, target, backend_only)
                contexts.extend(found)
                references[record["identifier"]] = record
            coverage = {**coverage, "references": references}
        elif profile == "symbol_trace":
            for target in evaluator.function_targets(query)[:2]:
                contexts.extend(self.retrieval_service._symbol_trace_retrieval(db, repo_id, repo_path, target))
        elif profile == "calculation":
            found, record = self.retrieval_service._computation_retrieval(
                db, repo_id, repo_path, evidence_signals.numeric_terms(query),
            )
            contexts.extend(found)
            coverage = {**coverage, "computations": {**coverage.get("computations", {}), **record}}
        return contexts, coverage

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

        # The registry may hold more than the answer prompt: the prompt is
        # capped separately (stage evidence first), so a larger per-round cap
        # keeps relationship evidence *and* the code that seeded it.
        collector_res = self.evidence_collector_service.collect(
            retrieval_results=retrieval_dict,
            max_evidence=18,
            question_type=q_enum,
        )

        new_evidence_raw = collector_res.get("evidence", [])
        new_evidence = self.retrieval_service._transform_evidence_for_response(new_evidence_raw)

        # Merge new evidence into the canonical evidence registry for this
        # investigation (deduplicating by normalised file + start_line).
        # Existing IDs never change; new items get the next free ID, and each
        # item records the repository it was retrieved from.
        accumulated = list(state.get("accumulated_evidence", []))
        seen = {
            ((item.get("file") or "").replace("\\", "/"), item.get("start_line"))
            for item in accumulated
        }

        for item in new_evidence:
            key = ((item.get("file") or "").replace("\\", "/"), item.get("start_line"))
            if key in seen or self._is_redundant_window(item, accumulated):
                continue
            seen.add(key)
            accumulated.append(item)

        for idx, item in enumerate(accumulated, start=1):
            item.setdefault("evidence_id", f"E{idx}")
            item.setdefault("repository_id", state.get("repository_id"))

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

        # The routing edge only runs round index r+1 while r+1 < max_rounds, so
        # the last executable round is max_rounds - 1. The evaluator is told
        # this so that its final verdict uses max-rounds semantics instead of
        # announcing a follow-up round that can never execute.
        last_round = max(0, max_rounds - 1)
        evaluation: EvidenceEvaluation = self.evidence_evaluation_service.evaluate(
            question=state["query"],
            evidence=evidence,
            question_type=q_type,
            current_round=current_round,
            max_rounds=last_round,
            search_coverage=state.get("search_coverage") or {},
        )

        # Bug-1 fix: Boundary enforcement must be STRICT.
        # If no further round can run, retrieval_needed MUST be False
        # regardless of what the evaluator returned. This prevents looping past the limit.
        retrieval_needed = evaluation.retrieval_needed and (current_round < last_round)

        # Another round is only useful if it would run something new.
        if retrieval_needed and not any(
            self._unexecuted_queries(evaluation.recommended_queries, state.get("executed_queries", [])).values()
        ):
            retrieval_needed = False

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
        evidence = self._order_evidence_for_answer(
            state.get("accumulated_evidence", []),
            state.get("evidence_evaluation") or {},
        )
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
            repository_id=state.get("repository_id"),
            search_coverage=state.get("search_coverage") or {},
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
        evidence = self._order_evidence_for_answer(
            state.get("accumulated_evidence", []),
            state.get("evidence_evaluation") or {},
        )
        val_dict = state.get("citation_validation", {})
        attempts = state.get("repair_attempts", 0)

        # Reconstruct CitationValidationResult
        val_result = CitationValidationResult(
            is_valid=val_dict.get("is_valid", False),
            valid_citations=answer.citations,
            invalid_citations=val_dict.get("invalid_citations", []),
            hallucinated_ids=val_dict.get("hallucinated_ids", []),
            unsupported_claims=val_dict.get("unsupported_claims", []),
            repair_needed=True,
            details=val_dict.get("details", ""),
        )

        repaired_answer = self.citation_validation_service.repair_answer(
            answer=answer,
            evidence=evidence,
            validation_result=val_result,
            repository_path=state.get("repository_path"),
            search_coverage=state.get("search_coverage") or {},
        )

        # Re-validate the repaired answer so the reported status describes
        # what is actually returned, not the pre-repair answer.
        revalidated = self.citation_validation_service.validate_answer_citations(
            answer=repaired_answer,
            evidence=evidence,
            repository_path=state.get("repository_path"),
            repository_id=state.get("repository_id"),
            search_coverage=state.get("search_coverage") or {},
        ).to_dict()
        revalidated["repair_attempted"] = True
        revalidated["pre_repair_status"] = val_dict.get("validation_status")
        revalidated["pre_repair_unsupported_claims"] = val_dict.get("unsupported_claims", [])

        stage_times = dict(state.get("stage_times", {}))
        stage_times["citation_repair_seconds"] = round(time.time() - t0, 3)

        return {
            "answer": repaired_answer,
            "citation_validation": revalidated,
            "repair_attempts": attempts + 1,
            "stage_times": stage_times,
        }

    def _finalize_response_node(self, state: InvestigationState) -> dict:
        """Node 9: Assemble final InvestigationResponse matching the required API model."""
        start_time = state.get("start_time", time.time())
        total_time = round(time.time() - start_time, 3)

        answer = state["answer"]
        answer.execution_time_seconds = total_time

        # High confidence is only allowed for an answer whose claims were
        # actually evaluated and verified against cited evidence.
        validation = state.get("citation_validation") or {}
        status = validation.get("validation_status")
        if status and status != "verified" and answer.confidence == "high":
            answer.confidence = "partial"
            note = f"Citation validation status: {status}. {validation.get('details', '')}".strip()
            if note not in answer.limitations:
                answer.limitations.append(note)

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

    @staticmethod
    def _interleave(*result_lists: list) -> list:
        """Round-robin merge of result lists, preserving each list's order."""
        merged = []
        for index in range(max((len(results) for results in result_lists), default=0)):
            for results in result_lists:
                if index < len(results):
                    merged.append(results[index])
        return merged

    MAX_CONTAINER_LINES = 80

    @classmethod
    def _is_redundant_window(cls, item: dict, accumulated: list[dict]) -> bool:
        """True when *item* repeats code already in the registry.

        Redundant: fully inside a registered item of modest size (e.g. a
        method inside its class), or overlapping a registered window by 60%+
        (e.g. adjacent route-line windows). Whole-file windows are not used as
        containers because they get truncated before reaching the LLM.
        """
        try:
            start, end = int(item.get("start_line")), int(item.get("end_line"))
        except (TypeError, ValueError):
            return False
        if end < start:
            return False

        from backend.app.services import evidence_signals

        file_key = (item.get("file") or "").replace("\\", "/")
        span = end - start + 1
        names = evidence_signals.defined_names(item)

        for other in accumulated:
            if (other.get("file") or "").replace("\\", "/") != file_key:
                continue
            try:
                o_start, o_end = int(other.get("start_line")), int(other.get("end_line"))
            except (TypeError, ValueError):
                continue
            overlap = min(end, o_end) - max(start, o_start) + 1
            if overlap <= 0:
                continue
            # A window that adds a definition (e.g. the enclosing class name
            # that a route refers to) is not redundant.
            if not names <= evidence_signals.defined_names(other):
                continue
            contained = o_start <= start and end <= o_end
            if contained and (o_end - o_start + 1) <= cls.MAX_CONTAINER_LINES:
                return True
            if not contained and overlap / span >= 0.6 and (o_end - o_start + 1) <= cls.MAX_CONTAINER_LINES:
                return True
        return False

    @staticmethod
    def _order_evidence_for_answer(evidence: list[dict], evaluation: dict) -> list[dict]:
        """Order registry evidence for the (size-capped) answer prompt.

        Evidence that establishes an evaluated stage comes first, then the rest
        by relevance with indirect files (tests/admin/migrations/docs) last.
        Without this, later targeted retrieval rounds never reach the LLM
        because the first round fills the prompt budget.
        """
        from backend.app.services import evidence_signals

        # Round-robin over stages (each stage's first item, then each second
        # item ...) so a capped prompt still covers every stage, e.g. the
        # backend total computation after many frontend subtotal sites.
        stage_ids: list[str] = []
        per_stage = [list(stage.get("evidence_ids") or []) for stage in evaluation.get("flow_stages") or []]
        for depth in range(max((len(ids) for ids in per_stage), default=0)):
            for ids in per_stage:
                if depth < len(ids) and ids[depth] not in stage_ids:
                    stage_ids.append(ids[depth])

        rank = {eid: idx for idx, eid in enumerate(stage_ids)}
        return sorted(
            evidence,
            key=lambda item: (
                0 if item.get("evidence_id") in rank else 1,
                rank.get(item.get("evidence_id"), 0),
                1 if evidence_signals.is_indirect(item.get("file")) else 0,
                -(item.get("relevance_score") or 0.0),
            ),
        )

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

        # An "insufficient" answer without citations (e.g. generation failed)
        # has nothing grounded to repair.
        answer = state.get("answer")
        if answer is not None and answer.confidence == "insufficient" and not answer.citations:
            repair_needed = False

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

        ensure_local_copy(db, repo)

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
