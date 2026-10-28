"""
Evidence Evaluator
==================
Determines whether retrieved evidence is sufficient to answer a question.

Design rules (directly address the three known bugs):

Bug 1 — incorrect "sufficient":
  - Evaluates each REQUIRED claim and flow stage individually.
  - Never marks sufficient based on evidence count alone.
  - Files that are unrelated (admin registrations, tests, docs) do NOT count
    as evidence for the primary claim.
  - If any required stage or claim lacks direct implementation evidence,
    returns ``needs_more_evidence`` with targeted search terms.

Bug 3 — admin/test files as nonexistent-function evidence:
  - Classifies every evidence file before scoring it.
  - Admin, test, migration, and documentation files are tagged ``indirect``.
  - Indirect files CANNOT satisfy a ``symbol_lookup`` sufficiency check.
  - A symbol not found in direct source files → ``insufficient``, not partial.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# File classification helpers
# ---------------------------------------------------------------------------

_INDIRECT_PATTERNS = (
    # Test files
    re.compile(r"(^|[/\\])tests?[/\\]"),
    re.compile(r"_tests?\.py$"),
    re.compile(r"\.spec\.(ts|js)$"),
    # Admin registrations
    re.compile(r"admin\.py$"),
    # Django migrations
    re.compile(r"[/\\]migrations[/\\]"),
    # Documentation
    re.compile(r"\.(md|rst|txt)$", re.IGNORECASE),
    # Fixtures / data
    re.compile(r"[/\\]fixtures[/\\]"),
    # Example / config docs
    re.compile(r"\.env\.example$"),
)

_MANIFEST_PATTERNS = (
    re.compile(r"(requirements|package|pyproject|cargo|gemfile|go\.mod)", re.IGNORECASE),
    re.compile(r"\.(toml|yaml|yml)$", re.IGNORECASE),
)


def _classify_file(path: str) -> Literal["direct", "indirect", "manifest"]:
    """Return the evidence class of a repository file path."""
    if not path:
        return "indirect"
    norm = path.replace("\\", "/")
    for pat in _MANIFEST_PATTERNS:
        if pat.search(norm):
            return "manifest"
    for pat in _INDIRECT_PATTERNS:
        if pat.search(norm):
            return "indirect"
    return "direct"


# ---------------------------------------------------------------------------
# Flow stage definitions per question type
# ---------------------------------------------------------------------------

_REQUIRED_STAGES: dict[str, list[str]] = {
    "end_to_end_flow": [
        "frontend_trigger",        # UI event handler / component method
        "http_request",            # Actual HTTP call (fetch / HttpClient / axios)
        "backend_route",           # URL routing (urls.py / routes.ts)
        "backend_view_or_handler", # View / controller implementation
        "data_persistence",        # DB write / ORM save
    ],
    "feature_flow": [
        "entry_point",             # Where the feature starts
        "core_logic",              # Main implementation
        "data_operation",          # Read/write to storage or external service
    ],
    "cross_cutting_concern": [
        "configuration",           # How it is configured
        "implementation",          # Where it is applied
    ],
    "dependency_analysis": [
        "manifest_file",           # Actual package manifest content
    ],
}

# Keywords that help detect each stage in snippet content
_STAGE_KEYWORDS: dict[str, list[str]] = {
    "frontend_trigger": [
        "onclick", "addEventListener", "submit(", "placeOrder", "handleSubmit",
        "checkOut", "checkout(", "(click)", ".subscribe(", "ngOnInit",
        "checkout.component", "cart.component",
    ],
    "http_request": [
        "http.post", "http.get", "http.put", "http.patch", "http.delete",
        "fetch(", "axios.", "this.http", "HttpClient", "httpClient",
        "$.post", "$.get", "XMLHttpRequest",
    ],
    "backend_route": [
        "urlpatterns", "path(", "re_path(", "router.add", "app.route(",
        "app.get(", "app.post(", "@api_view", "urls.py",
    ],
    "backend_view_or_handler": [
        "def get(", "def post(", "def put(", "def patch(", "APIView",
        "viewsets.", "GenericAPIView", "ListView", "DetailView",
        "CreateAPIView", "request.data", "request.user",
    ],
    "data_persistence": [
        ".save(", ".create(", ".update(", ".delete(", "objects.create",
        "objects.update", "objects.filter", "session.add", "db.commit",
        "INSERT INTO", "UPDATE ", "DELETE FROM",
    ],
    "entry_point": [
        "def ", "class ", "function ", "const ", "export ",
    ],
    "core_logic": [
        "if ", "for ", "while ", "try:", "return ",
    ],
    "data_operation": [
        "objects.", ".save(", ".create(", "session.", "db.", "query(",
    ],
    "configuration": [
        "settings", "config", "INSTALLED_APPS", "DATABASES", "CACHES",
        "MIDDLEWARE", "REST_FRAMEWORK", "AUTH_",
    ],
    "implementation": [
        "def ", "class ", "@", "middleware", "decorator",
    ],
    "manifest_file": [
        "django", "djangorestframework", "@angular", "dependencies",
        "version", "requirements",
    ],
}


@dataclass
class EvidenceEvaluation:
    """Structured result from evidence evaluation."""

    status: Literal["sufficient", "needs_more_evidence", "insufficient"]
    """
    sufficient           — all required claims/stages have direct evidence.
    needs_more_evidence  — some stages missing but targeted search may help.
    insufficient         — symbol not found or max rounds reached with gaps.
    """

    supported_claims: list[str] = field(default_factory=list)
    """Claims/stages that ARE supported by direct evidence."""

    missing_information: list[str] = field(default_factory=list)
    """Claims/stages that are NOT supported."""

    next_search_targets: list[str] = field(default_factory=list)
    """Search terms to use in the next retrieval round."""

    limitations: list[str] = field(default_factory=list)
    """Human-readable gap descriptions for the final answer."""

    unverified_connections: list[str] = field(default_factory=list)
    """Connections that exist in evidence but cannot be traced end-to-end."""

    missing_stages: list[str] = field(default_factory=list)
    """Specific flow stages that have no direct evidence."""

    evidence_count: int = 0
    """Total evidence items considered."""

    direct_evidence_count: int = 0
    """Evidence items from direct source files (not admin/test/doc)."""


class EvidenceEvaluator:
    """
    Evaluates whether retrieved evidence is sufficient to answer a query.

    Key design decisions
    --------------------
    1. Sufficiency is claim-based, not count-based.
    2. Admin, test, migration, and documentation files are ``indirect`` and
       cannot satisfy direct-implementation requirements.
    3. For symbol_lookup, the symbol must appear in a *direct* source file.
    4. For end_to_end / feature_flow, each required stage must have at least
       one direct snippet containing stage-specific keywords.
    5. Returns ``needs_more_evidence`` with specific search targets when
       stages are missing but retrieval can still help.
    """

    MAX_RETRIEVAL_ROUNDS: int = 2  # configurable via constructor

    def __init__(self, max_retrieval_rounds: int = 2) -> None:
        self.max_retrieval_rounds = max_retrieval_rounds

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def evaluate(
        self,
        question: str,
        question_type: str,
        evidence: list[dict],
        retrieval_round: int = 0,
    ) -> EvidenceEvaluation:
        """
        Evaluate evidence against the question and question type.

        Parameters
        ----------
        question:        The user's original question.
        question_type:   One of the QuestionType values (as string).
        evidence:        Transformed evidence list (each item has 'file',
                         'snippet', 'symbol', etc.).
        retrieval_round: How many additional retrieval rounds have already run
                         (0 = initial retrieval only).

        Returns
        -------
        EvidenceEvaluation with status, gaps, and next search targets.
        """
        if not evidence:
            return EvidenceEvaluation(
                status="insufficient",
                missing_information=["No evidence was retrieved."],
                limitations=["No evidence was retrieved for this question."],
                evidence_count=0,
                direct_evidence_count=0,
            )

        # Classify each evidence item
        classified = self._classify_evidence(evidence)
        direct_items = [e for e in classified if e["_class"] == "direct"]
        manifest_items = [e for e in classified if e["_class"] == "manifest"]

        logger.info(
            "Evidence evaluation: total=%d direct=%d indirect=%d manifest=%d",
            len(classified),
            len(direct_items),
            len([e for e in classified if e["_class"] == "indirect"]),
            len(manifest_items),
        )

        # Route to the appropriate evaluator
        if question_type == "symbol_lookup":
            return self._evaluate_symbol_lookup(
                question, classified, direct_items, retrieval_round
            )

        if question_type in ("end_to_end_flow", "feature_flow"):
            return self._evaluate_flow(
                question, question_type, classified, direct_items, retrieval_round
            )

        if question_type == "dependency_analysis":
            return self._evaluate_dependency(
                question, classified, direct_items, manifest_items, retrieval_round
            )

        # Default: require at least one direct evidence item
        return self._evaluate_generic(
            question, classified, direct_items, retrieval_round
        )

    # ------------------------------------------------------------------
    # Per-type evaluators
    # ------------------------------------------------------------------

    def _evaluate_symbol_lookup(
        self,
        question: str,
        all_items: list[dict],
        direct_items: list[dict],
        retrieval_round: int,
    ) -> EvidenceEvaluation:
        """
        For symbol lookup: the symbol must appear in a DIRECT source file.

        Bug-3 fix: admin.py, migrations, tests are classified 'indirect' and
        do NOT count as evidence that the symbol exists.
        """
        # Extract the symbol name from the question
        symbol_name = self._extract_symbol_name(question)

        # Check direct items for the symbol
        symbol_found_direct = self._symbol_in_direct_evidence(
            symbol_name, direct_items
        )

        if symbol_found_direct:
            matching = [
                f"{e['file']} (lines {e.get('start_line','?')}-{e.get('end_line','?')})"
                for e in direct_items
                if self._item_contains_symbol(symbol_name, e)
            ]
            return EvidenceEvaluation(
                status="sufficient",
                supported_claims=[f"Symbol '{symbol_name}' found in direct source"],
                evidence_count=len(all_items),
                direct_evidence_count=len(direct_items),
            )

        # Symbol not in direct evidence — check if it might be in indirect
        indirect_mention = any(
            self._item_contains_symbol(symbol_name, e) for e in all_items
        )

        if indirect_mention:
            # Found only in admin/test/doc files — report as unverified
            indirect_files = [
                e["file"] for e in all_items
                if e["_class"] == "indirect"
                and self._item_contains_symbol(symbol_name, e)
            ]
            if retrieval_round < self.max_retrieval_rounds:
                return EvidenceEvaluation(
                    status="needs_more_evidence",
                    missing_information=[
                        f"'{symbol_name}' only found in indirect files: {indirect_files}. "
                        "Direct implementation source not retrieved."
                    ],
                    next_search_targets=[symbol_name, f"def {symbol_name}", f"class {symbol_name}"],
                    limitations=[
                        f"'{symbol_name}' appears only in admin/test/doc files, not in direct source."
                    ],
                    evidence_count=len(all_items),
                    direct_evidence_count=len(direct_items),
                )
            # Max rounds reached
            return EvidenceEvaluation(
                status="insufficient",
                missing_information=[
                    f"'{symbol_name}' not found in direct source files after {retrieval_round} retrieval round(s)."
                ],
                limitations=[
                    f"'{symbol_name}' was referenced only in indirect files (admin, test, or migration). "
                    "Direct implementation was not found in the indexed source."
                ],
                evidence_count=len(all_items),
                direct_evidence_count=len(direct_items),
            )

        # Symbol not found anywhere
        if retrieval_round < self.max_retrieval_rounds:
            return EvidenceEvaluation(
                status="needs_more_evidence",
                missing_information=[f"Symbol '{symbol_name}' not found in any evidence."],
                next_search_targets=[
                    symbol_name,
                    f"def {symbol_name}",
                    f"class {symbol_name}",
                    # Snake/camel variants
                    re.sub(r"([A-Z])", r"_\1", symbol_name).lstrip("_").lower(),
                ],
                limitations=[f"Symbol '{symbol_name}' not found in retrieved evidence."],
                evidence_count=len(all_items),
                direct_evidence_count=len(direct_items),
            )

        return EvidenceEvaluation(
            status="insufficient",
            missing_information=[f"Symbol '{symbol_name}' not found in the repository index."],
            limitations=[
                f"Symbol '{symbol_name}' was not found in any indexed source file. "
                "It may not exist, or it may be in a file type not indexed by this system."
            ],
            evidence_count=len(all_items),
            direct_evidence_count=len(direct_items),
        )

    def _evaluate_flow(
        self,
        question: str,
        question_type: str,
        all_items: list[dict],
        direct_items: list[dict],
        retrieval_round: int,
    ) -> EvidenceEvaluation:
        """
        For end_to_end_flow and feature_flow: each required stage needs
        at least one direct snippet containing stage keywords.

        Bug-1 fix: sufficiency requires ALL stages to have direct evidence.
        """
        required_stages = _REQUIRED_STAGES.get(question_type, [])
        if not required_stages:
            return self._evaluate_generic(
                question, all_items, direct_items, retrieval_round
            )

        supported: list[str] = []
        missing: list[str] = []
        missing_stages: list[str] = []
        next_targets: list[str] = []
        unverified: list[str] = []

        for stage in required_stages:
            keywords = _STAGE_KEYWORDS.get(stage, [])
            stage_found = self._stage_has_direct_evidence(stage, keywords, direct_items)

            if stage_found:
                supported.append(stage)
            else:
                missing.append(stage)
                missing_stages.append(stage)
                # Generate search targets for this stage
                next_targets.extend(keywords[:3])

        limitations = []
        if missing:
            limitations.append(
                f"Missing direct evidence for stages: {', '.join(missing)}."
            )

        # Detect unverified connections (stage present in indirect only)
        for stage in missing_stages:
            keywords = _STAGE_KEYWORDS.get(stage, [])
            in_indirect = self._stage_has_indirect_evidence(stage, keywords, all_items)
            if in_indirect:
                unverified.append(
                    f"Stage '{stage}' referenced in docs/tests but no direct implementation found"
                )

        if not missing:
            return EvidenceEvaluation(
                status="sufficient",
                supported_claims=supported,
                evidence_count=len(all_items),
                direct_evidence_count=len(direct_items),
            )

        # Bug-1 fix: only return needs_more if we haven't hit max rounds
        if retrieval_round < self.max_retrieval_rounds:
            return EvidenceEvaluation(
                status="needs_more_evidence",
                supported_claims=supported,
                missing_information=missing,
                missing_stages=missing_stages,
                next_search_targets=list(set(next_targets))[:8],
                limitations=limitations,
                unverified_connections=unverified,
                evidence_count=len(all_items),
                direct_evidence_count=len(direct_items),
            )

        # Max rounds reached — return partial/insufficient, NOT sufficient
        status = "insufficient" if not supported else "insufficient"
        # If most stages are present, treat as partial (for answer generation)
        if len(supported) >= len(required_stages) // 2:
            status = "insufficient"  # still insufficient — let answer generator mark partial

        return EvidenceEvaluation(
            status=status,
            supported_claims=supported,
            missing_information=missing,
            missing_stages=missing_stages,
            limitations=limitations + [
                f"Maximum retrieval rounds reached. "
                f"Stages still missing: {', '.join(missing_stages)}."
            ],
            unverified_connections=unverified,
            evidence_count=len(all_items),
            direct_evidence_count=len(direct_items),
        )

    def _evaluate_dependency(
        self,
        question: str,
        all_items: list[dict],
        direct_items: list[dict],
        manifest_items: list[dict],
        retrieval_round: int,
    ) -> EvidenceEvaluation:
        """Dependency analysis requires actual manifest file content."""
        if manifest_items:
            return EvidenceEvaluation(
                status="sufficient",
                supported_claims=["Package manifest retrieved"],
                evidence_count=len(all_items),
                direct_evidence_count=len(direct_items),
            )

        if retrieval_round < self.max_retrieval_rounds:
            return EvidenceEvaluation(
                status="needs_more_evidence",
                missing_information=["No package manifest file found in evidence."],
                next_search_targets=["requirements.txt", "package.json", "pyproject.toml"],
                limitations=["Package manifest not yet retrieved."],
                evidence_count=len(all_items),
                direct_evidence_count=len(direct_items),
            )

        return EvidenceEvaluation(
            status="insufficient",
            missing_information=["Package manifest not found after all retrieval rounds."],
            limitations=["Could not retrieve package manifest files."],
            evidence_count=len(all_items),
            direct_evidence_count=len(direct_items),
        )

    def _evaluate_generic(
        self,
        question: str,
        all_items: list[dict],
        direct_items: list[dict],
        retrieval_round: int,
    ) -> EvidenceEvaluation:
        """Fallback: require at least one direct evidence item."""
        if direct_items:
            return EvidenceEvaluation(
                status="sufficient",
                supported_claims=["Direct source evidence present"],
                evidence_count=len(all_items),
                direct_evidence_count=len(direct_items),
            )

        if retrieval_round < self.max_retrieval_rounds:
            return EvidenceEvaluation(
                status="needs_more_evidence",
                missing_information=["No direct source evidence found."],
                next_search_targets=self._extract_keywords(question),
                limitations=["Only indirect evidence (docs, tests, admin) was retrieved."],
                evidence_count=len(all_items),
                direct_evidence_count=0,
            )

        return EvidenceEvaluation(
            status="insufficient",
            missing_information=["No direct source evidence found."],
            limitations=["No direct source evidence found after retrieval."],
            evidence_count=len(all_items),
            direct_evidence_count=0,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_evidence(evidence: list[dict]) -> list[dict]:
        """Add a ``_class`` field to each evidence item."""
        result = []
        for item in evidence:
            classified = dict(item)
            classified["_class"] = _classify_file(item.get("file", ""))
            result.append(classified)
        return result

    @staticmethod
    def _symbol_in_direct_evidence(symbol: str, direct_items: list[dict]) -> bool:
        if not symbol:
            return bool(direct_items)
        sym_lower = symbol.lower()
        for item in direct_items:
            if EvidenceEvaluator._item_contains_symbol(symbol, item):
                return True
        return False

    @staticmethod
    def _item_contains_symbol(symbol: str, item: dict) -> bool:
        if not symbol:
            return False
        sym_lower = symbol.lower()
        snippet = (item.get("snippet") or "").lower()
        sym_name = (item.get("symbol") or "").lower()
        return sym_lower in snippet or sym_lower == sym_name

    @staticmethod
    def _stage_has_direct_evidence(
        stage: str, keywords: list[str], direct_items: list[dict]
    ) -> bool:
        if not direct_items:
            return False
        for item in direct_items:
            snippet = (item.get("snippet") or "").lower()
            if any(kw.lower() in snippet for kw in keywords):
                return True
        return False

    @staticmethod
    def _stage_has_indirect_evidence(
        stage: str, keywords: list[str], all_items: list[dict]
    ) -> bool:
        for item in all_items:
            if item.get("_class") == "indirect":
                snippet = (item.get("snippet") or "").lower()
                if any(kw.lower() in snippet for kw in keywords):
                    return True
        return False

    @staticmethod
    def _extract_symbol_name(question: str) -> str:
        """Extract CamelCase or snake_case identifier from question text."""
        # PascalCase / camelCase
        camel = re.findall(r"\b[A-Z][a-zA-Z0-9]+(?:[A-Z][a-zA-Z0-9]*)*\b", question)
        if camel:
            return camel[0]
        # snake_case
        snake = re.findall(r"\b[a-z][a-z0-9_]+(?:_[a-z0-9]+)+\b", question)
        if snake:
            return snake[0]
        # Last word
        words = question.rstrip("?").split()
        return words[-1] if words else ""

    @staticmethod
    def _extract_keywords(question: str) -> list[str]:
        stopwords = {"a", "an", "the", "is", "are", "was", "were", "how", "what",
                     "where", "when", "why", "does", "do", "did", "this", "that",
                     "which", "who", "in", "on", "at", "for", "with", "to", "of"}
        words = re.findall(r"\b\w{3,}\b", question.lower())
        return [w for w in words if w not in stopwords][:6]
