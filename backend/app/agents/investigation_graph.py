"""
Investigation Graph
===================
LangGraph workflow for evidence-grounded repository investigation.

Nodes
-----
analyze_query        → Classify question type, extract search terms.
plan_retrieval       → Decide which search strategies to run.
retrieve_evidence    → Execute all searches (delegates to RetrievalService).
collect_evidence     → Rank and merge evidence (EvidenceCollectorService).
evaluate_evidence    → Decide if evidence is sufficient (EvidenceEvaluator).
generate_answer      → LLM answer grounded in evidence (AnswerGenerationService).
validate_citations   → Verify every citation (CitationValidator).
finalize             → Package the API-compatible InvestigationResponse.

Edges
-----
evaluate_evidence → "sufficient"       → generate_answer
evaluate_evidence → "needs_more"       → retrieve_evidence   (bounded loop)
evaluate_evidence → "insufficient"     → generate_answer     (with gaps noted)

The loop is bounded by GraphState.retrieval_round ≤ MAX_EXTRA_ROUNDS (2).
After the limit the workflow continues to generate_answer regardless of gaps.
"""

from __future__ import annotations

import logging
import time
from typing import Annotated, Literal, TypedDict

from langgraph.graph import END, StateGraph

from backend.app.agents.citation_validator import CitationValidator, ValidationResult
from backend.app.agents.evidence_evaluator import EvidenceEvaluation, EvidenceEvaluator
from backend.app.models.investigation_answer import InvestigationAnswer, InvestigationResponse

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Graph state
# ---------------------------------------------------------------------------

class GraphState(TypedDict, total=False):
    # ── inputs (set before graph.invoke) ──────────────────────────────────
    query: str
    repository_id: int
    repository_path: str
    repository_name: str
    repository_url: str
    db: object                          # SQLAlchemy Session

    # ── per-round retrieval context ────────────────────────────────────────
    retrieval_round: int                # 0 = initial, 1/2 = supplemental
    extra_search_targets: list[str]     # targets from EvidenceEvaluator

    # ── intermediate results ───────────────────────────────────────────────
    retrieval_result: dict              # raw from RetrievalService.retrieve()
    evidence: list[dict]                # transformed + ranked evidence
    question_type: str
    query_analysis: dict

    # ── evaluation ────────────────────────────────────────────────────────
    evaluation: EvidenceEvaluation
    evaluation_context: dict            # missing_stages, unverified_connections

    # ── answer generation ─────────────────────────────────────────────────
    answer: InvestigationAnswer

    # ── citation validation ───────────────────────────────────────────────
    validation_result: ValidationResult

    # ── output ────────────────────────────────────────────────────────────
    response: InvestigationResponse
    error: str
    stage_times: dict                   # {stage_name: seconds}


# ---------------------------------------------------------------------------
# Node implementations
# ---------------------------------------------------------------------------

def _node_analyze_query(state: GraphState, *, services: dict) -> dict:
    """Run query analysis. Falls back deterministically on LLM failure."""
    t0 = time.perf_counter()

    query = state["query"]
    qa_service = services["query_analyzer_service"]

    try:
        analysis = qa_service.analyze_with_fallback(query)
        question_type = analysis.question_type.value
        query_analysis = {
            "intent": analysis.intent,
            "question_type": question_type,
            "concepts": analysis.concepts,
            "symbol_queries": analysis.symbol_queries,
            "text_queries": analysis.text_queries,
            "import_queries": analysis.import_queries,
        }
    except Exception as exc:
        logger.error("Query analysis failed: %s", exc, exc_info=True)
        question_type = "feature_flow"
        query_analysis = {
            "intent": f"[fallback] {query[:120]}",
            "question_type": question_type,
            "concepts": [],
            "symbol_queries": [],
            "text_queries": [query],
            "import_queries": [],
        }

    times = dict(state.get("stage_times") or {})
    times["analyze_query"] = round(time.perf_counter() - t0, 3)
    return {
        "question_type": question_type,
        "query_analysis": query_analysis,
        "retrieval_round": 0,
        "extra_search_targets": [],
        "stage_times": times,
    }


def _node_retrieve_evidence(state: GraphState, *, services: dict) -> dict:
    """Run retrieval. On supplemental rounds, inject extra search targets."""
    t0 = time.perf_counter()

    retrieval_service = services["retrieval_service"]
    db = state["db"]
    repository_id = state["repository_id"]
    retrieval_round = state.get("retrieval_round", 0)
    extra_targets = state.get("extra_search_targets", [])

    # On extra rounds, augment the query with targeted terms
    base_query = state["query"]
    if retrieval_round > 0 and extra_targets:
        augmented = f"{base_query} {' '.join(extra_targets[:4])}"
        logger.info(
            "Supplemental retrieval round %d, augmented query: %s",
            retrieval_round, augmented[:80]
        )
    else:
        augmented = base_query

    try:
        result = retrieval_service.retrieve(
            db=db,
            repository_id=repository_id,
            query=augmented,
            semantic_limit=10,
        )
    except Exception as exc:
        logger.error("Retrieval failed: %s", exc, exc_info=True)
        result = {
            "query": base_query,
            "repository": {"id": repository_id, "name": state.get("repository_name", ""), "url": state.get("repository_url", "")},
            "query_analysis": state.get("query_analysis", {}),
            "evidence": [],
            "raw_results": {},
            "summary": {},
        }

    # Merge evidence from previous rounds (dedup by file+line range)
    prev_evidence = list(state.get("evidence") or [])
    new_evidence = result.get("evidence", [])
    merged = _merge_evidence(prev_evidence, new_evidence)

    times = dict(state.get("stage_times") or {})
    times[f"retrieve_round_{retrieval_round}"] = round(time.perf_counter() - t0, 3)
    return {
        "retrieval_result": result,
        "evidence": merged,
        "stage_times": times,
    }


def _node_evaluate_evidence(state: GraphState, *, services: dict) -> dict:
    """Evaluate whether evidence is sufficient for the query type."""
    t0 = time.perf_counter()

    evaluator: EvidenceEvaluator = services["evidence_evaluator"]
    evidence = state.get("evidence") or []
    question_type = state.get("question_type") or "feature_flow"
    retrieval_round = state.get("retrieval_round", 0)

    evaluation = evaluator.evaluate(
        question=state["query"],
        question_type=question_type,
        evidence=evidence,
        retrieval_round=retrieval_round,
    )

    eval_context: dict = {}
    if evaluation.missing_stages:
        eval_context["missing_stages"] = evaluation.missing_stages
    if evaluation.unverified_connections:
        eval_context["unverified_connections"] = evaluation.unverified_connections

    times = dict(state.get("stage_times") or {})
    times[f"evaluate_round_{retrieval_round}"] = round(time.perf_counter() - t0, 3)
    return {
        "evaluation": evaluation,
        "evaluation_context": eval_context,
        "stage_times": times,
    }


def _route_after_evaluation(state: GraphState) -> Literal["retrieve_evidence", "generate_answer"]:
    """
    Conditional edge: decide whether to retry retrieval or proceed to answer.

    Rules
    -----
    * needs_more_evidence + round < MAX → retrieve again
    * sufficient, insufficient, or round limit reached → generate answer
    """
    evaluation: EvidenceEvaluation = state.get("evaluation")
    retrieval_round = state.get("retrieval_round", 0)
    evaluator_max = 2  # must match EvidenceEvaluator.MAX_RETRIEVAL_ROUNDS

    if (
        evaluation
        and evaluation.status == "needs_more_evidence"
        and retrieval_round < evaluator_max
    ):
        logger.info(
            "Routing to additional retrieval (round %d), missing: %s",
            retrieval_round + 1,
            evaluation.missing_information,
        )
        return "retrieve_evidence"

    return "generate_answer"


def _increment_round(state: GraphState) -> dict:
    """Node that increments retrieval_round and injects new search targets."""
    evaluation: EvidenceEvaluation = state.get("evaluation")
    new_targets = evaluation.next_search_targets if evaluation else []
    return {
        "retrieval_round": state.get("retrieval_round", 0) + 1,
        "extra_search_targets": new_targets,
    }


def _node_generate_answer(state: GraphState, *, services: dict) -> dict:
    """Generate an evidence-grounded answer using the LLM."""
    t0 = time.perf_counter()

    answer_service = services["answer_generation_service"]
    evidence = state.get("evidence") or []
    question_type = state.get("question_type")
    evaluation: EvidenceEvaluation | None = state.get("evaluation")
    eval_context: dict = state.get("evaluation_context") or {}

    # Build evaluation context for the prompt (informs the LLM about gaps)
    if evaluation:
        if evaluation.missing_stages and "missing_stages" not in eval_context:
            eval_context["missing_stages"] = evaluation.missing_stages
        if evaluation.unverified_connections and "unverified_connections" not in eval_context:
            eval_context["unverified_connections"] = evaluation.unverified_connections

    try:
        answer = answer_service.generate_answer(
            question=state["query"],
            evidence=evidence,
            question_type=question_type,
            repository_name=state.get("repository_name"),
            evaluation_context=eval_context or None,
        )
    except Exception as exc:
        logger.error("Answer generation failed: %s", exc, exc_info=True)
        from backend.app.models.investigation_answer import InvestigationAnswer
        answer = InvestigationAnswer(
            question=state["query"],
            answer=f"Answer generation failed: {exc}",
            citations=[],
            confidence="insufficient",
            limitations=[str(exc)],
        )

    times = dict(state.get("stage_times") or {})
    times["generate_answer"] = round(time.perf_counter() - t0, 3)
    return {"answer": answer, "stage_times": times}


def _node_validate_citations(state: GraphState, *, services: dict) -> dict:
    """Validate all citations against the repository source."""
    t0 = time.perf_counter()

    validator: CitationValidator = services["citation_validator"]
    answer: InvestigationAnswer = state.get("answer")
    evidence = state.get("evidence") or []
    repository_path = state.get("repository_path") or ""

    # Rebuild prepared evidence map expected by validator
    # (answer_generation_service already prepared these, but we need the same IDs)
    prepared = _rebuild_prepared_evidence(evidence)

    if answer is None:
        validation = ValidationResult(status="not_evaluated")
    else:
        try:
            validation = validator.validate(
                answer=answer,
                prepared_evidence=prepared,
                repository_path=repository_path,
            )
        except Exception as exc:
            logger.error("Citation validation failed: %s", exc, exc_info=True)
            validation = ValidationResult(
                status="not_evaluated",
                limitations_added=[f"Citation validation error: {exc}"],
            )

    # Apply validated answer back (remove unsupported citations)
    if answer and validation.repaired_citations_list is not None:
        answer.citations = validation.repaired_citations_list

    # Inject validation limitations into answer
    if answer and validation.limitations_added:
        existing_lims = list(answer.limitations or [])
        for lim in validation.limitations_added:
            if lim not in existing_lims:
                existing_lims.append(lim)
        answer.limitations = existing_lims

    times = dict(state.get("stage_times") or {})
    times["validate_citations"] = round(time.perf_counter() - t0, 3)
    return {
        "answer": answer,
        "validation_result": validation,
        "stage_times": times,
    }


def _node_finalize(state: GraphState, *, services: dict) -> dict:
    """Package the final InvestigationResponse."""
    t0 = time.perf_counter()

    answer: InvestigationAnswer = state.get("answer")
    validation: ValidationResult | None = state.get("validation_result")
    retrieval_result: dict = state.get("retrieval_result") or {}
    stage_times = dict(state.get("stage_times") or {})
    total_time = round(sum(stage_times.values()), 3)

    if answer:
        answer.execution_time_seconds = total_time

    repo_info = retrieval_result.get("repository") or {
        "id": state.get("repository_id"),
        "name": state.get("repository_name", ""),
        "url": state.get("repository_url", ""),
    }

    summary = {
        **(retrieval_result.get("summary") or {}),
        "retrieval_rounds": state.get("retrieval_round", 0) + 1,
        "stage_times": stage_times,
        "total_time_seconds": total_time,
    }
    if validation:
        summary["citation_validation"] = validation.to_dict()

    response = InvestigationResponse(
        repository=repo_info,
        query=state["query"],
        question_type=state.get("question_type"),
        investigation=answer,
        evidence_summary=summary,
        metadata={
            "version": "2.0",
            "timestamp": time.time(),
            "workflow": "langgraph",
        },
    )

    stage_times["finalize"] = round(time.perf_counter() - t0, 3)
    return {"response": response, "stage_times": stage_times}


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _merge_evidence(existing: list[dict], new_items: list[dict]) -> list[dict]:
    """Merge two evidence lists, deduplicating by (file, start_line, end_line)."""
    seen: set[tuple] = set()
    result: list[dict] = []

    for item in existing + new_items:
        key = (
            str(item.get("file", "")).replace("\\", "/"),
            item.get("start_line"),
            item.get("end_line"),
        )
        if key not in seen:
            seen.add(key)
            result.append(item)

    return result


def _rebuild_prepared_evidence(evidence: list[dict]) -> list[dict]:
    """
    Assign stable E1..En IDs to evidence items for citation validator.
    Mirrors the logic in AnswerGenerationService._prepare_evidence.
    """
    prepared = []
    seen: set[tuple] = set()
    eid = 1

    for item in evidence:
        file_path = item.get("file")
        start_line = item.get("start_line", 0)
        end_line = item.get("end_line", 0)

        if not file_path:
            continue
        if start_line == 0 and end_line == 0:
            continue

        snippet = (item.get("snippet") or item.get("content") or item.get("source") or "").strip()
        if not snippet:
            continue

        key = (file_path, start_line, end_line)
        if key in seen:
            continue
        seen.add(key)

        prepared.append({
            "evidence_id": f"E{eid}",
            "file": file_path,
            "start_line": start_line,
            "end_line": end_line,
            "symbol": item.get("symbol"),
            "symbol_type": item.get("symbol_type"),
            "snippet": snippet[:1500],
            "relevance_score": item.get("relevance_score", 0.0),
        })
        eid += 1

    return prepared


# ---------------------------------------------------------------------------
# Graph factory
# ---------------------------------------------------------------------------

def build_investigation_graph(services: dict):
    """
    Compile and return the LangGraph investigation workflow.

    Parameters
    ----------
    services: dict with keys:
        - query_analyzer_service
        - retrieval_service
        - answer_generation_service
        - evidence_evaluator     (EvidenceEvaluator instance)
        - citation_validator     (CitationValidator instance)

    Returns
    -------
    Compiled LangGraph (callable with state dict).
    """
    from functools import partial

    # Bind services to each node function
    def bind(fn):
        return partial(fn, services=services)

    graph = StateGraph(GraphState)

    # ── nodes ─────────────────────────────────────────────────────────────
    graph.add_node("analyze_query",      bind(_node_analyze_query))
    graph.add_node("retrieve_evidence",  bind(_node_retrieve_evidence))
    graph.add_node("evaluate_evidence",  bind(_node_evaluate_evidence))
    graph.add_node("increment_round",    _increment_round)   # no services needed
    graph.add_node("generate_answer",    bind(_node_generate_answer))
    graph.add_node("validate_citations", bind(_node_validate_citations))
    graph.add_node("finalize",           bind(_node_finalize))

    # ── edges ──────────────────────────────────────────────────────────────
    graph.set_entry_point("analyze_query")
    graph.add_edge("analyze_query",      "retrieve_evidence")
    graph.add_edge("retrieve_evidence",  "evaluate_evidence")

    # Conditional edge: re-retrieve or proceed
    graph.add_conditional_edges(
        "evaluate_evidence",
        _route_after_evaluation,
        {
            "retrieve_evidence": "increment_round",
            "generate_answer":   "generate_answer",
        },
    )

    graph.add_edge("increment_round",    "retrieve_evidence")
    graph.add_edge("generate_answer",    "validate_citations")
    graph.add_edge("validate_citations", "finalize")
    graph.add_edge("finalize",           END)

    return graph.compile()


# ---------------------------------------------------------------------------
# Convenience runner (used by the FastAPI endpoint)
# ---------------------------------------------------------------------------

def run_investigation(
    query: str,
    repository_id: int,
    repository_path: str,
    repository_name: str,
    repository_url: str,
    db,
    services: dict,
) -> InvestigationResponse:
    """
    Run the full investigation graph and return an InvestigationResponse.

    Raises RuntimeError on unrecoverable graph failure.
    """
    compiled_graph = build_investigation_graph(services)

    initial_state: GraphState = {
        "query": query,
        "repository_id": repository_id,
        "repository_path": repository_path,
        "repository_name": repository_name,
        "repository_url": repository_url,
        "db": db,
        "retrieval_round": 0,
        "extra_search_targets": [],
        "stage_times": {},
    }

    try:
        final_state = compiled_graph.invoke(initial_state)
    except Exception as exc:
        logger.error("Investigation graph failed: %s", exc, exc_info=True)
        raise RuntimeError(f"Investigation graph failed: {exc}") from exc

    response = final_state.get("response")
    if response is None:
        raise RuntimeError("Investigation graph completed without producing a response.")

    return response
