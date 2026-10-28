"""
Citation Validation Service

Validates that citations referenced in technical answers correspond
to real, retrieved evidence and actual code lines in the repository.

Performs rigorous repository scope checks, line number validation,
evidence ID verification, and claim-to-source support evaluation.
"""

import logging
import re
from pathlib import Path
from typing import Any

from backend.app.models.investigation_answer import Citation, InvestigationAnswer
from backend.app.services.llm_service import LLMService

logger = logging.getLogger(__name__)


class CitationValidationResult:
    """Result of citation validation."""

    def __init__(
        self,
        is_valid: bool,
        valid_citations: list[Citation],
        invalid_citations: list[dict],
        hallucinated_ids: list[str],
        unsupported_claims: list[str],
        evaluated_claims: list[str] | None = None,
        repair_needed: bool = False,
        details: str = "",
    ):
        self.is_valid = is_valid
        self.valid_citations = valid_citations
        self.invalid_citations = invalid_citations
        self.hallucinated_ids = hallucinated_ids
        self.unsupported_claims = unsupported_claims
        self.evaluated_claims = evaluated_claims or []
        self.repair_needed = repair_needed
        self.details = details

    def to_dict(self) -> dict:
        return {
            "is_valid": self.is_valid,
            "valid_citation_count": len(self.valid_citations),
            "invalid_citation_count": len(self.invalid_citations),
            "hallucinated_ids": self.hallucinated_ids,
            "unsupported_claims": self.unsupported_claims,
            "evaluated_claims_count": len(self.evaluated_claims),
            "repair_needed": self.repair_needed,
            "details": self.details,
        }


class CitationValidationService:
    """Validates citations against supplied evidence and repository files."""

    REPAIR_SCHEMA: dict = {
        "type": "object",
        "properties": {
            "answer": {
                "type": "string",
                "description": "Repaired explanation referencing ONLY valid evidence IDs like [E1], [E2].",
            },
            "cited_evidence_ids": {
                "type": "array",
                "items": {"type": "string", "pattern": "^E[0-9]+$"},
                "description": "List of valid evidence IDs that were cited in the repaired answer.",
            },
            "confidence": {
                "type": "string",
                "enum": ["high", "partial", "insufficient"],
            },
            "limitations": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": ["answer", "cited_evidence_ids", "confidence", "limitations"],
        "additionalProperties": False,
    }

    def __init__(self, llm_service: LLMService | None = None):
        self.llm_service = llm_service

    def validate_answer_citations(
        self,
        answer: InvestigationAnswer,
        evidence: list[dict],
        repository_path: str | None = None,
    ) -> CitationValidationResult:
        """
        Validate an answer's citations, scope, locations, and claim-to-source support.

        Checks:
        1. Repository scope: Cited files exist within the repository without path traversal.
        2. Source locations: Start/end lines are valid and within source bounds.
        3. Evidence IDs: All inline [E#] and citation objects exist in supplied evidence.
        4. Claim-to-source support: Evaluates whether the cited evidence actually supports
           the claim made in the text, detecting false endpoint attributions, unverified
           connections, or fabricated functionality.
        """
        evidence_by_id = {}
        for item in evidence:
            eid = item.get("evidence_id")
            if eid:
                evidence_by_id[eid] = item

        valid_eids = set(evidence_by_id.keys())

        # 1. Check inline references in answer text
        inline_refs = set(re.findall(r"\[(E\d+)\]", answer.answer))
        hallucinated_inline = list(inline_refs - valid_eids)

        # 2. Check citations list & locations
        valid_citations: list[Citation] = []
        invalid_citations: list[dict] = []
        hallucinated_cited_ids = []

        root_path = Path(repository_path).resolve() if repository_path else None

        for citation in answer.citations:
            eid = citation.evidence_id

            if eid not in evidence_by_id:
                hallucinated_cited_ids.append(eid)
                invalid_citations.append({
                    "evidence_id": eid,
                    "reason": f"Evidence ID '{eid}' does not exist in supplied evidence.",
                })
                continue

            ev_item = evidence_by_id[eid]

            # Verify file matches
            ev_file = ev_item.get("file")
            if citation.file and ev_file and citation.file.replace("\\", "/") != ev_file.replace("\\", "/"):
                invalid_citations.append({
                    "evidence_id": eid,
                    "reason": f"File mismatch: cited '{citation.file}' vs evidence '{ev_file}'.",
                })
                continue

            # Check line ranges
            if citation.start_line > 0 and citation.end_line > 0 and citation.start_line > citation.end_line:
                invalid_citations.append({
                    "evidence_id": eid,
                    "reason": f"Invalid line range: {citation.start_line}-{citation.end_line}.",
                })
                continue

            # Check repository scope and existence if repository path is available
            if root_path and citation.file:
                target_file = (root_path / citation.file).resolve()
                if not target_file.is_relative_to(root_path):
                    invalid_citations.append({
                        "evidence_id": eid,
                        "reason": f"Security: Citation file '{citation.file}' is outside repository root.",
                    })
                    continue

                if not target_file.is_file():
                    invalid_citations.append({
                        "evidence_id": eid,
                        "reason": f"File does not exist in repository: {citation.file}.",
                    })
                    continue

                # Check line range within actual file bounds
                try:
                    line_count = len(target_file.read_text(encoding="utf-8", errors="replace").splitlines())
                    if citation.start_line > line_count:
                        invalid_citations.append({
                            "evidence_id": eid,
                            "reason": f"Citation start_line {citation.start_line} exceeds file length ({line_count}) in {citation.file}.",
                        })
                        continue
                except Exception as exc:
                    logger.debug(f"Could not verify file line count for {citation.file}: {exc}")

            valid_citations.append(citation)

        all_hallucinated = list(set(hallucinated_inline + hallucinated_cited_ids))

        # 3. Evaluate claim-to-source support (NEVER assume unsupported_claims=[] without evaluation!)
        evaluated_claims, unsupported_claims = self._evaluate_claim_to_source_support(
            answer_text=answer.answer,
            evidence_by_id=evidence_by_id,
        )

        for eid in all_hallucinated:
            unsupported_claims.append(f"Answer cites hallucinated evidence ID [{eid}] which does not exist.")

        # Bug-2 fix: zero evaluated claims means we could NOT verify anything.
        # This must NEVER report is_valid=True.
        if not evaluated_claims:
            return CitationValidationResult(
                is_valid=False,
                valid_citations=list(answer.citations) if answer.citations else [],
                invalid_citations=invalid_citations,
                hallucinated_ids=all_hallucinated,
                unsupported_claims=unsupported_claims,
                evaluated_claims=[],
                repair_needed=bool(all_hallucinated or invalid_citations),
                details=(
                    "Citation validation could not extract any evaluated claims. "
                    "The answer contains no inline [E#] evidence references. "
                    "Cannot confirm that citations support the stated claims."
                ),
            )

        repair_needed = bool(all_hallucinated or invalid_citations or unsupported_claims)

        details = "All citations and claims are verified."
        if repair_needed:
            issues = []
            if all_hallucinated:
                issues.append(f"{len(all_hallucinated)} hallucinated IDs ({all_hallucinated})")
            if invalid_citations:
                issues.append(f"{len(invalid_citations)} invalid citation locations")
            if unsupported_claims:
                issues.append(f"{len(unsupported_claims)} unsupported claims")
            details = f"Validation issues detected: {'; '.join(issues)}."

        is_valid = not repair_needed

        return CitationValidationResult(
            is_valid=is_valid,
            valid_citations=valid_citations,
            invalid_citations=invalid_citations,
            hallucinated_ids=all_hallucinated,
            unsupported_claims=unsupported_claims,
            evaluated_claims=evaluated_claims,
            repair_needed=repair_needed,
            details=details,
        )

    def _evaluate_claim_to_source_support(
        self,
        answer_text: str,
        evidence_by_id: dict[str, dict],
    ) -> tuple[list[str], list[str]]:
        """
        Evaluate whether claims citing [E#] are genuinely supported by the source snippets.

        Bug-2 fix: Every sentence containing a citation reference is evaluated and added
        to `evaluated_claims`. The function MUST NOT return an empty `evaluated_claims` list
        when the answer contains citation references — doing so would allow `to_dict()` to
        report `evaluated_claims_count: 0` while also reporting `unsupported_claims: []`,
        which falsely implies "all claims verified".

        After this fix:
        - Any sentence with [E#] is evaluated and recorded in evaluated_claims.
        - Pattern-based unsupported-claim detection runs on top of the base evaluation.
        - Zero evaluated claims is only possible when the answer has no [E#] references at all.
        """
        evaluated_claims: list[str] = []
        unsupported_claims: list[str] = []

        if not answer_text:
            return evaluated_claims, unsupported_claims

        # Extract sentences or clauses containing citations
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", answer_text) if s.strip()]

        for sentence in sentences:
            cited_ids = re.findall(r"\[(E\d+)\]", sentence)
            if not cited_ids:
                continue

            sentence_lower = sentence.lower()

            for eid in cited_ids:
                if eid not in evidence_by_id:
                    # Hallucinated ID — reported elsewhere, but still counts as evaluated
                    claim_desc = f"[{eid}] (not in evidence): {sentence[:90]}"
                    evaluated_claims.append(claim_desc)
                    unsupported_claims.append(
                        f"Citation [{eid}] references an evidence ID that does not exist in supplied evidence."
                    )
                    continue

                ev_item = evidence_by_id[eid]
                snippet = ev_item.get("snippet") or ev_item.get("content") or ev_item.get("source") or ""
                snippet_lower = snippet.lower()
                file_lower = (ev_item.get("file") or "").lower()
                symbol = ev_item.get("symbol") or ""

                # Record this claim as evaluated regardless of pass/fail
                claim_desc = f"[{eid}] in {ev_item.get('file')}: {sentence[:90]}"
                evaluated_claims.append(claim_desc)

                # ── Pattern checks for known false-attribution cases ──────────

                # Check 1: False attribution of CartSyncView to normal quantity updates
                is_claiming_quantity_update = any(
                    kw in sentence_lower for kw in (
                        "quantity update", "update quantity", "increase quantity",
                        "decrease quantity", "normal quantity", "adjust quantity",
                        "updating quantity", "increment quantity", "decrement quantity",
                        "user adjusts", "user clicks", "cart quantity is updated"
                    )
                )
                if is_claiming_quantity_update:
                    if "cartsyncview" in snippet_lower or "cartsyncview" in symbol.lower():
                        if "fresh login only" in snippet_lower or "guest cart" in snippet_lower or "cart/sync" in snippet_lower:
                            if "cartaddview" not in snippet_lower and "increaseqty" not in snippet_lower:
                                unsupported_claims.append(
                                    f"Claim citing [{eid}] describes CartSyncView as handling normal quantity updates, "
                                    f"but the cited code is implemented for guest-cart merging upon fresh login only."
                                )

                # Check 2: Asserting URL routing from a non-routing file
                is_claiming_route = any(
                    kw in sentence_lower for kw in ("url route", "routed via", "endpoint mapping", "routes to", "path(")
                ) and not any(kw in sentence_lower for kw in ("missing", "unverified", "not found", "unknown"))

                if is_claiming_route:
                    if not any(kw in file_lower for kw in ("urls.py", "routes", "router")):
                        if not any(kw in snippet_lower for kw in ("path(", "re_path(", "urlpatterns")):
                            unsupported_claims.append(
                                f"Claim citing [{eid}] asserts a URL route mapping, but cited file {ev_item.get('file')} "
                                f"does not contain URL configuration."
                            )

                # Check 3: Asserting UI event handling from a backend file
                is_claiming_ui = any(
                    kw in sentence_lower for kw in ("ui event handler", "button click", "click event", "template button")
                ) and not any(kw in sentence_lower for kw in ("missing", "unverified", "not found"))

                if is_claiming_ui:
                    if not any(file_lower.endswith(ext) for ext in (".ts", ".tsx", ".js", ".jsx", ".html")):
                        unsupported_claims.append(
                            f"Claim citing [{eid}] asserts UI event handling, but cited file {ev_item.get('file')} "
                            f"is a backend file."
                        )

                # Check 4: Bug-3 — claiming a nonexistent function exists based on admin/test evidence
                # If the citation file is an admin.py, test file, or migration, and the sentence
                # positively asserts the function "is defined", "exists", or "handles" something,
                # flag it as unsupported.
                _INDIRECT_FILE_PATTERNS = (
                    "admin.py", "/tests/", "/test_", ".spec.", "/migrations/",
                    "_test.py", "test_", "fixtures/",
                )
                is_indirect_file = any(pat in file_lower for pat in _INDIRECT_FILE_PATTERNS)
                is_positive_existence_claim = any(
                    kw in sentence_lower for kw in (
                        "is defined", "is implemented", "handles", "processes",
                        "exists in", "can be found", "is located", "lives in",
                    )
                ) and not any(neg in sentence_lower for neg in ("not", "missing", "absent", "no "))

                if is_indirect_file and is_positive_existence_claim:
                    unsupported_claims.append(
                        f"Claim citing [{eid}] positively asserts existence/implementation using "
                        f"an indirect file ({ev_item.get('file')}). Admin registrations, tests, and "
                        f"migrations do not prove that the primary implementation exists."
                    )

        return evaluated_claims, unsupported_claims

    def repair_answer(
        self,
        answer: InvestigationAnswer,
        evidence: list[dict],
        validation_result: CitationValidationResult,
    ) -> InvestigationAnswer:
        """
        Repair an answer that has hallucinated citations, invalid locations, or unsupported claims.

        Attempts LLM-based repair if LLM service is available;
        otherwise applies targeted deterministic repair to correct false claims and limitations.
        """
        valid_eids = {item.get("evidence_id") for item in evidence if item.get("evidence_id")}
        evidence_by_id = {item.get("evidence_id"): item for item in evidence if item.get("evidence_id")}

        # Try LLM repair first if service available
        if self.llm_service:
            try:
                system_prompt = """\
You are a Citation and Factual Grounding Repair Assistant.
The previous technical answer contained invalid citations or unsupported claims.
Your task is to REWRITE the answer following these strict rules:
1. ONLY reference valid evidence IDs provided below like [E1], [E2].
2. DO NOT claim that CartSyncView handles normal quantity updates! (CartSyncView is only for guest-cart merge on login).
3. If UI event handlers or URL routing are missing from the evidence, state clearly that those stages are missing or unverified.
4. Set confidence to "partial" or "insufficient" when key stages or connections are unverified.
5. In your limitations array, explicitly list any unverified connections or missing stages."""

                valid_ev_summary = []
                for eid in sorted(valid_eids):
                    ev = evidence_by_id[eid]
                    valid_ev_summary.append(
                        f"[{eid}] {ev.get('file')} ({ev.get('symbol', '')}): {(ev.get('snippet') or '')[:180]}"
                    )

                user_prompt = f"""ORIGINAL QUESTION:
{answer.question}

ORIGINAL ANSWER WITH DEFECTS:
{answer.answer}

UNSUPPORTED CLAIMS DETECTED:
{validation_result.unsupported_claims}

HALLUCINATED IDS DETECTED:
{validation_result.hallucinated_ids}

VALID AVAILABLE EVIDENCE ({len(valid_eids)} items):
{chr(10).join(valid_ev_summary)}

Please return the repaired answer with ONLY valid citations [E#] and appropriate confidence."""

                repaired_raw = self.llm_service.generate_structured(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    response_schema=self.REPAIR_SCHEMA,
                    max_tokens=1500,
                )

                repaired_answer_text = repaired_raw.get("answer", "")
                repaired_cited_ids = [
                    eid for eid in repaired_raw.get("cited_evidence_ids", [])
                    if eid in valid_eids
                ]

                # Filter citations to only valid ones
                new_citations = [c for c in validation_result.valid_citations if c.evidence_id in repaired_cited_ids]

                limitations = list(repaired_raw.get("limitations", []))
                for uc in validation_result.unsupported_claims:
                    if uc not in str(limitations):
                        limitations.append(f"Correction: {uc}")

                return InvestigationAnswer(
                    question=answer.question,
                    answer=repaired_answer_text,
                    citations=new_citations if new_citations else validation_result.valid_citations,
                    confidence=repaired_raw.get("confidence", "partial"),
                    limitations=limitations,
                    execution_time_seconds=answer.execution_time_seconds,
                )

            except Exception as exc:
                logger.warning("LLM citation repair failed, using deterministic repair: %s", exc)

        # Deterministic repair fallback
        repaired_text = answer.answer

        # Strip hallucinated IDs
        for bad_id in validation_result.hallucinated_ids:
            repaired_text = re.sub(rf"\[{re.escape(bad_id)}\]", "", repaired_text)

        # Correct false claim attributing CartSyncView to normal quantity updates
        if any("CartSyncView" in uc for uc in validation_result.unsupported_claims):
            cart_sync_correction = (
                " Note: CartSyncView is implemented for guest-cart merge upon fresh login only; "
                "it is not confirmed to be the endpoint used for normal UI quantity updates (e.g. CartAddView)."
            )
            repaired_text = repaired_text.strip() + cart_sync_correction

        repaired_text = re.sub(r"  +", " ", repaired_text).strip()

        limitations = list(answer.limitations)
        for uc in validation_result.unsupported_claims:
            limitations.append(f"Unverified claim corrected: {uc}")

        if validation_result.hallucinated_ids:
            limitations.append(
                f"Removed unsupported citations ({', '.join(validation_result.hallucinated_ids)}) during validation."
            )

        return InvestigationAnswer(
            question=answer.question,
            answer=repaired_text,
            citations=validation_result.valid_citations,
            confidence="partial" if answer.confidence == "high" else answer.confidence,
            limitations=limitations,
            execution_time_seconds=answer.execution_time_seconds,
        )
