import json
import logging
import re

from backend.app.models.query_analysis import QueryAnalysis, QuestionType
from backend.app.services.llm_service import LLMService

logger = logging.getLogger(__name__)


class QueryAnalyzerService:
    """Analyzes natural-language questions and produces structured search instructions.

    Design notes
    ------------
    * The schema sent to the LLM is **compact** – listing only the fields we need
      with short descriptions.  Pydantic's full ``model_json_schema()`` embeds $defs,
      enum arrays, pattern strings, etc. that unnecessarily inflate the prompt token
      count and increase the probability of JSON truncation in prompt-only mode.
    * On a ``finish_reason == "length"`` (truncated) response the service attempts
      one retry with a tighter ``max_tokens`` cap before raising.
    * Invalid / partial JSON is handled explicitly: we try to salvage a response via
      a lightweight extraction heuristic before giving up.
    """

    # Compact schema for the LLM – keeps prompt short and avoids truncation
    RESPONSE_SCHEMA: dict = {
        "type": "object",
        "properties": {
            "intent": {
                "type": "string",
                "description": "The primary investigation intent of the user (one sentence).",
            },
            "question_type": {
                "type": "string",
                "enum": [qt.value for qt in QuestionType],
                "description": "One of the defined question type values.",
            },
            "concepts": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Important concepts from the question (2–6 items).",
            },
            "symbol_queries": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Identifiers to search in the symbol index (0–5 items).",
            },
            "text_queries": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Terms for full-text search (0–5 items).",
            },
            "import_queries": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Module/package names for import search (0–3 items).",
            },
        },
        "required": [
            "intent",
            "question_type",
            "concepts",
            "symbol_queries",
            "text_queries",
            "import_queries",
        ],
        "additionalProperties": False,
    }

    SYSTEM_PROMPT = """\
You are the Query Analyzer for a code-investigation system.
Analyze the user's question and produce a JSON search plan.

QUESTION TYPES (use exactly one value):
  symbol_lookup         – "Where is function X defined?"
  call_site_lookup      – "Where is X called?"
  file_explanation      – "What does file X do?"
  feature_flow          – "How does feature X work?"
  architectural_overview – "Explain this project / architecture"
  end_to_end_flow       – "Trace request from frontend to DB"
  cross_cutting_concern – "How is caching/auth/logging implemented?"
  dependency_analysis   – "What external services/packages are used?"

QUERY GENERATION:
  symbol_queries  → exact identifiers (function, class, method names)
  text_queries    → keywords likely in source code or config
  import_queries  → package or module names
  Keep each list short (≤5 items). Prefer quality over quantity.
  Generate hypotheses only – do NOT assert what exists in the repo.

Return ONLY the JSON object with these fields:
  intent, question_type, concepts, symbol_queries, text_queries, import_queries"""

    # Token budget for query analysis.  Kept deliberately lower than answer
    # generation so rate-limit headroom is preserved for the LLM answer step.
    _PRIMARY_MAX_TOKENS = 600
    _RETRY_MAX_TOKENS = 400
    _MAX_RETRIES = 1  # one retry on truncation (avoids rate-limit spirals)

    def __init__(self, llm_service: LLMService):
        self.llm_service = llm_service

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(self, query: str) -> QueryAnalysis:
        """Analyze *query* and return structured search instructions.

        Raises
        ------
        ValueError
            If the query is empty.
        RuntimeError
            If the LLM returns an unrecoverable response after retries.
        """
        query = query.strip()
        if not query:
            raise ValueError("Query cannot be empty.")

        last_error: Exception | None = None

        for attempt in range(self._MAX_RETRIES + 1):
            max_tokens = (
                self._RETRY_MAX_TOKENS if attempt > 0 else self._PRIMARY_MAX_TOKENS
            )

            try:
                raw = self.llm_service.generate_structured(
                    system_prompt=self.SYSTEM_PROMPT,
                    user_prompt=query,
                    response_schema=self.RESPONSE_SCHEMA,
                    max_tokens=max_tokens,
                )
            except RuntimeError as exc:
                last_error = exc
                logger.warning(
                    "Query analysis attempt %d/%d failed (LLM error): %s",
                    attempt + 1,
                    self._MAX_RETRIES + 1,
                    exc,
                )
                if attempt < self._MAX_RETRIES:
                    continue
                raise RuntimeError(
                    f"Query analysis failed after {self._MAX_RETRIES + 1} attempts: {exc}"
                ) from exc

            metadata = raw.pop("__llm_metadata__", {})
            raw.pop("_metadata", None)

            was_truncated = metadata.get("was_truncated", False)
            if was_truncated:
                logger.warning(
                    "Query analysis response was truncated (finish_reason=length) "
                    "on attempt %d – trying to salvage or retry.",
                    attempt + 1,
                )
                # Attempt to salvage a partial JSON object
                salvaged = self._salvage_partial(raw)
                if salvaged is not None:
                    logger.info("Salvaged truncated query analysis response.")
                    raw = salvaged
                elif attempt < self._MAX_RETRIES:
                    last_error = RuntimeError(
                        "LLM response was truncated and could not be salvaged."
                    )
                    continue
                else:
                    raise RuntimeError(
                        "Query analysis response was truncated and could not be "
                        "salvaged after retries. Consider using a simpler query."
                    )

            try:
                return QueryAnalysis.model_validate(raw)
            except Exception as exc:  # pydantic ValidationError or similar
                last_error = exc
                logger.warning(
                    "Query analysis validation failed on attempt %d: %s – raw: %s",
                    attempt + 1,
                    exc,
                    str(raw)[:200],
                )
                if attempt < self._MAX_RETRIES:
                    continue
                raise RuntimeError(
                    f"Query analysis returned invalid data: {exc}"
                ) from exc

        # Should not reach here, but satisfy type checker
        raise RuntimeError(
            f"Query analysis failed after {self._MAX_RETRIES + 1} attempts."
        ) from last_error

    def analyze_with_fallback(self, query: str) -> QueryAnalysis:
        """Like ``analyze`` but NEVER raises on LLM failure.

        If the LLM is unavailable, returns an unambiguously empty
        analysis derived purely from the original query text.  The
        caller can detect the fallback via ``analysis.intent`` containing
        the literal string ``"[fallback]"``.

        The fallback extracts identifier-like tokens from the query and
        uses them as symbol / text queries so that at least the
        deterministic search stages (symbol, text, import) can run.
        """
        try:
            return self.analyze(query)
        except Exception as exc:
            logger.warning(
                "Query analysis failed (all retries exhausted), "
                "using deterministic fallback.  Error: %s",
                exc,
            )
            return self._deterministic_fallback(query)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _deterministic_fallback(query: str) -> QueryAnalysis:
        """Build a minimal QueryAnalysis from the raw query text.

        Rules:
        - Identify CamelCase / PascalCase tokens → symbol_queries
        - Remaining meaningful words → text_queries
        - Empty import_queries (no LLM needed)
        - Guess question_type from keywords
        """
        # Identify likely identifiers (CamelCase, snake_case, etc.)
        identifier_pattern = re.compile(
            r"\b([A-Z][a-zA-Z0-9]+(?:[A-Z][a-zA-Z0-9]*)*|[a-z_][a-z_0-9]*(?:_[a-z_0-9]+)+)\b"
        )
        identifiers = list(dict.fromkeys(identifier_pattern.findall(query)))[:5]

        # Stopword-stripped text terms
        stopwords = {
            "a", "an", "and", "are", "as", "at", "be", "by", "do",
            "for", "from", "how", "i", "if", "in", "is", "it", "of",
            "on", "or", "so", "that", "the", "their", "this", "to",
            "was", "what", "when", "where", "which", "who", "why",
            "with", "you", "your", "does", "find", "show", "tell",
            "me", "please", "explain", "describe", "where", "list",
        }
        words = re.findall(r"\b[a-zA-Z]\w+\b", query)
        text_terms = [
            w for w in words
            if w.lower() not in stopwords and len(w) > 2
        ][:5]

        # Guess question type
        q_lower = query.lower()
        if any(kw in q_lower for kw in ("architecture", "structure", "overview", "what is this", "explain the project", "describe the project")):
            qt = QuestionType.ARCHITECTURAL_OVERVIEW
        elif any(kw in q_lower for kw in ("end to end", "frontend", "backend", "request flow", "from ui to")):
            qt = QuestionType.END_TO_END_FLOW
        elif any(kw in q_lower for kw in ("depends", "dependenc", "package", "version", "library", "manifest")):
            qt = QuestionType.DEPENDENCY_ANALYSIS
        elif any(kw in q_lower for kw in ("how does", "how is", "explain", "flow", "process", "trace")):
            qt = QuestionType.FEATURE_FLOW
        elif any(kw in q_lower for kw in ("where is", "find", "locate", "defined")):
            qt = QuestionType.SYMBOL_LOOKUP
        elif any(kw in q_lower for kw in ("cach", "auth", "log", "email", "celery", "task")):
            qt = QuestionType.CROSS_CUTTING_CONCERN
        else:
            qt = QuestionType.FEATURE_FLOW

        return QueryAnalysis(
            intent=f"[fallback] {query[:120]}",
            question_type=qt,
            concepts=text_terms[:4],
            symbol_queries=identifiers,
            text_queries=text_terms,
            import_queries=[],
        )

    @staticmethod
    def _salvage_partial(raw: dict) -> dict | None:
        """Try to repair a response that arrived with truncated JSON.

        The LLM occasionally cuts off inside an array value.  We ensure every
        required list field exists (defaulting to ``[]``) and every required
        string field has a fallback, then attempt a validate-safe dict.

        Returns ``None`` if the response is too empty to be useful (e.g. it
        contains only the metadata key and nothing application-relevant).
        """
        required_lists = ("concepts", "symbol_queries", "text_queries", "import_queries")
        required_strings = ("intent", "question_type")

        valid_question_types = {qt.value for qt in QuestionType}

        # Strip internal metadata – it is not part of the application payload
        repaired = {k: v for k, v in raw.items() if not k.startswith("__")}

        # Nothing substantive survived the truncation
        if not repaired:
            return None

        # Fill missing list fields
        for field in required_lists:
            if field not in repaired or not isinstance(repaired.get(field), list):
                repaired[field] = []

        # Fill missing string fields with safe defaults
        if not repaired.get("intent"):
            repaired["intent"] = "Investigate the repository"

        if repaired.get("question_type") not in valid_question_types:
            # Try to guess from any text present
            repaired["question_type"] = "architectural_overview"

        # Verify all required keys are present
        for field in required_strings + required_lists:
            if field not in repaired:
                return None  # Cannot salvage

        return repaired