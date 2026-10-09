import re
from collections import defaultdict


class EvidenceCollectorService:
    """
    Collect, merge, and rank repository evidence.

    Ranking priorities:
    1. Exact identifier matches.
    2. Deterministic symbol and source evidence.
    3. Independent retrieval methods agreeing on the same location.
    4. Semantic similarity.
    5. Evidence with file paths, line numbers, and source content.
    """

    DEFAULT_MAX_EVIDENCE = 12

    TYPE_BASE_SCORES = {
        "source_context": 1.00,
        "symbol": 0.98,
        "text_match": 0.82,
        "import": 0.68,
        "relationship": 0.66,
        "semantic": 0.35,
    }

    STOP_WORDS = {
        "a", "an", "and", "are", "at", "be", "by", "does", "for",
        "from", "how", "i", "in", "is", "it", "of", "on", "or",
        "the", "this", "to", "was", "what", "when", "where",
        "which", "who", "why", "with", "you", "your", "defined",
        "definition", "find", "show", "tell", "me", "please",
    }

    def collect(
        self,
        retrieval_results: dict,
        max_evidence: int = DEFAULT_MAX_EVIDENCE,
    ) -> dict:
        results = retrieval_results.get("results", {})
        query = retrieval_results.get("query", "")
        query_analysis = retrieval_results.get("query_analysis", {})

        query_terms, exact_identifiers = self._query_features(
            query,
            query_analysis,
        )

        candidates = []

        self._add_standard_candidates(
            candidates,
            results.get("symbols", []),
            "symbol",
            query_terms,
            exact_identifiers,
        )

        self._add_standard_candidates(
            candidates,
            results.get("text_matches", []),
            "text_match",
            query_terms,
            exact_identifiers,
        )

        self._add_standard_candidates(
            candidates,
            results.get("imports", []),
            "import",
            query_terms,
            exact_identifiers,
        )

        self._add_semantic_candidates(
            candidates,
            results.get("semantic", []),
            query_terms,
            exact_identifiers,
        )

        self._add_standard_candidates(
            candidates,
            results.get("source_context", []),
            "source_context",
            query_terms,
            exact_identifiers,
        )

        self._add_standard_candidates(
            candidates,
            results.get("relationships", []),
            "relationship",
            query_terms,
            exact_identifiers,
        )

        merged = self._merge_candidates(candidates)

        ranked = sorted(
            merged,
            key=self._ranking_key,
            reverse=True,
        )

        selected = ranked[:max(0, max_evidence)]

        return {
            "query": query,
            "evidence": selected,
            "summary": {
                "candidate_count": len(candidates),
                "unique_count": len(merged),
                "selected_count": len(selected),
            },
        }

    # ---------------------------------------------------------
    # CANDIDATE CREATION
    # ---------------------------------------------------------

    def _add_standard_candidates(
        self,
        candidates: list,
        results: list,
        evidence_type: str,
        query_terms: set[str],
        exact_identifiers: set[str],
    ) -> None:
        base_score = self.TYPE_BASE_SCORES[evidence_type]

        for result in results:
            candidates.append(
                self._build_candidate(
                    result=result,
                    evidence_type=evidence_type,
                    base_score=base_score,
                    query_terms=query_terms,
                    exact_identifiers=exact_identifiers,
                )
            )

    def _add_semantic_candidates(
        self,
        candidates: list,
        results: list,
        query_terms: set[str],
        exact_identifiers: set[str],
    ) -> None:
        for result in results:
            semantic_score = self._safe_float(
                result.get("score")
            )

            semantic_score = max(
                0.0,
                min(1.0, semantic_score),
            )

            # Semantic-only evidence stays below strong deterministic
            # evidence unless it also matches the requested identifier.
            base_score = 0.35 + (semantic_score * 0.35)

            candidates.append(
                self._build_candidate(
                    result=result,
                    evidence_type="semantic",
                    base_score=base_score,
                    query_terms=query_terms,
                    exact_identifiers=exact_identifiers,
                )
            )

    def _build_candidate(
        self,
        result: dict,
        evidence_type: str,
        base_score: float,
        query_terms: set[str],
        exact_identifiers: set[str],
    ) -> dict:
        symbol = result.get("symbol") or result.get("name")
        symbol_type = (
            result.get("symbol_type")
            or result.get("type")
        )

        qualified_name = result.get("qualified_name")

        content = (
            result.get("content")
            or result.get("source")
            or result.get("text")
        )

        start_line = result.get("start_line")

        if start_line is None:
            start_line = result.get("line")

        end_line = result.get("end_line")

        if end_line is None:
            end_line = result.get("line")

        candidate = {
            "type": evidence_type,
            "file": result.get("file"),
            "language": result.get("language"),
            "symbol": symbol,
            "symbol_type": symbol_type,
            "qualified_name": qualified_name,
            "start_line": start_line,
            "end_line": end_line,
            "content": content,
            "score": result.get("score"),
            "search_query": result.get("search_query"),
            "details": result,
            "evidence_score": base_score,
            "query_match_score": 0.0,
            "exact_identifier_match": False,
        }

        query_bonus, exact_match = self._query_match_bonus(
            candidate,
            query_terms,
            exact_identifiers,
        )

        candidate["query_match_score"] = query_bonus
        candidate["exact_identifier_match"] = exact_match

        candidate["evidence_score"] = round(
            min(1.35, base_score + query_bonus),
            6,
        )

        return candidate

    # ---------------------------------------------------------
    # QUERY RELEVANCE
    # ---------------------------------------------------------

    @classmethod
    def _query_features(
        cls,
        query: str,
        query_analysis: dict,
    ) -> tuple[set[str], set[str]]:
        text_parts = [query or ""]

        for field in (
            "concepts",
            "symbol_queries",
            "text_queries",
        ):
            values = query_analysis.get(field, [])

            if isinstance(values, list):
                text_parts.extend(
                    str(value)
                    for value in values
                    if value
                )

        combined_text = " ".join(text_parts)

        query_terms = {
            token.lower()
            for token in re.findall(
                r"[A-Za-z_$][A-Za-z0-9_$]*",
                combined_text,
            )
            if (
                token.lower() not in cls.STOP_WORDS
                and len(token) > 1
            )
        }

        exact_identifiers = set()

        # Identifiers explicitly proposed by the query analyzer.
        for value in query_analysis.get("symbol_queries", []) or []:
            value = str(value).strip()

            if value and re.fullmatch(
                r"[A-Za-z_$][A-Za-z0-9_$.]*",
                value,
            ):
                exact_identifiers.add(value.casefold())

        # Detect identifier-like names directly in the user's question.
        for value in re.findall(
            r"\b[A-Za-z_$][A-Za-z0-9_$]*"
            r"(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*\b",
            query or "",
        ):
            if (
                value.lower() not in cls.STOP_WORDS
                and (
                    "_" in value
                    or any(ch.isupper() for ch in value[1:])
                )
            ):
                exact_identifiers.add(value.casefold())

        return query_terms, exact_identifiers

    @staticmethod
    def _normalized_tokens(value: str) -> set[str]:
        return {
            token.casefold()
            for token in re.findall(
                r"[A-Za-z_$][A-Za-z0-9_$]*",
                value or "",
            )
        }

    @classmethod
    def _query_match_bonus(
        cls,
        candidate: dict,
        query_terms: set[str],
        exact_identifiers: set[str],
    ) -> tuple[float, bool]:
        symbol = str(candidate.get("symbol") or "")
        qualified_name = str(
            candidate.get("qualified_name") or ""
        )

        content = str(candidate.get("content") or "")
        search_query = str(
            candidate.get("search_query") or ""
        )

        candidate_names = {
            name.casefold()
            for name in (symbol, qualified_name)
            if name
        }

        # Rule: an exact requested identifier should rank above
        # unrelated results with high semantic similarity.
        exact_match = bool(
            exact_identifiers
            and any(
                identifier == name
                or identifier.split(".")[-1]
                == name.split(".")[-1]
                for identifier in exact_identifiers
                for name in candidate_names
            )
        )

        if exact_match:
            return 0.28, True

        searchable_text = " ".join(
            part
            for part in (
                symbol,
                qualified_name,
                content,
                search_query,
            )
            if part
        )

        candidate_tokens = cls._normalized_tokens(
            searchable_text
        )

        if not query_terms or not candidate_tokens:
            return 0.0, False

        overlap = (
            len(query_terms & candidate_tokens)
            / len(query_terms)
        )

        # Modest lexical relevance bonus.
        return round(
            min(0.12, overlap * 0.12),
            6,
        ), False

    # ---------------------------------------------------------
    # MERGE DUPLICATE EVIDENCE
    # ---------------------------------------------------------

    @classmethod
    def _merge_candidates(
        cls,
        candidates: list[dict],
    ) -> list[dict]:
        grouped = defaultdict(list)

        for candidate in candidates:
            key = cls._candidate_key(candidate)
            grouped[key].append(candidate)

        merged = []

        for items in grouped.values():
            best = max(
                items,
                key=lambda item: item["evidence_score"],
            )

            evidence_types = sorted({
                item["type"]
                for item in items
            })

            exact_identifier_match = any(
                item.get("exact_identifier_match", False)
                for item in items
            )

            merged_item = {
                "type": best["type"],
                "types": evidence_types,
                "file": cls._first_value(items, "file"),
                "language": cls._first_value(items, "language"),
                "symbol": cls._first_value(items, "symbol"),
                "symbol_type": cls._first_value(
                    items,
                    "symbol_type",
                ),
                "qualified_name": cls._first_value(
                    items,
                    "qualified_name",
                ),
                "start_line": cls._first_value(
                    items,
                    "start_line",
                ),
                "end_line": cls._first_value(
                    items,
                    "end_line",
                ),
                "content": cls._first_value(items, "content"),
                "score": cls._best_semantic_score(items),
                "search_query": cls._first_value(
                    items,
                    "search_query",
                ),
                "details": [
                    item["details"]
                    for item in items
                ],
                "exact_identifier_match": exact_identifier_match,
                "query_match_score": max(
                    item.get("query_match_score", 0.0)
                    for item in items
                ),
                "evidence_score": cls._combined_score(
                    items,
                    evidence_types,
                ),
            }

            merged.append(merged_item)

        return merged

    @staticmethod
    def _candidate_key(candidate: dict) -> tuple:
        file_path = candidate.get("file")
        start_line = candidate.get("start_line")
        end_line = candidate.get("end_line")

        if file_path and start_line is not None:
            return (
                "location",
                file_path,
                start_line,
                end_line,
            )

        symbol = (
            candidate.get("qualified_name")
            or candidate.get("symbol")
        )

        if file_path and symbol:
            return ("symbol", file_path, symbol)

        content = candidate.get("content")

        if file_path and content:
            return ("content", file_path, content)

        return (
            "fallback",
            candidate.get("type"),
            file_path,
            symbol,
            content,
        )

    @classmethod
    def _combined_score(
        cls,
        items: list[dict],
        evidence_types: list[str],
    ) -> float:
        best = max(
            item["evidence_score"]
            for item in items
        )

        # Reward agreement between different retrieval methods.
        # Repeated hits from one method do not add extra bonuses.
        corroboration_bonus = min(
            0.16,
            0.06 * (len(evidence_types) - 1),
        )

        # Exact symbol plus its retrieved source is particularly strong.
        deterministic_bonus = (
            0.08
            if {
                "symbol",
                "source_context",
            }.issubset(evidence_types)
            else 0.0
        )

        return round(
            min(
                1.35,
                best
                + corroboration_bonus
                + deterministic_bonus,
            ),
            6,
        )

    # ---------------------------------------------------------
    # FINAL RANKING
    # ---------------------------------------------------------

    @staticmethod
    def _ranking_key(item: dict) -> tuple:
        types = set(item.get("types", []))

        has_deterministic_evidence = int(
            bool(
                types
                & {
                    "source_context",
                    "symbol",
                    "text_match",
                }
            )
        )

        has_location = int(
            bool(item.get("file"))
            and item.get("start_line") is not None
        )

        has_content = int(bool(item.get("content")))

        exact_identifier_match = int(
            bool(item.get("exact_identifier_match"))
        )

        semantic_score = (
            EvidenceCollectorService._safe_float(
                item.get("score")
            )
        )

        return (
            item.get("evidence_score", 0.0),
            exact_identifier_match,
            has_deterministic_evidence,
            has_location,
            has_content,
            semantic_score,
        )

    # ---------------------------------------------------------
    # HELPERS
    # ---------------------------------------------------------

    @staticmethod
    def _first_value(
        items: list[dict],
        key: str,
    ):
        for item in sorted(
            items,
            key=lambda value: value["evidence_score"],
            reverse=True,
        ):
            value = item.get(key)

            if value is not None and value != "":
                return value

        return None

    @staticmethod
    def _best_semantic_score(
        items: list[dict],
    ):
        scores = [
            EvidenceCollectorService._safe_float(
                item.get("score")
            )
            for item in items
            if item.get("score") is not None
        ]

        return max(scores) if scores else None

    @staticmethod
    def _safe_float(value) -> float:
        try:
            return float(value or 0.0)
        except (TypeError, ValueError):
            return 0.0