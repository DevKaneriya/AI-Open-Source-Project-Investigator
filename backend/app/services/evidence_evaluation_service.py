"""
Evidence Evaluation Service

Determines whether retrieved evidence is sufficient to answer the technical question,
or if conditional follow-up retrieval is warranted.

Evaluation is question-type aware. Each question is mapped to an evaluation
profile with its own requirements:

- symbol_lookup:     the requested definition (or an explicit "not found")
- call_site:         call sites that reference the target, plus its definition
- code_explanation:  relevant implementation code
- database_query:    direct backend ORM/SQL evidence (client storage never counts)
- flow:              applicable stages UI -> client -> route -> handler ->
                     persistence -> response -> frontend handling, with every
                     consecutive connection verified from code (not file names)
- architecture:      entry points, configuration, routing, persistence config,
                     API communication (and authentication when asked)
- general:           dependency / cross-cutting questions (LLM-assisted)

Deterministic profiles never call the LLM.
"""

import logging
import re
from typing import Any, Optional
from pydantic import BaseModel, Field

from backend.app.services import evidence_signals as sig
from backend.app.services.llm_service import LLMService

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# File classification helpers (kept for backwards compatibility)
# ---------------------------------------------------------------------------

def _is_indirect_file(file_path: str) -> bool:
    """Return True if *file_path* is an indirect file (test, admin, migration, doc, generated)."""
    return sig.is_indirect(file_path)


def _filter_direct(evidence: list[dict]) -> list[dict]:
    """Return only direct source evidence items (exclude indirect files)."""
    return [item for item in evidence if not _is_indirect_file(item.get("file") or "")]


class FlowStageStatus(BaseModel):
    """Status of an evaluated stage / requirement."""

    stage: str = Field(
        description="Stage name, e.g. 'ui_event_handler', 'client_service', 'url_routing', 'backend_handler', 'persistence'",
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
    evidence_ids: list[str] = Field(
        default_factory=list,
        description="Evidence IDs that establish this stage",
    )
    verified_absent: bool = Field(
        default=False,
        description="Evidence affirmatively shows this stage does not exist (e.g. the response is discarded)",
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
        description="Tracked stages with presence and connection verification",
    )
    missing_stages: list[str] = Field(
        default_factory=list,
        description="List of stage names that are missing from retrieved evidence",
    )
    unverified_connections: list[str] = Field(
        default_factory=list,
        description="List of consecutive stage connections that could not be verified",
    )
    evaluation_profile: str = Field(
        default="",
        description="Evaluation profile derived from the question type and wording",
    )
    grounding_notes: list[str] = Field(
        default_factory=list,
        description="Deterministic facts about what the evidence does and does not establish",
    )


def _empty_queries() -> dict:
    return {"symbol_queries": [], "text_queries": [], "import_queries": [], "caller_queries": []}


class _Assessment:
    """Deterministic requirement assessment for one profile."""

    def __init__(self):
        self.stages: list[FlowStageStatus] = []
        self.unverified: list[str] = []
        self.queries: dict = _empty_queries()
        self.notes: list[str] = []

    def add_stage(
        self,
        stage: str,
        items: list[dict],
        details: str,
        missing_details: str,
        absent_items: list[dict] | None = None,
    ) -> FlowStageStatus:
        """Add a requirement. *absent_items*: evidence proving the stage does not exist."""
        absent = not items and bool(absent_items)
        shown = items or absent_items or []
        status = FlowStageStatus(
            stage=stage,
            present=bool(items),
            details=details if items or absent else missing_details,
            evidence_ids=list(dict.fromkeys(i.get("evidence_id") for i in shown if i.get("evidence_id")))[:8],
            verified_absent=absent,
        )
        self.stages.append(status)
        return status

    def want(self, kind: str, *values: str) -> None:
        for value in values:
            if value and value not in self.queries[kind]:
                self.queries[kind].append(value)

    @property
    def missing_stages(self) -> list[str]:
        return [s.stage for s in self.stages if not s.present and not s.verified_absent]

    def finalized_queries(self) -> dict:
        return {
            "symbol_queries": self.queries["symbol_queries"][:5],
            "text_queries": self.queries["text_queries"][:5],
            "import_queries": self.queries["import_queries"][:2],
            "caller_queries": self.queries["caller_queries"][:3],
        }


def _describe(item: dict) -> str:
    label = item.get("symbol") or item.get("qualified_name") or ""
    location = f"{sig.normalize_path(item.get('file'))}:{item.get('start_line')}-{item.get('end_line')}"
    eid = item.get("evidence_id")
    return f"{label + ' in ' if label else ''}{location}{f' [{eid}]' if eid else ''}"


def _describe_many(items: list[dict], limit: int = 2) -> str:
    return "; ".join(_describe(i) for i in items[:limit])


class EvidenceEvaluationService:
    """Evaluates retrieved code evidence completeness and identifies retrieval gaps."""

    RESPONSE_SCHEMA: dict = {
        "type": "object",
        "properties": {
            "sufficiency": {
                "type": "string",
                "enum": ["sufficient", "partially_sufficient", "insufficient"],
                "description": "Evidence sufficiency: 'sufficient' only if the question's requirements are verified, 'partially_sufficient' if some parts answerable, 'insufficient' if core missing.",
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
                "description": "True if searching for specific missing symbols/text would materially improve the answer.",
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
                "description": "Explanation evaluating whether evidence genuinely supports the requested claims.",
            },
            "missing_stages": {
                "type": "array",
                "items": {"type": "string"},
            },
            "unverified_connections": {
                "type": "array",
                "items": {"type": "string"},
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
Assess whether retrieved code evidence is sufficient to answer the user's technical question.

CRITICAL RULES:
1. NEVER determine sufficiency from snippet count alone.
2. Admin registrations, tests, migrations, generated bundles and documentation do not prove runtime behaviour.
3. Browser storage (localStorage/sessionStorage) is client-side state, never backend database persistence.
4. When evidence is missing but searchable, return retrieval_needed=True with specific recommended_queries
   (exact identifiers, URL fragments, configuration keys) — never generic words.
5. If current_round >= max_rounds and gaps remain, return "partially_sufficient" or "insufficient".

Return ONLY valid JSON matching the specified schema."""

    _FLOW_WORDS_RE = re.compile(
        r"\btrace\b|\bflow\b|\bend[- ]to[- ]end\b|\bfrom\b.+\bto\b|\bstep[- ]by[- ]step\b|\blifecycle\b",
        re.IGNORECASE,
    )
    _FRONTEND_WORDS_RE = re.compile(
        r"\b(frontend|front-end|ui|button|click|form|component|page|angular|react|vue|template|browser|"
        r"returned to the (?:frontend|client|ui))\b",
        re.IGNORECASE,
    )
    _ACTOR_WORDS_RE = re.compile(
        r"\b(?:an? |the )?(?:authenticated|logged[- ]in|signed[- ]in|guest|anonymous) (?:user|customer|shopper|visitor)s?\b"
        r"|\b(?:user|customer|shopper)s? (?:clicks?|submits?|adds?|updates?|changes?|removes?|retriev\w*|views?|opens?)\b",
        re.IGNORECASE,
    )
    _DATA_READ_WORDS_RE = re.compile(r"\b(retriev\w*|fetch\w*|load\w*|list\w*|reads?|get|gets)\b", re.IGNORECASE)
    _UI_WORDS_RE = re.compile(r"\b(ui|button|click|clicks|form|submit|submitting|component|page|template|event)\b", re.IGNORECASE)
    _PERSIST_WORDS_RE = re.compile(
        r"\b(database|db|persist\w*|stor(?:e|ed|es|ing)|sav(?:e|ed|es|ing)|writes?|written|orm|sql|quer(?:y|ies))\b",
        re.IGNORECASE,
    )
    _WRITE_WORDS_RE = re.compile(
        r"\b(writes?|written|sav(?:e|ed|es|ing)|updat(?:e|ed|es|ing)|insert\w*|creat(?:e|es|ed|ing)|"
        r"persist\w*|adds?|adding|delet\w*|remov\w*|increas\w*|decreas\w*)\b",
        re.IGNORECASE,
    )
    _RESPONSE_WORDS_RE = re.compile(r"\b(response|returned?|returns)\b", re.IGNORECASE)
    _FRONTEND_HANDLING_WORDS_RE = re.compile(
        r"returned to the (?:frontend|client|ui)|frontend (?:session|state|handling|update)|session update|"
        r"(?:ui|view|state) update|displayed|rendered|"
        r"response (?:is |gets )?(?:consumed|handled|used|processed)|(?:consum|handl|process)\w* the response",
        re.IGNORECASE,
    )
    _CALL_SITE_WORDS_RE = re.compile(
        r"\b(?:who|what|which)\b[^?]*\bcalls?\b|\bcall[- ]sites?\b|\bcallers?\b|\bwhere is\b[^?]*\b(?:called|used|invoked)\b",
        re.IGNORECASE,
    )
    _GUEST_WORDS_RE = re.compile(r"\b(guest|anonymous|logged[- ]out|unauthenticated|not logged)\b", re.IGNORECASE)
    _AUTH_WORDS_RE = re.compile(r"\bauth\w*|\blog ?in\b|\bsession\b|\bjwt\b|\btoken\b", re.IGNORECASE)
    _SQL_WORDS_RE = re.compile(r"\bsql\b", re.IGNORECASE)
    _MODEL_CLASS_RE = re.compile(r"\(models\.Model\)|declarative_base|DeclarativeBase|@Entity|mongoose\.Schema|\(Base\)")
    _AUTH_BRANCH_RE = re.compile(r"is_authenticated|isAuthenticated|isLoggedIn|isLogged\w*|request\.user|auth_user|currentUser|IsAuthenticated")
    _BACKEND_ENTRY_NAMES = {"manage.py", "wsgi.py", "asgi.py", "main.py", "app.py", "server.py", "server.js", "server.ts"}
    _FRONTEND_ENTRY_NAMES = {"main.ts", "main.tsx", "main.js", "index.tsx", "index.jsx"}

    def __init__(self, llm_service: LLMService):
        self.llm_service = llm_service

    # =========================================================================
    # PROFILE SELECTION
    # =========================================================================

    _USAGE_WORDS_RE = re.compile(
        r"\busages?\b|\bcrud\b|\breferences? to\b|\bwhere\b[^?]*\b(?:used|referenced)\b|"
        r"\b(?:all|every)\b[^?]*\b(?:operations|references|usages|places)\b",
        re.IGNORECASE,
    )
    _TRACE_WORDS_RE = re.compile(
        r"\btrace\b|\bwhat (?:happens|does)\b|\bwalk (?:me )?through\b|\bhow does\b|\bexplain\b|\bstep[- ]by[- ]step\b",
        re.IGNORECASE,
    )

    @classmethod
    def function_targets(cls, question: str) -> list[str]:
        """Function/method names the question asks about (snake_case or lowerCamelCase)."""
        # Read directly from the question: claim_identifiers misses names with a
        # leading underscore (``_handle_payment_captured``).
        names = re.findall(r"(?<![\w.])(_*[a-z][a-z0-9]*(?:_[a-z0-9]+)+|_*[a-z]+(?:[A-Z][a-z0-9]*)+)(?:\(\))?(?![\w.])", question or "")
        return list(dict.fromkeys(n for n in names if not n.startswith(("http", "www"))))[:3]

    @classmethod
    def evaluation_profile(cls, question: str, question_type: str | None) -> str:
        q = question or ""
        if (
            question_type == "call_site_lookup"
            or cls._CALL_SITE_WORDS_RE.search(q)
            or (cls._USAGE_WORDS_RE.search(q) and cls._question_identifiers(q))
        ):
            return "call_site"
        if question_type == "symbol_lookup":
            return "symbol_lookup"
        if sig.is_cache_question(q) and question_type != "dependency_analysis":
            return "cache"
        if question_type == "architectural_overview":
            return "architecture"
        if (
            cls.function_targets(q)
            and question_type != "dependency_analysis"
            and (cls._TRACE_WORDS_RE.search(q) or question_type in ("feature_flow", "file_explanation"))
        ):
            return "symbol_trace"
        if question_type == "file_explanation":
            return "code_explanation"
        if question_type == "dependency_analysis":
            return "general"
        if sig.is_calculation_question(q) and question_type != "end_to_end_flow":
            return "calculation"
        if cls.is_flow_question(q, question_type):
            return "flow"
        if cls._PERSIST_WORDS_RE.search(q):
            return "database_query"
        if question_type == "cross_cutting_concern":
            return "general"
        return "code_explanation"

    @classmethod
    def is_flow_question(cls, question: str, question_type: str | None) -> bool:
        """Whether the question requires multi-stage flow verification."""
        q = question or ""
        if question_type == "end_to_end_flow":
            return True
        if cls._FLOW_WORDS_RE.search(q):
            return True
        if question_type in ("feature_flow", "cross_cutting_concern"):
            if cls._FRONTEND_WORDS_RE.search(q):
                return True
            # "an authenticated user retrieves X" is a user flow; "how is X
            # persisted for a logged-in customer" stays a persistence question.
            if cls._ACTOR_WORDS_RE.search(q) and not cls._PERSIST_WORDS_RE.search(q):
                return True
            if question_type == "feature_flow" and cls._WRITE_WORDS_RE.search(q) and not cls._PERSIST_WORDS_RE.search(q):
                return True
        return False

    # =========================================================================
    # PROFILE ASSESSMENTS
    # =========================================================================

    @classmethod
    def _question_identifiers(cls, question: str) -> list[str]:
        idents = [
            tok for tok in sig.claim_identifiers(question)
            if "/" not in tok and not tok.lower().endswith((".py", ".ts", ".js"))
        ]
        return sorted(idents, key=lambda t: (-len(t), t))[:4]

    def _assess_symbol_lookup(self, question: str, evidence: list[dict]) -> _Assessment:
        a = _Assessment()
        targets = self._question_identifiers(question)
        direct = _filter_direct(evidence)
        found = []
        for item in direct:
            if item.get("exact_match") or item.get("exact_identifier_match"):
                found.append(item)
                continue
            names = sig.defined_names(item)
            if any(t in names or t.split(".")[-1] in names for t in targets):
                found.append(item)
        label = ", ".join(targets) or "requested symbol"
        a.add_stage(
            "symbol_definition",
            found,
            f"Definition of {label} found: {_describe_many(found)}",
            f"No definition of {label} found in direct (non-test, non-admin, non-migration) source files.",
        )
        if not found:
            for t in targets:
                a.want("symbol_queries", t, t.split(".")[-1])
                a.want("text_queries", f"class {t}", f"def {t}")
        return a

    @staticmethod
    def _covers(item: dict, file: str, line: int) -> bool:
        try:
            return (
                sig.normalize_path(item.get("file")) == sig.normalize_path(file)
                and int(item.get("start_line")) <= int(line) <= int(item.get("end_line"))
            )
        except (TypeError, ValueError):
            return False

    def _assess_call_site(self, question: str, evidence: list[dict], coverage: dict | None = None) -> _Assessment:
        a = _Assessment()
        targets = self._question_identifiers(question) or sorted(sig.domain_terms(question))[:2]
        direct = _filter_direct(evidence)
        definitions, call_sites = [], []
        for item in direct:
            content = sig.evidence_text(item)
            classes = sig.defined_names(item)
            for t in targets:
                short = t.split(".")[-1]
                if short in sig.class_names(item) or item.get("symbol") == short:
                    definitions.append(item)
                    break
                if re.search(rf"\b{re.escape(short)}\b", content) and short not in classes:
                    call_sites.append(item)
                    break
        label = ", ".join(targets)
        a.add_stage(
            "call_sites", call_sites,
            f"Code referencing {label}: {_describe_many(call_sites, 4)}",
            f"No retrieved runtime code references {label}.",
        )
        a.add_stage(
            "target_definition", definitions,
            f"Definition: {_describe_many(definitions)}",
            f"Definition of {label} not retrieved.",
        )
        if not call_sites:
            a.want("caller_queries", *targets)
            a.want("text_queries", *(f"{t.split('.')[-1]}." for t in targets))
        if not definitions:
            a.want("symbol_queries", *targets)

        # Completeness: a call-site list is only exhaustive when a
        # repository-wide reference search ran and every site it found is in
        # the evidence.
        references = (coverage or {}).get("references", {})
        searched = [references[t.split(".")[-1]] for t in targets if t.split(".")[-1] in references]
        if not searched:
            a.notes.append(
                f"No repository-wide reference search for {label} was recorded; the retrieved call sites "
                "may be incomplete. Do not claim the list is exhaustive."
            )
        for cov in searched:
            sites = cov.get("sites", [])
            unretrieved = [s for s in sites if not any(self._covers(i, s["file"], s["line"]) for i in evidence)]
            if not cov.get("complete"):
                a.unverified.append(
                    f"The repository-wide search for {cov['identifier']} hit its result limit; more references may exist."
                )
            if unretrieved:
                a.unverified.append(
                    f"{len(unretrieved)} of {len(sites)} reference sites of {cov['identifier']} found by the "
                    f"repository-wide search are not in the evidence: "
                    + ", ".join(f"{s.get('symbol') or '?'} ({s['file']}:{s['line']})" for s in unretrieved[:4])
                )
                for s in unretrieved[:3]:
                    if s.get("symbol"):
                        a.want("symbol_queries", s["symbol"].split(".")[0])
            a.notes.extend(self._crud_notes(cov, evidence))
        return a

    def _crud_notes(self, cov: dict, evidence: list[dict]) -> list[str]:
        """Per-site CRUD operations and HTTP methods of a reference search, from the evidence."""
        name = cov["identifier"]
        lines, methods = [], set()
        for site in cov.get("sites", []):
            item = next((i for i in evidence if self._covers(i, site["file"], site["line"])), None)
            if item is None:
                continue
            symbol = site.get("symbol") or item.get("qualified_name") or item.get("symbol") or ""
            # Only the site's own method counts (the item may be the whole class).
            text = sig.evidence_text(item)
            code = sig.method_body(text, symbol.split(".")[-1]) if "." in symbol else None
            code = code or text
            ops = [
                op for op in sig.orm_operations(code, self._line_of(item, code))
                if op["model"] == name or (op["model"] is None and name in code)
            ]
            kinds = sorted({op["kind"] for op in ops})
            http = symbol.split(".")[-1].lower() if "." in symbol else ""
            http = {"list": "get", "retrieve": "get", "create": "post", "update": "put",
                    "partial_update": "patch", "destroy": "delete"}.get(http, http)
            if http in ("get", "post", "put", "patch", "delete"):
                methods.add(http.upper())
            what = "/".join(kinds) if kinds else "reference only, no ORM call"
            lines.append(
                f"{symbol or site['file']} [{item.get('evidence_id')}] {what}"
                + (f", {http.upper()} handler" if http in ("get", "post", "put", "patch", "delete") else "")
            )
        if not lines:
            return []
        complete = cov.get("complete") and len(lines) == len(cov.get("sites", []))
        note = (
            f"Repository-wide search for {name}: {cov.get('total_lines', len(lines))} reference lines in "
            f"{len(cov.get('sites', []))} runtime code sites"
            + (f" (+{cov['indirect_lines']} in admin/tests/migrations, not runtime)" if cov.get("indirect_lines") else "")
            + ". Sites: " + "; ".join(lines) + "."
        )
        if complete:
            missing = [m for m in ("GET", "POST", "PUT", "PATCH", "DELETE") if m not in methods]
            note += (
                " The search is complete, so this list is exhaustive."
                + (f" No {'/'.join(missing)} handler references {name}." if missing and methods else "")
            )
        else:
            note += " The search is NOT complete; do not claim this list is exhaustive."
        return [note]

    def _assess_symbol_trace(self, question: str, evidence: list[dict]) -> _Assessment:
        """A named function: its full definition, the helpers it calls and its callers."""
        a = _Assessment()
        direct = _filter_direct(evidence)
        for target in self.function_targets(question)[:2]:
            definitions = [i for i in direct if sig.method_body(sig.evidence_text(i), target)]
            body = sig.method_body(sig.evidence_text(definitions[0]), target) if definitions else ""
            a.add_stage(
                "target_definition", definitions,
                f"Definition of {target}: {_describe_many(definitions)}",
                f"The definition of {target} was not retrieved.",
            )
            if not definitions:
                a.want("symbol_queries", target)
                a.want("text_queries", f"def {target}", f"{target}(")
                continue

            helpers = [h for h in sig.helper_calls(body) if h != target]
            resolved, unresolved = [], []
            for helper in helpers:
                found = [i for i in direct if sig.method_body(sig.evidence_text(i), helper)]
                (resolved if found else unresolved).append(helper)
                resolved_items = found
                for item in resolved_items:
                    if item not in definitions:
                        definitions.append(item)
            a.add_stage(
                "helper_calls", definitions if not unresolved else [],
                (f"{target} calls {', '.join(resolved)}; definitions retrieved." if resolved
                 else f"{target} calls no repository helper functions; its body performs the work directly."),
                f"{target} calls {', '.join(unresolved)}, whose definitions were not retrieved.",
            )
            a.want("symbol_queries", *unresolved[:3])

            call_re = re.compile(rf"(?:\.|\b){re.escape(target)}\s*\(")
            callers = [
                i for i in direct
                if call_re.search(re.sub(rf"def\s+{re.escape(target)}\s*\(", "", sig.evidence_text(i)))
                and (i not in definitions or call_re.search(sig.evidence_text(i).replace(body, "")))
            ]
            a.add_stage(
                "callers", callers,
                f"{target} is called from: {_describe_many(callers, 3)}",
                f"No code calling {target} was retrieved.",
            )
            if not callers:
                a.want("caller_queries", target)

            ops = sig.orm_operations(body, self._line_of(definitions[0], body))
            if ops:
                described = "; ".join(
                    f"{op['kind']} {op['model'] or '(instance)'}: `{op['code'][:90]}` (line {op['line']})" for op in ops
                )
                models = sorted({op["model"] for op in ops if op["model"]})
                a.notes.append(
                    f"Database operations inside {target} [{definitions[0].get('evidence_id')}]: {described}. "
                    f"Operations on {', '.join(models) or 'these models'} in OTHER functions/views are not part of "
                    f"{target} and must not be cited as its behaviour."
                )
            else:
                a.notes.append(f"{target} [{definitions[0].get('evidence_id')}] performs no ORM/database operation directly.")
        if not a.stages:
            return self._assess_code_explanation(question, evidence)
        return a

    @staticmethod
    def _line_of(item: dict, fragment: str) -> int:
        """Absolute line number at which *fragment* starts inside *item*."""
        text = sig.evidence_text(item)
        idx = text.find(fragment) if fragment else -1
        start = int(item.get("start_line") or 1)
        return start + (text[:idx].count("\n") if idx > 0 else 0)

    def _assess_calculation(self, question: str, evidence: list[dict], coverage: dict | None = None) -> _Assessment:
        """Each asked quantity must be computed in retrieved code; frontend and backend kept apart."""
        a = _Assessment()
        direct = _filter_direct(evidence)
        repo_wide = (coverage or {}).get("computations", {})
        for term in sig.numeric_terms(question):
            sites = []
            for item in direct:
                comps = sig.computations(sig.evidence_text(item), term, int(item.get("start_line") or 1))
                if comps:
                    sites.append((item, comps))
            items = [i for i, _ in sites]
            a.add_stage(
                f"computation_{term}", items,
                f"{term} computed in: {_describe_many(items, 3)}",
                f"No code computing {term} was retrieved.",
            )
            if not items:
                a.want("text_queries", term)
                continue
            # One compact fact per quantity (the answer prompt keeps only a few).
            by_side = {"frontend": [], "backend": []}
            for item, comps in sites:
                side = "frontend" if sig.is_frontend_runtime(item.get("file")) else "backend"
                where = item.get("qualified_name") or item.get("symbol") or sig.normalize_path(item.get("file"))
                entry = f"{where} [{item.get('evidence_id')}] `{comps[0]['code'][:80]}`"
                if not any(entry.split(" [")[0] == e.split(" [")[0] for e in by_side[side]):
                    by_side[side].append(entry)
            cov = repo_wide.get(term)
            parts = []
            for side in ("frontend", "backend"):
                if by_side[side]:
                    parts.append(f"{side.upper()}: " + "; ".join(by_side[side][:3]))
                elif cov and cov.get("complete") and not cov.get(side):
                    parts.append(f"{side.upper()}: none (a complete repository-wide search found no {side} computation)")
                else:
                    parts.append(f"{side.upper()}: none in the evidence (do not claim the {side} does not compute it)")
            note = f"Computation of {term}. " + ". ".join(parts) + "."
            if cov and cov.get("complete"):
                unretrieved = [
                    s for s in cov.get("frontend", []) + cov.get("backend", [])
                    if not any(self._covers(i, s["file"], s["line"]) for i in evidence)
                ]
                if unretrieved:
                    note += " Also computed (not in evidence) at " + ", ".join(
                        f"{s['file']}:{s['line']}" for s in unretrieved[:3]) + "."
            a.notes.append(note)

        # Price/amount fields the computations read must be defined in evidence.
        fields = set()
        for item in direct:
            text = sig.evidence_text(item)
            if any(sig.computations(text, t) for t in sig.numeric_terms(question)):
                fields |= {f for f in re.findall(r"\.([a-z]+_(?:price|cost|amount)\w*|(?:price|cost|amount)_\w+)\b", text)}
        if fields:
            defined = [
                i for i in direct
                if any(re.search(rf"^\s*{re.escape(f)}\s*[:=]", sig.evidence_text(i), re.MULTILINE) for f in fields)
            ]
            a.add_stage(
                "source_fields", defined,
                f"Fields {', '.join(sorted(fields))} defined in: {_describe_many(defined)}",
                f"Definitions of the fields {', '.join(sorted(fields))} used in the calculation were not retrieved.",
            )
            if not defined:
                a.want("text_queries", *(f"{f} =" for f in sorted(fields)[:2]))
        return a

    def _assess_code_explanation(self, question: str, evidence: list[dict]) -> _Assessment:
        a = _Assessment()
        terms = sig.domain_terms(question)
        idents = self._question_identifiers(question)
        relevant = [
            item for item in _filter_direct(evidence)
            if (sig.is_relevant(item, terms) and sig.evidence_text(item).strip()) or item.get("exact_match")
        ]
        # A question naming code identifiers is only answered by code that
        # contains them; other on-topic code does not make it sufficient.
        named = [t.split(".")[-1].rstrip("()") for t in idents if not t.endswith((".html", ".css"))]
        if named:
            relevant = [
                item for item in relevant
                if any(re.search(rf"\b{re.escape(n)}\b", sig.evidence_text(item)) or n in sig.defined_names(item) for n in named)
            ]
        a.add_stage(
            "relevant_implementation", relevant,
            f"Implementation code: {_describe_many(relevant, 3)}",
            "No direct implementation code relevant to the question was retrieved.",
        )
        if not relevant:
            a.want("symbol_queries", *idents)
            a.want("text_queries", *sorted(terms)[:3])
        return a

    def _assess_database_query(self, question: str, evidence: list[dict]) -> _Assessment:
        a = _Assessment()
        terms = sig.domain_terms(question)
        wants_write = bool(self._WRITE_WORDS_RE.search(question))
        persistence = []
        for item in evidence:
            ops = sig.backend_persistence_ops(item)
            if (ops["writes"] or ops["reads"] or ops["raw_sql"]) and sig.is_relevant(item, terms):
                persistence.append(item)
        a.add_stage(
            "backend_persistence", persistence,
            f"Backend ORM/SQL operations: {_describe_many(persistence, 3)}",
            "No backend ORM/SQL operation relevant to the question was retrieved.",
        )
        if not persistence:
            a.want("text_queries", *(f"{t}" for t in sorted(terms)[:2]), ".objects.")
            a.want("symbol_queries", *self._question_identifiers(question))
        elif wants_write and not any(sig.backend_persistence_ops(i)["writes"] for i in persistence):
            a.unverified.append("Only ORM reads were retrieved; no backend write operation is visible.")
            a.want("text_queries", ".save(", ".objects.create")

        if self._GUEST_WORDS_RE.search(question):
            storage = [
                item for item in evidence
                if not sig.is_indirect(item.get("file"))
                and (sig.has_client_storage(sig.evidence_text(item)) or "request.session" in sig.evidence_text(item))
                and sig.is_relevant(item, terms)
            ]
            a.add_stage(
                "guest_storage", storage,
                f"Guest/client-side storage: {_describe_many(storage)}",
                "No code showing where guest state is stored was retrieved.",
            )
            branching = [
                item for item in evidence
                if not sig.is_indirect(item.get("file"))
                and self._AUTH_BRANCH_RE.search(sig.evidence_text(item))
                and sig.is_relevant(item, terms)
            ]
            a.add_stage(
                "authentication_branching", branching,
                f"Logic distinguishing logged-in vs guest: {_describe_many(branching)}",
                "No code deciding between guest and authenticated persistence was retrieved.",
            )
            if not storage:
                a.want("text_queries", "localStorage", "request.session")
            if not branching:
                a.want("text_queries", "isLoggedIn", "is_authenticated")
        return a

    def _assess_cache(self, question: str, evidence: list[dict]) -> _Assessment:
        """Cache usage needs code that reads/writes/wraps cached data.

        Cache-backend or Celery-broker configuration says where a cache would
        live, not what is cached, for how long, under which keys, or when it is
        invalidated or bypassed.
        """
        a = _Assessment()
        q = question or ""
        runtime = _filter_direct(evidence)
        config_items = [i for i in runtime if sig.has_cache_config(sig.evidence_text(i))]
        op_items = [i for i in runtime if sig.cache_operations(sig.evidence_text(i))]
        broker_items = [i for i in runtime if sig.has_broker_config(sig.evidence_text(i))]

        if re.search(r"\bredis\b", q, re.IGNORECASE) or not op_items:
            a.add_stage(
                "cache_configuration", config_items,
                f"Cache backend configuration: {_describe_many(config_items)}",
                "No cache backend configuration (e.g. CACHES / django_redis) was retrieved.",
            )
        ops = sorted({op for i in op_items for op in sig.cache_operations(sig.evidence_text(i))})
        a.add_stage(
            "cache_operations", op_items,
            f"Cache operations ({', '.join(ops[:4])}): {_describe_many(op_items, 3)}",
            "No code that reads, writes or wraps data in the cache (cache.get/set, cache_page, ...) was retrieved; "
            "what is cached is not verified.",
        )
        for aspect, (asked_re, code_re) in sig.CACHE_ASPECTS.items():
            if not asked_re.search(q):
                continue
            # A TTL in CACHES config applies to cache usage only once usage is verified.
            pool = op_items + (config_items if op_items else [])
            items = [i for i in pool if code_re.search(sig.evidence_text(i))]
            label = aspect.replace("cache_", "")
            a.add_stage(aspect, items, f"Cache {label}: {_describe_many(items)}",
                        f"No cache {label} behaviour was retrieved.")

        if not op_items:
            a.want("text_queries", "cache_page", "cache.get(", "cache.set(", "cache.delete(")
            a.want("import_queries", "django.core.cache", "django.views.decorators.cache")
            if config_items or broker_items:
                a.notes.append(
                    "Only cache/broker configuration was retrieved "
                    f"({_describe_many(config_items + broker_items)}). Configuration shows which backend a cache "
                    "would use, not what data is cached, its TTL, keys, invalidation or bypass; do not describe "
                    "those as verified."
                )
        elif "cache_invalidation" in a.missing_stages:
            a.want("text_queries", "cache.delete(", "cache.clear(")
        if broker_items:
            a.notes.append(
                f"Redis as the Celery broker/result backend ({_describe_many(broker_items, 1)}) is task queueing, "
                "not caching."
            )
        return a

    def _assess_architecture(self, question: str, evidence: list[dict]) -> _Assessment:
        a = _Assessment()
        direct = _filter_direct(evidence)
        has_frontend = any(sig.is_frontend_runtime(i.get("file")) for i in evidence) or bool(
            self._FRONTEND_WORDS_RE.search(question)
        )

        def basename(item):
            return sig.normalize_path(item.get("file")).lower().rsplit("/", 1)[-1]

        backend_entry = [
            i for i in direct
            if not sig.is_frontend_runtime(i.get("file"))
            and (basename(i) in self._BACKEND_ENTRY_NAMES or sig.has_entry_point_signal(sig.evidence_text(i)))
        ]
        config = [i for i in direct if sig.file_role(i.get("file")) == sig.ROLE_CONFIG]
        routing = [i for i in direct if not sig.is_frontend_runtime(i.get("file")) and sig.extract_routes(sig.evidence_text(i))]
        persistence_cfg = [
            i for i in direct
            if sig.has_db_config(sig.evidence_text(i)) or self._MODEL_CLASS_RE.search(sig.evidence_text(i))
        ]
        a.add_stage("backend_entry_point", backend_entry,
                    f"Backend entry point(s): {_describe_many(backend_entry)}",
                    "No backend entry point (e.g. manage.py/wsgi.py/server bootstrap) retrieved.")
        a.add_stage("configuration", config,
                    f"Configuration: {_describe_many(config)}",
                    "No runtime configuration file (e.g. settings / app config) retrieved.")
        a.add_stage("backend_routing", routing,
                    f"URL routing: {_describe_many(routing)}",
                    "No backend URL route registrations retrieved.")
        a.add_stage("persistence_configuration", persistence_cfg,
                    f"Persistence configuration/models: {_describe_many(persistence_cfg)}",
                    "No database configuration or ORM model definitions retrieved.")
        if not backend_entry:
            a.want("text_queries", "get_wsgi_application", "execute_from_command_line")
        if not config:
            a.want("text_queries", "INSTALLED_APPS")
        if not routing:
            a.want("text_queries", "urlpatterns")
        if not persistence_cfg:
            a.want("text_queries", "DATABASES", "DATABASE_URL")

        if has_frontend:
            frontend_entry = [
                i for i in direct
                if sig.is_frontend_runtime(i.get("file"))
                and basename(i) in self._FRONTEND_ENTRY_NAMES
                and sig.has_entry_point_signal(sig.evidence_text(i))
            ]
            api_comm = [i for i in direct if sig.is_frontend_runtime(i.get("file")) and sig.extract_http_calls(sig.evidence_text(i))]
            a.add_stage("frontend_entry_point", frontend_entry,
                        f"Frontend bootstrap: {_describe_many(frontend_entry)}",
                        "No frontend bootstrap file (e.g. bootstrapApplication/createRoot) retrieved; components are not entry points.")
            a.add_stage("api_communication", api_comm,
                        f"Frontend HTTP calls: {_describe_many(api_comm)}",
                        "No frontend code issuing HTTP calls to the backend retrieved.")
            if not frontend_entry:
                a.want("text_queries", "bootstrapApplication", "createRoot(")
            if not api_comm:
                a.want("text_queries", "apiUrl", "HttpClient")

        if self._AUTH_WORDS_RE.search(question):
            auth = [i for i in direct if sig.has_auth_signal(sig.evidence_text(i))]
            a.add_stage("authentication", auth,
                        f"Authentication: {_describe_many(auth)}",
                        "No authentication configuration or implementation retrieved.")
            if not auth:
                a.want("text_queries", "DEFAULT_AUTHENTICATION_CLASSES", "permission_classes")
        return a

    def _assess_flow(self, question: str, question_type: str | None, evidence: list[dict]) -> _Assessment:
        a = _Assessment()
        q = question or ""
        terms = sig.domain_terms(q)
        include_frontend = (
            question_type == "end_to_end_flow"
            or bool(self._FRONTEND_WORDS_RE.search(q))
            or bool(self._ACTOR_WORDS_RE.search(q))
        )
        # A user-initiated flow is only complete from the frontend method that
        # starts it to the code that consumes the response.
        full_flow = include_frontend and (
            question_type == "end_to_end_flow" or bool(self._ACTOR_WORDS_RE.search(q))
        )
        include_ui = include_frontend and (full_flow or bool(self._UI_WORDS_RE.search(q)))
        # Persistence is part of a full flow only when the question is about
        # reading or changing data (a login flow need not touch the ORM itself).
        # "frontend session update" is frontend state, not a data write.
        backend_q = re.sub(r"\b(?:(?:frontend|client|ui)\s+)?(?:session|state|ui|view)\s+updat\w*", " ", q, flags=re.IGNORECASE)
        data_flow = full_flow and bool(self._WRITE_WORDS_RE.search(backend_q) or self._DATA_READ_WORDS_RE.search(backend_q))
        include_persistence = data_flow or bool(self._PERSIST_WORDS_RE.search(q)) or (
            question_type == "feature_flow" and bool(self._WRITE_WORDS_RE.search(q)) and not include_frontend
        )
        wants_write = bool(self._WRITE_WORDS_RE.search(q))
        include_response = full_flow or bool(self._RESPONSE_WORDS_RE.search(q)) or bool(self._FRONTEND_HANDLING_WORDS_RE.search(q))
        include_frontend_handling = include_frontend and (full_flow or bool(self._FRONTEND_HANDLING_WORDS_RE.search(q)))
        include_auth = full_flow and bool(re.search(r"\bauthenticated\b|\blogged[- ]in\b|\bsigned[- ]in\b", q, re.IGNORECASE))

        runtime = _filter_direct(evidence)
        frontend_items = [i for i in runtime if sig.is_frontend_runtime(i.get("file"))]
        backend_items = [i for i in runtime if not sig.is_frontend_runtime(i.get("file"))]

        # --- client service calls -------------------------------------------------
        # Only the calls most relevant to the question count as the flow's
        # request(s); other calls in the same service are not part of it.
        item_calls = []
        for item in frontend_items:
            for call in sig.extract_http_calls(sig.evidence_text(item)):
                item_calls.append((item, call))
        # A submission/update flow is carried by a mutating request; a GET
        # (e.g. a session check) cannot stand in for a login POST.
        mutating_only = bool(sig._WRITE_INTENT_RE.search(q)) and not sig._READ_INTENT_RE.search(q)
        candidate_calls = [c for _, c in item_calls if not mutating_only or c["method"] != "get"]
        best_calls = sig.rank_http_calls(candidate_calls, terms, q)
        best_keys = {(c["method"], tuple(c["segments"])) for c in best_calls}
        client_items, relevant_calls = [], []
        for item, call in item_calls:
            if (call["method"], tuple(call["segments"])) not in best_keys:
                continue
            if item not in client_items:
                client_items.append(item)
            if not any(c["method"] == call["method"] and c["segments"] == call["segments"] for c in relevant_calls):
                relevant_calls.append(call)
        # The client methods that issue the flow's request(s); other methods
        # of the same service are a different flow.
        client_methods = {c["enclosing"] for c in relevant_calls if c.get("enclosing")}
        if not client_methods:
            for item in client_items:
                client_methods |= sig.defined_names(item)

        # Files that issue HTTP calls (or are named as services) are client
        # services; their other methods are not UI event handlers.
        service_files = {
            sig.normalize_path(item.get("file")) for item, _ in item_calls
        } | {
            sig.normalize_path(i.get("file")) for i in frontend_items
            if re.search(r"service|/api[/.]|client", sig.normalize_path(i.get("file")).lower())
        }

        # --- UI event handlers ----------------------------------------------------
        # Any frontend code that calls a flow client method starts the flow
        # (a component handler, or another service such as a session restore);
        # other code in service files is not a UI handler.
        ui_items, ui_connected_items = [], []
        for item in frontend_items:
            if item in client_items:
                continue
            content = sig.evidence_text(item)
            calls = sig.member_calls(content)
            if any(method in client_methods for _, method in calls):
                ui_items.append(item)
                ui_connected_items.append(item)
                continue
            if sig.normalize_path(item.get("file")) in service_files:
                continue
            if (sig.has_ui_event(content) or calls) and sig.is_relevant(item, terms):
                ui_items.append(item)

        # --- routes ---------------------------------------------------------------
        route_items, matched = [], []  # matched: (call, route, route_item)
        all_routes = []
        for item in backend_items:
            routes = sig.extract_routes(sig.evidence_text(item))
            if routes:
                all_routes.append((item, routes))
        for item, routes in all_routes:
            hit = False
            for call in relevant_calls:
                for route in routes:
                    if sig.url_matches(call["segments"], route["segments"]):
                        matched.append((call, route, item))
                        hit = True
            if hit:
                route_items.append(item)
        if not relevant_calls:
            # No client stage to match against: accept routes that name the domain.
            for item, routes in all_routes:
                if any(any(sig._stem(t) in "/".join(r["segments"]) + r["handler"].lower() for t in terms) for r in routes):
                    route_items.append(item)
                    matched.extend((None, r, item) for r in routes
                                   if any(sig._stem(t) in "/".join(r["segments"]) + r["handler"].lower() for t in terms))

        # --- backend handlers -----------------------------------------------------
        routed_handlers = {route["handler"] for _, route, _ in matched}
        handler_items = [
            i for i in backend_items
            if routed_handlers & (sig.class_names(i) | sig.defined_names(i)) and i not in route_items
        ]
        connected_handler_names = set()
        for item in handler_items:
            connected_handler_names |= routed_handlers & (sig.class_names(item) | sig.defined_names(item))
        unrouted_handlers = [
            i for i in backend_items
            if i not in handler_items and i not in route_items
            and (sig.handled_http_methods(sig.evidence_text(i)) or "request" in sig.evidence_text(i))
            and sig.is_relevant(i, terms)
        ]

        # HTTP method agreement between client call and handler implementation
        # (methods are unioned over every retrieved item of the same handler).
        method_mismatches = []
        for call, route, _ in matched:
            if not call:
                continue
            handled = set()
            for item in handler_items:
                if route["handler"] in (sig.class_names(item) | sig.defined_names(item)):
                    handled |= sig.handled_http_methods(sig.evidence_text(item))
            if handled and call["method"] not in handled:
                message = (
                    f"{route['handler']} evidence implements {', '.join(sorted(m.upper() for m in handled))} "
                    f"but the client sends {call['method'].upper()} to {call['path']}; "
                    f"the {call['method'].upper()} implementation is not verified."
                )
                if message not in method_mismatches:
                    method_mismatches.append(message)

        # --- persistence ----------------------------------------------------------
        # When the client's HTTP method is known, only the handler method for
        # it (e.g. CartSyncView.get for a GET) is the flow's backend code.
        request_methods = {call["method"] for call, _, _ in matched if call}
        handler_bodies: dict[int, str] = {}
        for item in handler_items:
            text = sig.evidence_text(item)
            bodies = [b for b in (sig.method_body(text, m) for m in request_methods) if b]
            if not bodies and request_methods & {"post", "put", "patch"}:
                bodies = [b for b in (sig.method_body(text, m) for m in ("create", "update", "partial_update")) if b]
            if not bodies and "get" in request_methods:
                bodies = [b for b in (sig.method_body(text, m) for m in ("list", "retrieve")) if b]
            handler_bodies[id(item)] = "\n".join(bodies) if bodies else text

        def code_of(item):
            return handler_bodies.get(id(item), sig.evidence_text(item))

        def has_ops(item):
            if not sig.is_backend_runtime(item.get("file")):
                return False
            code = code_of(item)
            writes, reads = sig.orm_writes(code), sig.orm_reads(code)
            return bool((writes if wants_write else (writes or reads)) or sig.has_raw_sql(code))

        # When a route was matched we know which handler the flow needs; an
        # unrelated view (e.g. a session GET for a login POST) must not stand in.
        chain_handlers = handler_items if matched else unrouted_handlers
        handler_refs = set()
        for item in chain_handlers:
            handler_refs |= set(re.findall(r"\b[A-Z][A-Za-z0-9]+\b", code_of(item)))
        if chain_handlers:
            # Persistence of *this* flow: ORM operations in its handler, or in
            # code the handler references, plus the referenced model classes.
            ops_items = [
                i for i in backend_items
                if has_ops(i) and (i in chain_handlers or handler_refs & sig.class_names(i))
            ]
            model_items = [
                i for i in backend_items
                if i not in ops_items and handler_refs & sig.class_names(i)
                and self._MODEL_CLASS_RE.search(sig.evidence_text(i))
            ]
        else:
            ops_items = [i for i in backend_items if has_ops(i) and sig.is_relevant(i, terms)]
            model_items = []
        persistence_items = ops_items + model_items if ops_items else []
        handler_to_db = bool(ops_items) and bool(chain_handlers)
        client_storage_only = [
            i for i in frontend_items if sig.has_client_storage(sig.evidence_text(i)) and sig.is_relevant(i, terms)
        ]

        # --- serialization --------------------------------------------------------
        serializer_names = set()
        for item in chain_handlers:
            serializer_names |= set(re.findall(r"\b([A-Z]\w*Serializer)\s*\(", code_of(item)))
        serializer_items = [i for i in backend_items if serializer_names & sig.class_names(i)]

        # --- response handling ----------------------------------------------------
        response_items = [i for i in chain_handlers if sig.has_backend_response(code_of(i))]
        # The response is handled only where code actually reads it: a
        # callback that uses its argument, an assignment, or a caller that
        # subscribes to the returned observable/promise.
        # A success callback that reacts without reading the body
        # ("acknowledged") is handling too, never fire-and-forget.
        frontend_handling_items, discarded, acknowledged = [], [], []
        for item, call in item_calls:
            if (call["method"], tuple(call["segments"])) not in best_keys:
                continue
            usage = sig.response_consumption(sig.evidence_text(item), call["position"])
            if usage == "consumed":
                frontend_handling_items.append(item)
            elif usage == "acknowledged":
                acknowledged.append(item)
            elif usage == "ignored":
                discarded.append((item, call))
            elif usage == "returned" and call.get("enclosing"):
                for caller in ui_connected_items:
                    text = sig.evidence_text(caller)
                    outcomes = {sig.response_consumption(text, pos) for pos in sig.member_call_positions(text, call["enclosing"])}
                    if "consumed" in outcomes:
                        frontend_handling_items.append(caller)
                    elif "acknowledged" in outcomes:
                        acknowledged.append(caller)
                    elif outcomes == {"ignored"}:
                        discarded.append((caller, call))
        # Callers' success callbacks run on the response even when the service
        # already reads it (e.g. ``tap`` in Auth.register, navigation in the component).
        for caller in ui_connected_items:
            text = sig.evidence_text(caller)
            for method in client_methods:
                if "acknowledged" in {sig.response_consumption(text, p) for p in sig.member_call_positions(text, method)}:
                    acknowledged.append(caller)
        acknowledged = [i for i in {id(i): i for i in acknowledged}.values() if i not in frontend_handling_items]
        frontend_handling_items = list({id(i): i for i in frontend_handling_items + acknowledged}.values())
        discarded_items = [] if frontend_handling_items else list({id(i): i for i, _ in discarded}.values())

        # --- authentication ---------------------------------------------------------
        # The backend must enforce it; a client-side guard alone proves nothing.
        auth_items = [
            i for i in chain_handlers
            if re.search(r"IsAuthenticated|permission_classes|login_required|@authenticated|requireAuth|"
                         r"Depends\(\s*get_current_user", sig.evidence_text(i))
        ]
        client_guards = [i for i in client_items if re.search(r"isLoggedIn|isAuthenticated|currentUser", sig.evidence_text(i))]

        # --- build ordered stages + connections -----------------------------------
        chain: list[tuple[FlowStageStatus, Optional[bool], str]] = []

        def stage(name, items, details, missing):
            st = a.add_stage(name, items, details, missing)
            return st

        if include_ui:
            st = stage("ui_event_handler", ui_items,
                       f"UI handler: {_describe_many(ui_connected_items or ui_items)}",
                       "No UI event handler relevant to the question was retrieved.")
            chain.append((st, bool(ui_connected_items),
                          "UI handler does not call any retrieved client-service method." if ui_items else ""))
        if include_frontend:
            st = stage("client_service", client_items,
                       f"Client HTTP call(s): {', '.join(sorted({c['method'].upper() + ' ' + c['path'] for c in relevant_calls})[:3])} in {_describe_many(client_items)}",
                       "No client code issuing the relevant HTTP request was retrieved.")
            chain.append((st, bool(matched) and bool(relevant_calls),
                          "Client request URL does not match any retrieved backend route." if client_items else ""))
        st = stage("url_routing", route_items,
                   "Routes: " + "; ".join(sorted({f"{r['path']} -> {r['handler']}" for _, r, _ in matched})[:4]) + f" ({_describe_many(route_items)})",
                   "No backend route registration matching the request was retrieved.")
        chain.append((st, bool(handler_items),
                      f"Route handler(s) {', '.join(sorted(routed_handlers)) or '?'} not found in retrieved code." if route_items else ""))
        st = stage("backend_handler", chain_handlers,
                   f"Handler: {_describe_many(chain_handlers)}",
                   f"Handler {', '.join(sorted(routed_handlers))} for the matched route was not retrieved."
                   if routed_handlers else "No backend handler implementation was retrieved.")
        if include_persistence:
            chain.append((st, handler_to_db,
                          "Handler code does not show the ORM/database operation." if chain_handlers else ""))
            st = stage("persistence", persistence_items,
                       f"ORM/database operations: {_describe_many(persistence_items)}",
                       "No backend database operation (ORM/SQL) was retrieved for this flow.")
        if include_response:
            chain.append((st, bool(response_items),
                          "Handler response construction not visible." if chain_handlers else ""))
            st = stage("backend_response", response_items,
                       f"Response construction: {_describe_many(response_items)}",
                       "No backend response construction retrieved for the handler.")
        if include_frontend_handling:
            chain.append((st, bool(frontend_handling_items), "Frontend handling of the response not verified." if response_items else ""))
            discarded_desc = "; ".join(dict.fromkeys(
                f"{call['method'].upper()} {call['path'].split('}')[-1]} in "
                f"{call.get('enclosing') or 'code'} ({sig.normalize_path(item.get('file'))}) [{item.get('evidence_id')}]"
                for item, call in discarded if call.get("enclosing")
            )) or "the client call"
            readers = [i for i in frontend_handling_items if i not in acknowledged]
            handling_desc = "; ".join(filter(None, [
                f"Frontend code consuming the response: {_describe_many(readers)}" if readers else "",
                f"success callback runs when the response arrives (does not read its body): {_describe_many(acknowledged)}"
                if acknowledged else "",
            ]))
            st = a.add_stage(
                "frontend_handling", frontend_handling_items,
                handling_desc,
                "No frontend code that reads the response was retrieved (a call alone does not prove the response is used).",
                absent_items=discarded_items,
            )
            if st.verified_absent:
                st.details = (
                    f"The response is NOT consumed: {discarded_desc} subscribes without using the result "
                    "(fire-and-forget). Any UI/state change happens locally, before or independent of the response."
                )
                a.notes.append(st.details)
        chain.append((st, True, ""))

        # Supporting requirements outside the request chain.
        if include_response and serializer_names:
            a.add_stage(
                "serialization", serializer_items,
                f"Serializer {', '.join(sorted(serializer_names))}: {_describe_many(serializer_items)}",
                f"Serializer {', '.join(sorted(serializer_names))} used by the handler was not retrieved.",
            )
            if not serializer_items:
                a.want("symbol_queries", *sorted(serializer_names)[:2])
        if include_auth:
            a.add_stage(
                "authentication_check", auth_items,
                f"Backend access control on the handler: {_describe_many(auth_items)}"
                + (f"; client-side login guard in {_describe_many(client_guards, 1)}" if client_guards else ""),
                "No backend authentication/permission check on the flow's handler was retrieved.",
            )
            if not auth_items and routed_handlers:
                a.want("text_queries", *(f"class {h}" for h in sorted(routed_handlers)[:2]))

        # Stages the question says the flow must reach ("... then through to
        # checkout"): a verified earlier stage (e.g. the guest-cart merge) does
        # not stand in for them.
        for dest in sig.flow_destinations(q):
            if any(dest in s.stage for s in a.stages):
                continue
            dest_items = [i for i in runtime if sig.implements_term(i, dest)]
            a.add_stage(
                dest, dest_items,
                f"{dest} stage: {_describe_many(dest_items)}",
                f"No {dest} implementation was retrieved; the flow is not verified through to {dest}.",
            )
            if not dest_items:
                a.want("symbol_queries", f"{dest.capitalize()}View", dest.capitalize())
                a.want("text_queries", f"{dest}/")
                continue
            # The destination must act on the flow's data (e.g. checkout reads the cart).
            other_terms = {t for t in terms if sig._stem(t) != sig._stem(dest)}
            if other_terms and not any(
                sig.relevance_hits(i, other_terms)[1] for i in dest_items
            ):
                a.unverified.append(
                    f"{dest} code ({_describe_many(dest_items)}) was retrieved but does not reference "
                    f"{', '.join(sorted(other_terms)[:3])}; the hand-off to {dest} is not verified."
                )

        # Literal +1/-1 deltas passed to the client method are increments/decrements.
        deltas = []
        for item in ui_connected_items:
            for method, delta in sig.delta_calls(sig.evidence_text(item)):
                if method in client_methods:
                    deltas.append(f"{'+1 (increment)' if delta > 0 else '-1 (decrement)'} to {method} in "
                                  f"{sig.normalize_path(item.get('file'))} [{item.get('evidence_id')}]")
        if deltas:
            backend_delta = [
                f"`{c['code']}` [{i.get('evidence_id')}]"
                for i in chain_handlers for c in sig.computations(code_of(i), "quantity")
            ]
            a.notes.append(
                "Quantity changes are sent as deltas: " + "; ".join(dict.fromkeys(deltas))
                + (". The backend applies the delta: " + "; ".join(backend_delta[:2]) if backend_delta else "")
                + "."
            )

        for idx, (status, connected, reason) in enumerate(chain):
            status.connected_to_next = bool(connected)
            nxt = chain[idx + 1][0] if idx + 1 < len(chain) else None
            if nxt is None:
                status.connected_to_next = True
                continue
            if nxt.verified_absent:
                status.connected_to_next = True
                continue
            if status.present and nxt.present and not connected:
                a.unverified.append(f"{status.stage} -> {nxt.stage}: {reason or 'connection not verified in code.'}")

        if unrouted_handlers and not handler_items:
            names = ", ".join(sorted({n for i in unrouted_handlers for n in sig.class_names(i)} or {i.get('symbol') or '?' for i in unrouted_handlers}))
            a.unverified.append(
                f"Backend handler {names} is present but no retrieved route or client call connects it to the requested operation."
            )
        a.unverified.extend(method_mismatches)
        if include_persistence and not persistence_items and client_storage_only:
            a.notes.append(
                f"Only client-side storage was retrieved ({_describe_many(client_storage_only)}); "
                "this is not backend database persistence."
            )

        # --- targeted follow-up queries -------------------------------------------
        missing = set(a.missing_stages)
        if "client_service" in missing:
            ui_methods = sorted({m for i in ui_items for _, m in sig.member_calls(sig.evidence_text(i))})
            a.want("symbol_queries", *ui_methods[:3])
            # The client URL string contains the backend route path, so the
            # matched route paths locate the calling client code directly.
            route_paths = [r["path"] for _, r, _ in matched if r.get("path") and "<" not in r["path"]]
            a.want("text_queries", *list(dict.fromkeys(route_paths))[:2])
            # Routes not yet retrieved: URL fragments named after the domain.
            a.want("text_queries", *(f"{t}/" for t in sorted(terms)[:3]))
        if routed_handlers and not handler_items:
            a.want("symbol_queries", *sorted(routed_handlers))
        if "url_routing" in missing or ("backend_handler" in missing and not routed_handlers):
            for call in relevant_calls[:3]:
                a.want("text_queries", *sig.route_search_terms(call["segments"])[:1])
                a.want("symbol_queries", *sig.handler_name_hypotheses(call["segments"])[:1])
            if not relevant_calls:
                a.want("text_queries", *(f"{t}/" for t in sorted(terms)[:2]))
        if ("ui_event_handler" in missing or "frontend_handling" in missing
                or (ui_items and not ui_connected_items)) and client_items:
            # Callers of the client method that issues the flow's request.
            methods = [c["enclosing"] for c in relevant_calls if c.get("enclosing")]
            methods.extend(i.get("symbol") for i in client_items if i.get("symbol") and i["symbol"][0].islower())
            a.want("caller_queries", *list(dict.fromkeys(methods))[:3])
        if "persistence" in missing and chain_handlers:
            known = set().union(*(sig.defined_names(i) for i in evidence)) if evidence else set()
            refs = sorted(r for r in handler_refs - known if r not in ("Response", "APIView", "IsAuthenticated", "AllowAny"))
            a.want("symbol_queries", *refs[:3])
        if "backend_response" in missing and routed_handlers:
            a.want("symbol_queries", *sorted(routed_handlers))
        # A client call seen only in a partial window: fetch its whole method.
        partial = [c for c in relevant_calls if not c.get("enclosing")]
        if partial and not client_methods:
            a.unverified.append(
                "The client HTTP call was retrieved without its enclosing method; the request is not verified "
                "from a complete method."
            )
            a.want("text_queries", *(c["path"].split("}")[-1] for c in partial[:1]))
        return a

    @classmethod
    def analyze_flow_stages(
        cls,
        question: str,
        question_type: str | None,
        evidence: list[dict],
    ) -> tuple[list[FlowStageStatus], list[str], list[str], dict]:
        """Analyze flow stages: (stages, missing_stages, unverified_connections, recommended_queries)."""
        a = cls.__new__(cls)._assess_flow(question, question_type, evidence)
        return a.stages, a.missing_stages, a.unverified, a.finalized_queries()

    @staticmethod
    def grounding_notes(evidence: list[dict]) -> list[str]:
        """Facts about what the evidence does and does not establish (used to ground answers)."""
        notes = []
        runtime = _filter_direct(evidence)
        orm_items = [i for i in runtime if any(sig.backend_persistence_ops(i)[k] for k in ("writes", "reads"))]
        if orm_items and not any(sig.backend_persistence_ops(i)["raw_sql"] for i in runtime):
            notes.append(
                "Database access in the evidence is ORM code only; no raw SQL was retrieved. "
                "Any SQL shown must be labelled as illustrative/inferred."
            )
        db_cfg = [i for i in runtime if sig.has_db_config(sig.evidence_text(i))]
        if db_cfg:
            notes.append(f"Database configuration evidence: {_describe_many(db_cfg)}.")
        else:
            notes.append("No database configuration was retrieved; do not name the database engine.")
        storage = [i for i in runtime if sig.is_frontend_runtime(i.get("file")) and sig.has_client_storage(sig.evidence_text(i))]
        if storage:
            notes.append(
                f"{_describe_many(storage)} uses browser storage (localStorage/sessionStorage): "
                "client-side state, not backend database persistence."
            )
        admin = [i for i in evidence if sig.file_role(i.get("file")) == sig.ROLE_ADMIN]
        if admin:
            notes.append(f"{_describe_many(admin)} is an admin registration; it does not show runtime reads/writes.")
        return notes

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
        search_coverage: dict | None = None,
    ) -> EvidenceEvaluation:
        """Evaluate evidence completeness with question-type-aware requirements.

        *search_coverage* records repository-wide searches run by retrieval
        (``references`` per identifier, ``computations`` per quantity); only
        with it may a list be called exhaustive or an absence be asserted.
        """
        if not evidence:
            return self._empty_evidence_fallback(question, current_round, max_rounds)

        profile = self.evaluation_profile(question, question_type)
        notes = self.grounding_notes(evidence)

        if profile == "general":
            result = self._llm_evaluation(question, evidence, question_type, current_round, max_rounds)
            result.evaluation_profile = profile
            result.grounding_notes = notes
            return result

        assessors = {
            "symbol_lookup": lambda: self._assess_symbol_lookup(question, evidence),
            "call_site": lambda: self._assess_call_site(question, evidence, search_coverage),
            "symbol_trace": lambda: self._assess_symbol_trace(question, evidence),
            "calculation": lambda: self._assess_calculation(question, evidence, search_coverage),
            "code_explanation": lambda: self._assess_code_explanation(question, evidence),
            "database_query": lambda: self._assess_database_query(question, evidence),
            "architecture": lambda: self._assess_architecture(question, evidence),
            "cache": lambda: self._assess_cache(question, evidence),
            "flow": lambda: self._assess_flow(question, question_type, evidence),
        }
        a = assessors[profile]()
        return self._from_assessment(profile, a, notes, current_round, max_rounds)

    def _from_assessment(
        self,
        profile: str,
        a: _Assessment,
        notes: list[str],
        current_round: int,
        max_rounds: int,
    ) -> EvidenceEvaluation:
        missing = a.missing_stages
        present = [s for s in a.stages if s.present]
        supported = [s.details for s in present]
        missing_claims = [f"Missing {s.stage}: {s.details}" for s in a.stages if not s.present] + a.unverified
        # Profile-specific facts first: the answer prompt keeps only the first few.
        notes = a.notes + notes

        if not missing and not a.unverified:
            return EvidenceEvaluation(
                sufficiency="sufficient",
                supported_claims=supported,
                missing_claims=[],
                retrieval_needed=False,
                recommended_queries=_empty_queries(),
                reasoning=f"All {profile} requirements and connections are verified in retrieved code.",
                flow_stages=a.stages,
                missing_stages=[],
                unverified_connections=[],
                evaluation_profile=profile,
                grounding_notes=notes,
            )

        sufficiency = "partially_sufficient" if present else "insufficient"
        queries = a.finalized_queries()
        has_queries = any(queries[k] for k in queries)
        can_retrieve = current_round < max_rounds and has_queries
        if current_round >= max_rounds:
            reasoning = (
                f"Maximum retrieval rounds reached. {profile} evidence remains incomplete: "
                f"missing ({', '.join(missing) or 'none'}); unverified ({'; '.join(a.unverified) or 'none'})."
            )
        else:
            reasoning = (
                f"{profile} evaluation: missing ({', '.join(missing) or 'none'}); "
                f"unverified ({'; '.join(a.unverified) or 'none'})."
                + (f" Targeted retrieval round {current_round + 1} planned." if can_retrieve else " No targeted query available.")
            )
        return EvidenceEvaluation(
            sufficiency=sufficiency,
            supported_claims=supported,
            partially_supported_claims=[s.details for s in present if not s.connected_to_next],
            missing_claims=missing_claims,
            retrieval_needed=can_retrieve,
            recommended_queries=queries if can_retrieve else _empty_queries(),
            reasoning=reasoning,
            flow_stages=a.stages,
            missing_stages=missing,
            unverified_connections=a.unverified,
            evaluation_profile=profile,
            grounding_notes=notes,
        )

    # =========================================================================
    # LLM-ASSISTED EVALUATION (general profile only)
    # =========================================================================

    def _llm_evaluation(
        self,
        question: str,
        evidence: list[dict],
        question_type: str | None,
        current_round: int,
        max_rounds: int,
    ) -> EvidenceEvaluation:
        if current_round >= max_rounds:
            return self._max_rounds_reached(question, evidence)

        evidence_summary_lines = []
        for i, item in enumerate(evidence[:10], 1):
            file = item.get("file", "unknown")
            symbol = item.get("symbol") or "N/A"
            lines = f"{item.get('start_line', '?')}-{item.get('end_line', '?')}"
            role = sig.file_role(file)
            snippet = (item.get("snippet") or item.get("content") or "")[:250].replace("\n", " ")
            evidence_summary_lines.append(f"[{i}] {file} ({role}; lines {lines}, symbol: {symbol}): {snippet}")

        user_prompt = f"""QUESTION:
{question}

QUESTION TYPE:
{question_type or 'general'}

CURRENT RETRIEVAL ROUND:
{current_round} (Max additional rounds: {max_rounds})

RETRIEVED EVIDENCE SUMMARY ({len(evidence)} items):
{chr(10).join(evidence_summary_lines)}

Evaluate whether this evidence genuinely supports an answer. DO NOT claim sufficiency based on snippet count alone."""

        try:
            raw = self.llm_service.generate_structured(
                system_prompt=self.SYSTEM_PROMPT,
                user_prompt=user_prompt,
                response_schema=self.RESPONSE_SCHEMA,
                max_tokens=700,
            )
            raw.pop("__llm_metadata__", None)
            raw.pop("_metadata", None)
            if not _filter_direct(evidence) and raw.get("sufficiency") == "sufficient":
                raw["sufficiency"] = "insufficient"
            return EvidenceEvaluation.model_validate(raw)
        except Exception as exc:
            logger.warning("LLM evidence evaluation failed, using deterministic fallback: %s", exc)
            return self._deterministic_evaluation(question, evidence, question_type, current_round, max_rounds)

    def _deterministic_evaluation(
        self,
        question: str,
        evidence: list[dict],
        question_type: str | None,
        current_round: int,
        max_rounds: int,
        *_unused: Any,
    ) -> EvidenceEvaluation:
        """Deterministic fallback for the general profile: relevant direct code is required."""
        a = self._assess_code_explanation(question, evidence)
        return self._from_assessment("general", a, self.grounding_notes(evidence), current_round, max_rounds)

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
                "symbol_queries": EvidenceEvaluationService._question_identifiers(question),
                "text_queries": sorted(sig.domain_terms(question))[:3],
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
        """Forced termination for the general profile. Never 'sufficient' without direct evidence."""
        a = self._assess_code_explanation(question, evidence)
        return self._from_assessment("general", a, self.grounding_notes(evidence), 1, 0)
