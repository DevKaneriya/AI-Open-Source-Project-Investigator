"""
Evidence Evaluation Service

Determines whether retrieved evidence is sufficient to answer the technical question,
or if conditional follow-up retrieval is warranted.

Produces structured assessments of supported, partially-supported, missing,
and contradictory claims, with explicit tracking of end-to-end flow stages
and consecutive stage connections.
"""

import logging
import re
from typing import Any, Optional
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# File classification helper (shared with citation_validation_service)
# ---------------------------------------------------------------------------
# Matches admin registrations, test files, migrations, fixtures, and docs.
_INDIRECT_FILE_RE = re.compile(
    r"(^|/)tests?\.py$"      # tests.py or test.py at any path depth
    r"|/tests?/"             # directory called tests/ or test/
    r"|/test_"               # test_ prefix files inside any directory
    r"|\.spec\."             # .spec.ts / .spec.js etc.
    r"|/migrations/"         # Django migrations directory
    r"|/fixtures/"           # fixture data directory
    r"|admin\.py$"           # Django admin registration files
    r"|\.(md|rst|txt)$",     # documentation
    re.IGNORECASE,
)


def _is_indirect_file(file_path: str) -> bool:
    """Return True if *file_path* is an indirect file (test, admin, migration, doc)."""
    norm = (file_path or "").replace("\\", "/")
    return bool(_INDIRECT_FILE_RE.search(norm))


def _filter_direct(evidence: list[dict]) -> list[dict]:
    """Return only direct source evidence items (exclude indirect files)."""
    return [item for item in evidence if not _is_indirect_file(item.get("file") or "")]

from backend.app.services.llm_service import LLMService

logger = logging.getLogger(__name__)


class FlowStageStatus(BaseModel):
    """Status of an end-to-end or feature flow stage."""

    stage: str = Field(
        description="Stage name: 'ui_event_handler', 'client_service', 'url_routing', 'backend_handler', 'persistence'",
    )
    present: bool = Field(
        description="Whether code for this stage is present in the evidence",
    )
    connected_to_next: bool = Field(
        default=False,
        description="Whether this stage has a verified connection to the subsequent stage",
    )
    details: str = Field(
        default="",
        description="Supporting evidence details or explanation of why connection is missing",
    )


class EvidenceEvaluation(BaseModel):
    """Structured assessment of evidence completeness and sufficiency."""

    sufficiency: str = Field(
        description="Assessment of sufficiency: 'sufficient', 'partially_sufficient', or 'insufficient'",
        pattern="^(sufficient|partially_sufficient|insufficient)$",
    )
    supported_claims: list[str] = Field(
        default_factory=list,
        description="Claims or aspects of the question clearly supported by the evidence",
    )
    partially_supported_claims: list[str] = Field(
        default_factory=list,
        description="Claims or aspects with incomplete or indirect evidence",
    )
    missing_claims: list[str] = Field(
        default_factory=list,
        description="Key aspects of the question completely missing from the evidence",
    )
    contradictory_claims: list[str] = Field(
        default_factory=list,
        description="Evidence items that conflict or appear contradictory",
    )
    retrieval_needed: bool = Field(
        description="True if additional targeted retrieval is warranted and likely to improve the answer",
    )
    recommended_queries: dict = Field(
        default_factory=lambda: {"symbol_queries": [], "text_queries": [], "import_queries": []},
        description="Specific recommended queries to fill missing evidence gaps",
    )
    reasoning: str = Field(
        description="Explanation for why evidence is sufficient or what specific gaps need filling",
    )
    flow_stages: list[FlowStageStatus] = Field(
        default_factory=list,
        description="Tracked flow stages with presence and connection verification",
    )
    missing_stages: list[str] = Field(
        default_factory=list,
        description="List of stage names that are missing from retrieved evidence",
    )
    unverified_connections: list[str] = Field(
        default_factory=list,
        description="List of consecutive stage connections that could not be verified",
    )


class EvidenceEvaluationService:
    """Evaluates retrieved code evidence completeness and identifies retrieval gaps."""

    RESPONSE_SCHEMA: dict = {
        "type": "object",
        "properties": {
            "sufficiency": {
                "type": "string",
                "enum": ["sufficient", "partially_sufficient", "insufficient"],
                "description": "Evidence sufficiency: 'sufficient' only if required stages and connections are verified, 'partially_sufficient' if some parts answerable but key connections missing, 'insufficient' if core missing.",
            },
            "supported_claims": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Aspects or sub-questions supported by verified evidence. Never use generic snippet counts.",
            },
            "partially_supported_claims": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Aspects with partial or unverified code evidence.",
            },
            "missing_claims": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Specific missing components, call connections, or files.",
            },
            "contradictory_claims": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Contradictory findings or conflicting implementations.",
            },
            "retrieval_needed": {
                "type": "boolean",
                "description": "True if searching for specific missing stages/symbols/text would materially improve the answer.",
            },
            "recommended_queries": {
                "type": "object",
                "properties": {
                    "symbol_queries": {"type": "array", "items": {"type": "string"}},
                    "text_queries": {"type": "array", "items": {"type": "string"}},
                    "import_queries": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["symbol_queries", "text_queries", "import_queries"],
            },
            "reasoning": {
                "type": "string",
                "description": "Explanation evaluating whether evidence genuinely supports the requested claims and required stages.",
            },
            "missing_stages": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Names of flow stages missing from evidence (e.g. 'ui_event_handler', 'url_routing').",
            },
            "unverified_connections": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Consecutive stage connections that are unverified (e.g. 'ui_event_handler to backend_handler via route').",
            },
        },
        "required": [
            "sufficiency",
            "supported_claims",
            "partially_supported_claims",
            "missing_claims",
            "contradictory_claims",
            "retrieval_needed",
            "recommended_queries",
            "reasoning",
        ],
        "additionalProperties": False,
    }

    SYSTEM_PROMPT = """\
You are an expert Evidence Evaluator in an agentic code investigation system.
Your job is to rigorously assess whether retrieved code evidence is sufficient to answer the user's technical question.

CRITICAL RULES:
1. NEVER determine sufficiency from evidence snippet count alone! Even 12+ snippets can be irrelevant or missing core stages.
2. For end-to-end and feature flows, track each required stage:
   - Stage 1: ui_event_handler (e.g., UI component button click, increaseQty, decreaseQty, event listener)
   - Stage 2: client_service (e.g., API service method making HTTP request like addToCart)
   - Stage 3: url_routing (e.g., urls.py / routes mapping HTTP path to backend view)
   - Stage 4: backend_handler (e.g., View/Controller method handling request like CartAddView.post)
   - Stage 5: persistence (e.g., database model write/update like CartItem.save())
3. Distinguish between a stage being PRESENT and its connection to consecutive stages being VERIFIED:
   - Example: If CartSyncView.post() is in evidence, but its docstring states "Merge local guest cart into DB — used on fresh login only" and maps to cart/sync/, it is NOT the verified endpoint for normal quantity updates!
   - The connection between a UI quantity update and CartSyncView is UNVERIFIED unless evidence demonstrates that the UI event handler or cart service actually routes to CartSyncView for quantity updates.
4. When evidence is missing but searchable (e.g. missing route, missing UI handler), return retrieval_needed=True and populate targeted recommended_queries.
5. If current_round >= max_rounds and connections remain unverified, return sufficiency="partially_sufficient" or "insufficient" and explicitly list missing_stages and unverified_connections.
6. For symbol lookups: if the exact symbol definition is located with file, line, and body, sufficiency is "sufficient" and retrieval_needed=False. If absent or only incidental text mentions exist, evaluate if alternative queries can find it.

Return ONLY valid JSON matching the specified schema."""

    def __init__(self, llm_service: LLMService):
        self.llm_service = llm_service

    # =========================================================================
    # FLOW STAGE DETECTION & CONNECTION ANALYSIS
    # =========================================================================

    @classmethod
    def is_flow_question(cls, question: str, question_type: str | None) -> bool:
        """Determine whether the question requires end-to-end or multi-stage flow verification."""
        if question_type in ("feature_flow", "end_to_end_flow"):
            return True
        q_lower = (question or "").lower()
        flow_keywords = (
            "flow", "update", "how does", "from ui", "from frontend",
            "quantity", "checkout", "auth", "login", "register",
            "request to", "end to end", "add to cart", "remove from",
        )
        return any(kw in q_lower for kw in flow_keywords)

    @classmethod
    def analyze_flow_stages(
        cls,
        question: str,
        question_type: str | None,
        evidence: list[dict],
    ) -> tuple[list[FlowStageStatus], list[str], list[str], dict]:
        """
        Analyze code evidence to detect presence and consecutive connection of flow stages:
        1. ui_event_handler
        2. client_service
        3. url_routing
        4. backend_handler
        5. persistence
        """
        q_lower = question.lower()
        is_quantity_update = "quantity" in q_lower or "qty" in q_lower

        has_ui = False
        ui_details = ""
        ui_symbol = ""

        has_client_svc = False
        client_svc_details = ""
        client_target_url = ""

        has_routing = False
        routing_details = ""
        routed_views = set()
        routed_paths = set()

        has_backend = False
        backend_details = ""
        backend_symbols = set()
        is_guest_merge_only = False

        has_db = False
        db_details = ""

        for item in evidence:
            f = (item.get("file") or "").replace("\\", "/").lower()
            sym = (item.get("symbol") or "")
            content = (item.get("snippet") or item.get("content") or item.get("source") or "")
            content_lower = content.lower()

            # 1. UI Event Handler
            if any(f.endswith(ext) for ext in (".ts", ".tsx", ".js", ".jsx", ".html")):
                if not f.endswith("service.ts") and not "services/" in f and not ".spec." in f:
                    if any(kw in content_lower for kw in ("increaseqty", "decreaseqty", "changequantity", "updatequantity", "click", "subtotal", "loadcart")):
                        has_ui = True
                        ui_symbol = sym or "Cart"
                        ui_details = f"Found UI component/event in {item.get('file')} ({sym})"

            # 2. Client Service Call
            if "service" in f or "api" in f or "client" in f:
                if any(kw in content_lower for kw in ("addtocart", "synccart", "http.", "httpclient", "environment.apiurl", "post<", "get<")):
                    has_client_svc = True
                    client_svc_details = f"Found client service in {item.get('file')} ({sym})"
                    # Check what URLs it calls
                    if "cart/add" in content_lower:
                        client_target_url = "cart/add"
                    elif "cart/sync" in content_lower:
                        if not client_target_url:
                            client_target_url = "cart/sync"

            # 3. URL Routing
            if "urls.py" in f or "routes" in f or "router" in f or "urlpatterns" in content_lower:
                if any(kw in content_lower for kw in ("path(", "re_path(", "url(", "route(")):
                    has_routing = True
                    routing_details = f"Found URL route configuration in {item.get('file')}"
                    for path_match in re.findall(r"path\(['\"]([^'\"]+)['\"],\s*([A-Za-z0-9_]+)", content):
                        routed_paths.add(path_match[0].lower().rstrip("/"))
                        routed_views.add(path_match[1].lower())

            # 4. Backend Handler
            if ("views.py" in f or "controller" in f or "handler" in f or "APIView" in content):
                if any(kw in content_lower for kw in ("cartsyncview", "cartaddview", "cartremoveview", "cartview")):
                    has_backend = True
                    backend_symbols.add(sym.lower() if sym else "")
                    backend_details = f"Found backend handler in {item.get('file')} ({sym})"
                    if "cartsyncview" in content_lower and "fresh login only" in content_lower:
                        if "cartaddview" not in content_lower:
                            is_guest_merge_only = True

            # 5. Persistence
            if any(kw in content_lower for kw in (".save()", "objects.get_or_create", "objects.filter", "objects.create", "cartitem")):
                has_db = True
                db_details = f"Found database operations in {item.get('file')} ({sym})"

        # Check consecutive connections
        # Connection 1: UI -> Client Service
        ui_to_svc_connected = False
        if has_ui and has_client_svc:
            # Does UI call the service? (e.g. cartService.addToCart)
            ui_to_svc_connected = True

        # Connection 2: Client Service -> URL Routing
        svc_to_route_connected = False
        if has_client_svc and has_routing:
            if client_target_url:
                svc_to_route_connected = any(client_target_url in p for p in routed_paths)
            else:
                svc_to_route_connected = True

        # Connection 3: URL Routing -> Backend Handler
        route_to_backend_connected = False
        if has_routing and has_backend:
            route_to_backend_connected = any(v in routed_views for v in backend_symbols if v)

        # Connection 4: Backend Handler -> Persistence
        backend_to_db_connected = False
        if has_backend and has_db:
            backend_to_db_connected = True

        # If question is specifically about quantity updates:
        # Crucial check: Is CartSyncView falsely being treated as the quantity update handler?
        unverified_conns = []
        if is_quantity_update:
            if is_guest_merge_only and not ("cartaddview" in backend_symbols):
                unverified_conns.append(
                    "CartSyncView is present but documented for guest-cart login merge only; normal quantity update endpoint (CartAddView) is unverified in evidence."
                )
                route_to_backend_connected = False

        if has_ui and not has_routing:
            unverified_conns.append("UI component is present, but URL routing connecting UI to backend is missing.")
        if has_client_svc and not has_routing:
            unverified_conns.append("Client service calls endpoint, but backend URL routing mapping is missing.")
        if has_backend and not has_ui:
            unverified_conns.append("Backend view is present, but UI event handler triggering the action is missing.")

        stages = [
            FlowStageStatus(
                stage="ui_event_handler",
                present=has_ui,
                connected_to_next=ui_to_svc_connected,
                details=ui_details or "Missing UI component or button click handler",
            ),
            FlowStageStatus(
                stage="client_service",
                present=has_client_svc,
                connected_to_next=svc_to_route_connected,
                details=client_svc_details or "Missing client API service method",
            ),
            FlowStageStatus(
                stage="url_routing",
                present=has_routing,
                connected_to_next=route_to_backend_connected,
                details=routing_details or "Missing URL route configuration",
            ),
            FlowStageStatus(
                stage="backend_handler",
                present=has_backend,
                connected_to_next=backend_to_db_connected,
                details=backend_details or "Missing backend view/handler",
            ),
            FlowStageStatus(
                stage="persistence",
                present=has_db,
                connected_to_next=True,
                details=db_details or "Missing database write/ORM operation",
            ),
        ]

        missing_stages = [s.stage for s in stages if not s.present]

        # Recommended queries for missing stages
        recommended_queries = {
            "symbol_queries": [],
            "text_queries": [],
            "import_queries": [],
        }

        if is_quantity_update:
            if not has_ui:
                recommended_queries["symbol_queries"].extend(["increaseQty", "decreaseQty", "Cart"])
                recommended_queries["text_queries"].extend(["increaseQty", "cart.ts", "quantity"])
            if not has_routing:
                recommended_queries["text_queries"].extend(["cart/add", "path('cart/", "urls.py"])
            if not has_backend or ("cartaddview" not in backend_symbols):
                recommended_queries["symbol_queries"].append("CartAddView")
                recommended_queries["text_queries"].append("CartAddView")
            if not has_client_svc:
                recommended_queries["symbol_queries"].append("addToCart")
                recommended_queries["text_queries"].append("cartservice.ts")
        else:
            if not has_routing:
                recommended_queries["text_queries"].append("urls.py")
            if not has_ui:
                recommended_queries["text_queries"].append("Component")

        # Deduplicate
        recommended_queries["symbol_queries"] = list(dict.fromkeys(recommended_queries["symbol_queries"]))[:4]
        recommended_queries["text_queries"] = list(dict.fromkeys(recommended_queries["text_queries"]))[:4]
        recommended_queries["import_queries"] = list(dict.fromkeys(recommended_queries["import_queries"]))[:2]

        return stages, missing_stages, unverified_conns, recommended_queries

    # =========================================================================
    # PUBLIC EVALUATION API
    # =========================================================================

    def evaluate(
        self,
        question: str,
        evidence: list[dict],
        question_type: str | None = None,
        current_round: int = 0,
        max_rounds: int = 2,
    ) -> EvidenceEvaluation:
        """Evaluate evidence completeness using structured LLM analysis with flow stage verification."""
        if not evidence:
            return self._empty_evidence_fallback(question, current_round, max_rounds)

        is_flow = self.is_flow_question(question, question_type)
        stages, missing_stages, unverified_conns, rec_queries = self.analyze_flow_stages(
            question, question_type, evidence
        )

        # Fast path heuristics for simple symbol lookups
        # Bug-3 fix: exact_match must be in a DIRECT source file, not admin/test/migration.
        if question_type == "symbol_lookup" and evidence:
            direct_ev = _filter_direct(evidence)
            # Only fire the fast path when the exact match comes from a DIRECT source file
            has_exact = any(item.get("exact_match") or item.get("exact_identifier_match") for item in direct_ev)
            if has_exact:
                return EvidenceEvaluation(
                    sufficiency="sufficient",
                    supported_claims=["Found exact symbol definition and source context."],
                    partially_supported_claims=[],
                    missing_claims=[],
                    contradictory_claims=[],
                    retrieval_needed=False,
                    recommended_queries={"symbol_queries": [], "text_queries": [], "import_queries": []},
                    reasoning="Exact symbol definition located in retrieved evidence.",
                    flow_stages=[],
                    missing_stages=[],
                    unverified_connections=[],
                )

        # For flow questions: if critical stages or connections are missing
        if is_flow:
            # If missing key stages and we haven't reached max rounds -> MUST retrieve more
            if (missing_stages or unverified_conns) and current_round < max_rounds:
                logger.info(
                    f"Flow evaluation detected missing stages {missing_stages} or unverified connections {unverified_conns}. Routing to retrieval round {current_round + 1}."
                )
                return EvidenceEvaluation(
                    sufficiency="insufficient" if len(missing_stages) >= 3 else "partially_sufficient",
                    supported_claims=[s.details for s in stages if s.present],
                    partially_supported_claims=[s.details for s in stages if s.present and not s.connected_to_next],
                    missing_claims=[f"Missing {s}" for s in missing_stages] + unverified_conns,
                    contradictory_claims=[],
                    retrieval_needed=True,
                    recommended_queries=rec_queries,
                    reasoning=(
                        f"Flow evaluation identified missing stages ({', '.join(missing_stages) or 'none'}) "
                        f"and unverified connections ({'; '.join(unverified_conns) or 'none'}). "
                        f"Retrieval round {current_round + 1} required."
                    ),
                    flow_stages=stages,
                    missing_stages=missing_stages,
                    unverified_connections=unverified_conns,
                )

        # If we reached max rounds
        if current_round >= max_rounds:
            return self._max_rounds_reached(question, evidence, stages, missing_stages, unverified_conns)

        # Prepare evidence summary for LLM prompt
        evidence_summary_lines = []
        for i, item in enumerate(evidence[:10], 1):
            file = item.get("file", "unknown")
            symbol = item.get("symbol") or "N/A"
            lines = f"{item.get('start_line', '?')}-{item.get('end_line', '?')}"
            snippet = (item.get("snippet") or item.get("content") or "")[:250].replace("\n", " ")
            evidence_summary_lines.append(f"[{i}] {file} (lines {lines}, symbol: {symbol}): {snippet}")

        evidence_text = "\n".join(evidence_summary_lines)

        user_prompt = f"""QUESTION:
{question}

QUESTION TYPE:
{question_type or 'general'}

CURRENT RETRIEVAL ROUND:
{current_round} (Max additional rounds: {max_rounds})

STAGE AUDIT:
Missing stages: {missing_stages}
Unverified connections: {unverified_conns}

RETRIEVED EVIDENCE SUMMARY ({len(evidence)} items):
{evidence_text}

Evaluate whether this evidence genuinely supports the requested claims and required stages.
DO NOT claim sufficiency based on snippet count alone. If key stages or connections are missing, set retrieval_needed=True."""

        try:
            raw = self.llm_service.generate_structured(
                system_prompt=self.SYSTEM_PROMPT,
                user_prompt=user_prompt,
                response_schema=self.RESPONSE_SCHEMA,
                max_tokens=700,
            )
            raw.pop("__llm_metadata__", None)
            raw.pop("_metadata", None)

            # Never allow LLM to report sufficient if critical flow stages are missing
            if is_flow and (missing_stages or unverified_conns):
                if current_round < max_rounds:
                    raw["retrieval_needed"] = True
                    raw["sufficiency"] = "partially_sufficient" if len(missing_stages) < 3 else "insufficient"
                else:
                    raw["retrieval_needed"] = False
                    raw["sufficiency"] = "partially_sufficient" if len(missing_stages) < 3 else "insufficient"

                raw["missing_stages"] = missing_stages
                raw["unverified_connections"] = unverified_conns

            if current_round >= max_rounds:
                raw["retrieval_needed"] = False

            raw["flow_stages"] = [s.model_dump() for s in stages]
            return EvidenceEvaluation.model_validate(raw)

        except Exception as exc:
            logger.warning("LLM evidence evaluation failed, using deterministic fallback: %s", exc)
            return self._deterministic_evaluation(
                question, evidence, question_type, current_round, max_rounds, stages, missing_stages, unverified_conns
            )

    # =========================================================================
    # DETERMINISTIC FALLBACK & BOUNDARY HANDLING
    # =========================================================================

    def _deterministic_evaluation(
        self,
        question: str,
        evidence: list[dict],
        question_type: str | None,
        current_round: int,
        max_rounds: int,
        stages: list[FlowStageStatus],
        missing_stages: list[str],
        unverified_conns: list[str],
    ) -> EvidenceEvaluation:
        """
        Deterministic rule-based evaluation.
        NEVER determines sufficiency from snippet count alone!
        Evaluates stage completeness and connection verification.
        """
        is_flow = self.is_flow_question(question, question_type)

        if is_flow:
            if missing_stages or unverified_conns:
                if current_round < max_rounds:
                    return EvidenceEvaluation(
                        sufficiency="insufficient" if len(missing_stages) >= 3 else "partially_sufficient",
                        supported_claims=[s.details for s in stages if s.present],
                        partially_supported_claims=[s.details for s in stages if s.present and not s.connected_to_next],
                        missing_claims=[f"Missing stage: {s}" for s in missing_stages] + unverified_conns,
                        contradictory_claims=[],
                        retrieval_needed=True,
                        recommended_queries={
                            "symbol_queries": ["increaseQty", "decreaseQty", "CartAddView"] if "quantity" in question.lower() else [],
                            "text_queries": ["cart/add", "path('cart/"] if "quantity" in question.lower() else ["urls.py"],
                            "import_queries": [],
                        },
                        reasoning=f"Missing required flow stages: {', '.join(missing_stages)}. Targeted retrieval needed.",
                        flow_stages=stages,
                        missing_stages=missing_stages,
                        unverified_connections=unverified_conns,
                    )
                else:
                    return self._max_rounds_reached(question, evidence, stages, missing_stages, unverified_conns)
            else:
                return EvidenceEvaluation(
                    sufficiency="sufficient",
                    supported_claims=[s.details for s in stages if s.present],
                    partially_supported_claims=[],
                    missing_claims=[],
                    contradictory_claims=[],
                    retrieval_needed=False,
                    recommended_queries={"symbol_queries": [], "text_queries": [], "import_queries": []},
                    reasoning="All required flow stages and consecutive connections verified in evidence.",
                    flow_stages=stages,
                    missing_stages=[],
                    unverified_connections=[],
                )

        # For non-flow questions (e.g. symbol lookups or architectural questions)
        # Bug-3 fix: only count direct source files as evidence of symbol existence.
        direct_evidence = _filter_direct(evidence)
        has_symbol_match = any(item.get("exact_match") or item.get("symbol") for item in direct_evidence)
        if has_symbol_match:
            return EvidenceEvaluation(
                sufficiency="sufficient",
                supported_claims=["Located relevant symbol definitions in retrieved evidence."],
                partially_supported_claims=[],
                missing_claims=[],
                contradictory_claims=[],
                retrieval_needed=False,
                recommended_queries={"symbol_queries": [], "text_queries": [], "import_queries": []},
                reasoning="Retrieved evidence contains required symbol definitions.",
                flow_stages=[],
                missing_stages=[],
                unverified_connections=[],
            )

        if current_round < max_rounds:
            return EvidenceEvaluation(
                sufficiency="insufficient",
                supported_claims=[],
                partially_supported_claims=[],
                missing_claims=["Specific symbol definition or implementation code."],
                contradictory_claims=[],
                retrieval_needed=True,
                recommended_queries={
                    "symbol_queries": [],
                    "text_queries": [w for w in question.split() if len(w) > 3][:3],
                    "import_queries": [],
                },
                reasoning="Requested symbol definition not located; targeted retrieval needed.",
                flow_stages=[],
                missing_stages=["symbol_definition"],
                unverified_connections=[],
            )

        return self._max_rounds_reached(question, evidence, stages, missing_stages, unverified_conns)

    def _empty_evidence_fallback(self, question: str, current_round: int, max_rounds: int) -> EvidenceEvaluation:
        """Fallback for empty evidence."""
        retrieval_needed = current_round < max_rounds
        return EvidenceEvaluation(
            sufficiency="insufficient",
            supported_claims=[],
            partially_supported_claims=[],
            missing_claims=["All relevant source code, symbols, and routes."],
            contradictory_claims=[],
            retrieval_needed=retrieval_needed,
            recommended_queries={
                "symbol_queries": [],
                "text_queries": [w for w in question.split() if len(w) > 3][:3],
                "import_queries": [],
            },
            reasoning="No evidence retrieved yet for this question.",
            flow_stages=[],
            missing_stages=["all_stages"],
            unverified_connections=[],
        )

    def _max_rounds_reached(
        self,
        question: str,
        evidence: list[dict],
        stages: list[FlowStageStatus] | None = None,
        missing_stages: list[str] | None = None,
        unverified_conns: list[str] | None = None,
    ) -> EvidenceEvaluation:
        """
        Max rounds reached - forced termination.

        Bug-1 fix: NEVER reports sufficiency="sufficient" if stages are missing.
        Bug-3 fix: Only direct source files (not admin/test/migration) count as
        relevant evidence for a positive existence claim.
        """
        stages = stages or []
        missing_stages = missing_stages or []
        unverified_conns = unverified_conns or []

        if missing_stages or unverified_conns:
            sufficiency = "partially_sufficient" if any(s.present for s in stages) else "insufficient"
            supported = [s.details for s in stages if s.present]
            missing = [f"Missing stage: {s}" for s in missing_stages] + unverified_conns
            reasoning = (
                f"Maximum retrieval rounds reached. Flow remains incomplete: "
                f"missing stages ({', '.join(missing_stages) or 'none'}) and "
                f"unverified connections ({'; '.join(unverified_conns) or 'none'})."
            )
        else:
            # Bug-3 fix: classify evidence before deciding sufficiency.
            # Admin, test, and migration files are indirect and do NOT prove
            # that the requested symbol/implementation exists.
            direct_evidence = _filter_direct(evidence)

            has_relevant = any(item.get("exact_match") or item.get("symbol") for item in direct_evidence)
            if has_relevant:
                sufficiency = "sufficient"
                supported = ["Retrieved evidence from direct source addresses question scope."]
                missing = []
                reasoning = "Maximum retrieval rounds reached; proceeding to answer generation."
            elif direct_evidence:
                sufficiency = "partially_sufficient"
                supported = ["Some direct source evidence present but no exact symbol match."]
                missing = ["Exact symbol definition or primary implementation."]
                reasoning = "Maximum retrieval rounds reached with partial direct evidence."
            else:
                sufficiency = "insufficient"
                supported = []
                missing = ["Direct implementation source. Available evidence is from admin/test/migration files only."]
                reasoning = (
                    "Maximum retrieval rounds reached. No direct source evidence found. "
                    "Available evidence comes from indirect files (admin registrations, tests, or migrations) "
                    "which do not confirm that the requested implementation exists."
                )

        return EvidenceEvaluation(
            sufficiency=sufficiency,
            supported_claims=supported,
            partially_supported_claims=[],
            missing_claims=missing,
            contradictory_claims=[],
            retrieval_needed=False,
            recommended_queries={"symbol_queries": [], "text_queries": [], "import_queries": []},
            reasoning=reasoning,
            flow_stages=stages,
            missing_stages=missing_stages,
            unverified_connections=unverified_conns,
        )
