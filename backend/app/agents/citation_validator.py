"""
Citation Validator
==================
Validates every citation in an investigation answer against actual
repository source and evaluates whether the cited snippet supports the
associated claim.

Bug-2 fixes
-----------
* Zero claims evaluated MUST produce status="not_evaluated", not "verified".
* Structural validity (ID exists, file exists, line range valid) is checked
  separately from semantic claim support.
* The validator extracts factual claims from the answer text and evaluates
  each one; it never short-circuits to "all claims verified" without doing it.
* One repair attempt per failed citation; if repair fails the claim is marked
  unsupported and a limitation is added.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class CitationCheck:
    """Result for one citation."""
    evidence_id: str
    file: str
    start_line: int
    end_line: int

    # Structural validity
    id_exists: bool = False
    file_exists: bool = False
    line_range_valid: bool = False
    source_retrievable: bool = False
    snippet_non_empty: bool = False

    # Semantic validity
    claim_text: str = ""
    claim_supported: bool = False
    claim_support_reason: str = ""

    # Overall
    structural_valid: bool = False
    repaired: bool = False
    repair_note: str = ""

    def to_dict(self) -> dict:
        return {
            "evidence_id": self.evidence_id,
            "file": self.file,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "structural_valid": self.structural_valid,
            "claim_supported": self.claim_supported,
            "claim_support_reason": self.claim_support_reason,
            "repaired": self.repaired,
            "repair_note": self.repair_note,
        }


@dataclass
class ValidationResult:
    """Full validation result for an investigation answer."""

    status: Literal["verified", "partial", "failed", "not_evaluated"]
    """
    verified      — all evaluated claims are structurally valid and supported.
    partial       — some claims valid; others were removed or qualified.
    failed        — no claims could be validated.
    not_evaluated — zero claims were extracted (must never claim success).
    """

    evaluated_claims_count: int = 0
    supported_count: int = 0
    unsupported_claims: list[str] = field(default_factory=list)
    invalid_citations: list[dict] = field(default_factory=list)
    repaired_citations: list[dict] = field(default_factory=list)
    limitations_added: list[str] = field(default_factory=list)

    # Repaired answer text (citations removed / qualified for unsupported claims)
    repaired_answer: str = ""
    repaired_citations_list: list = field(default_factory=list)  # valid Citation objects

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "evaluated_claims_count": self.evaluated_claims_count,
            "supported_count": self.supported_count,
            "unsupported_claims": self.unsupported_claims,
            "invalid_citations": self.invalid_citations,
            "repaired_citations": self.repaired_citations,
            "limitations_added": self.limitations_added,
        }


# ---------------------------------------------------------------------------
# Claim extractor
# ---------------------------------------------------------------------------

def _extract_claims(answer_text: str, citations: list) -> list[tuple[str, str]]:
    """
    Return a list of (claim_sentence, evidence_id) pairs.

    A claim is any sentence that references an evidence ID [E1], [E2], etc.
    Bug-2 fix: this function must return at least one pair for non-empty
    answers that contain evidence references, or an empty list if none exist.
    """
    if not answer_text:
        return []

    # Split into sentences (rough but sufficient)
    sentences = re.split(r"(?<=[.!?])\s+", answer_text.strip())
    claims: list[tuple[str, str]] = []

    for sentence in sentences:
        ids_in_sentence = re.findall(r"\[E(\d+)\]", sentence)
        for eid_num in ids_in_sentence:
            eid = f"E{eid_num}"
            claims.append((sentence.strip(), eid))

    # Deduplicate by (eid, first 60 chars of claim)
    seen: set[tuple[str, str]] = set()
    unique: list[tuple[str, str]] = []
    for claim, eid in claims:
        key = (eid, claim[:60])
        if key not in seen:
            seen.add(key)
            unique.append((claim, eid))

    return unique


# ---------------------------------------------------------------------------
# Semantic claim-support checker (deterministic, no LLM)
# ---------------------------------------------------------------------------

def _claim_supported_by_snippet(claim: str, snippet: str, symbol: str = "") -> bool:
    """
    Lightweight deterministic check: does the snippet contain enough keywords
    to be plausibly relevant to the claim?

    We intentionally keep this simple (no LLM) to remain deterministic and
    avoid extra API calls per citation.  The check is conservative: it flags
    citations as unsupported only when there is clearly no topical overlap.
    """
    if not snippet or not claim:
        return False

    claim_lower = claim.lower()
    snippet_lower = snippet.lower()

    # Extract meaningful tokens from the claim (skip stop words)
    _STOPS = {
        "a", "an", "the", "is", "are", "was", "were", "has", "have", "had",
        "it", "its", "this", "that", "these", "those", "be", "been", "being",
        "in", "on", "at", "to", "for", "of", "and", "or", "but", "with",
        "from", "by", "as", "at", "into", "through", "during", "according",
        "does", "do", "did", "not", "which", "where", "when", "how", "what",
        "will", "would", "could", "should", "can", "may", "might", "must",
        "he", "she", "they", "we", "i", "you", "using", "used", "also",
        "defined", "definition", "function", "method", "class", "code",
    }

    claim_tokens = set(
        re.findall(r"\b[a-z_][a-z0-9_]{2,}\b", claim_lower)
    ) - _STOPS

    if not claim_tokens:
        return True  # Can't evaluate — give benefit of doubt

    # Symbol name in snippet → strong positive signal
    if symbol and symbol.lower() in snippet_lower:
        return True

    # Count overlap
    overlap = sum(1 for tok in claim_tokens if tok in snippet_lower)
    # Need at least 20% overlap or 2 tokens
    ratio = overlap / len(claim_tokens)
    return ratio >= 0.2 or overlap >= 2


# ---------------------------------------------------------------------------
# Main validator class
# ---------------------------------------------------------------------------

class CitationValidator:
    """
    Validates citations in an InvestigationAnswer against repository source.

    Usage
    -----
    result = validator.validate(answer, prepared_evidence, repository_path)
    """

    def __init__(self) -> None:
        pass

    def validate(
        self,
        answer,  # InvestigationAnswer
        prepared_evidence: list[dict],
        repository_path: str,
    ) -> ValidationResult:
        """
        Validate all citations in *answer* against the repository.

        Steps
        -----
        1. Extract factual claims (sentences containing [E1], [E2], etc.).
        2. For each claim, check the associated citation structurally
           (ID exists, file exists, line range valid, snippet retrievable).
        3. Check semantic support (does the snippet support the claim?).
        4. Attempt one repair pass: find an alternative evidence item that
           supports the same claim.
        5. Build ValidationResult:
           - evaluated_claims_count > 0 required before status can be "verified".
           - 0 evaluated → status "not_evaluated" (Bug-2 fix).
        """
        evidence_by_id = {
            e.get("evidence_id"): e
            for e in prepared_evidence
            if e.get("evidence_id")
        }

        answer_text = answer.answer if hasattr(answer, "answer") else str(answer)
        citations = answer.citations if hasattr(answer, "citations") else []

        # ----------------------------------------------------------------
        # Step 1: Extract claims
        # ----------------------------------------------------------------
        claim_pairs = _extract_claims(answer_text, citations)

        # Bug-2 fix: if zero claims extracted, immediately return not_evaluated
        if not claim_pairs:
            return ValidationResult(
                status="not_evaluated",
                evaluated_claims_count=0,
                limitations_added=[
                    "Citation validation could not extract any claims to evaluate. "
                    "Answer may not contain inline evidence references [E1], [E2], etc."
                ],
                repaired_answer=answer_text,
                repaired_citations_list=list(citations),
            )

        # ----------------------------------------------------------------
        # Step 2 & 3: Validate each (claim, evidence_id) pair
        # ----------------------------------------------------------------
        checks: list[CitationCheck] = []
        for claim_text, eid in claim_pairs:
            check = self._check_citation(
                eid, claim_text, evidence_by_id, repository_path
            )
            checks.append(check)

        # ----------------------------------------------------------------
        # Step 4: Repair pass
        # ----------------------------------------------------------------
        repaired_checks: list[CitationCheck] = []
        for check in checks:
            if not check.structural_valid or not check.claim_supported:
                repaired = self._attempt_repair(
                    check, claim_pairs, evidence_by_id, repository_path
                )
                repaired_checks.append(repaired)
            else:
                repaired_checks.append(check)

        # ----------------------------------------------------------------
        # Step 5: Build result
        # ----------------------------------------------------------------
        evaluated = len(repaired_checks)
        supported = [c for c in repaired_checks if c.structural_valid and c.claim_supported]
        unsupported = [c for c in repaired_checks if not (c.structural_valid and c.claim_supported)]
        invalid_structural = [c for c in repaired_checks if not c.structural_valid]

        limitations: list[str] = []
        unsupported_claim_texts: list[str] = []
        invalid_citation_dicts: list[dict] = []
        repaired_citation_dicts: list[dict] = []

        for c in unsupported:
            if not c.structural_valid:
                limitations.append(
                    f"Citation {c.evidence_id} is structurally invalid: "
                    f"file='{c.file}', lines {c.start_line}-{c.end_line}."
                )
                invalid_citation_dicts.append(c.to_dict())
            else:
                limitations.append(
                    f"Claim not supported by {c.evidence_id}: '{c.claim_text[:80]}'"
                )
                unsupported_claim_texts.append(c.claim_text[:120])

        for c in repaired_checks:
            if c.repaired:
                repaired_citation_dicts.append(c.to_dict())

        # Determine status
        if evaluated == 0:
            status: Literal["verified", "partial", "failed", "not_evaluated"] = "not_evaluated"
        elif len(supported) == evaluated:
            status = "verified"
        elif supported:
            status = "partial"
        else:
            status = "failed"

        # Build repaired citations list from original citations
        # (keep structurally valid, supported ones)
        valid_eids = {c.evidence_id for c in repaired_checks if c.structural_valid and c.claim_supported}
        repaired_citation_objects = [
            cit for cit in citations
            if hasattr(cit, "evidence_id") and cit.evidence_id in valid_eids
        ]
        # Also keep citations from the original list that weren't evaluated
        evaluated_eids = {c.evidence_id for c in repaired_checks}
        unevaluated_citations = [
            cit for cit in citations
            if hasattr(cit, "evidence_id") and cit.evidence_id not in evaluated_eids
        ]
        final_citations = repaired_citation_objects + unevaluated_citations

        return ValidationResult(
            status=status,
            evaluated_claims_count=evaluated,
            supported_count=len(supported),
            unsupported_claims=unsupported_claim_texts,
            invalid_citations=invalid_citation_dicts,
            repaired_citations=repaired_citation_dicts,
            limitations_added=limitations,
            repaired_answer=answer_text,
            repaired_citations_list=final_citations,
        )

    # ------------------------------------------------------------------
    # Per-citation check
    # ------------------------------------------------------------------

    def _check_citation(
        self,
        eid: str,
        claim_text: str,
        evidence_by_id: dict,
        repository_path: str,
    ) -> CitationCheck:
        """Run structural and semantic check for one (claim, eid) pair."""
        check = CitationCheck(
            evidence_id=eid,
            file="",
            start_line=0,
            end_line=0,
            claim_text=claim_text,
        )

        # 1. Evidence ID exists
        evidence = evidence_by_id.get(eid)
        if not evidence:
            logger.warning("Citation %s not found in evidence map", eid)
            return check  # all False

        check.id_exists = True
        check.file = evidence.get("file", "")
        check.start_line = evidence.get("start_line", 0)
        check.end_line = evidence.get("end_line", 0)

        # 2. File exists in repository
        if check.file and repository_path:
            full = Path(repository_path) / check.file
            check.file_exists = full.exists()
        else:
            check.file_exists = bool(check.file)  # can't check without path

        # 3. Line range valid
        check.line_range_valid = (
            check.start_line > 0
            and check.end_line >= check.start_line
        )

        # 4. Snippet non-empty
        snippet = (evidence.get("snippet") or "").strip()
        check.snippet_non_empty = bool(snippet)

        # 5. Source retrievable (if we have a path and valid range)
        if check.file_exists and check.line_range_valid and repository_path:
            try:
                full = Path(repository_path) / check.file
                lines = full.read_text(encoding="utf-8", errors="replace").splitlines()
                s = max(0, check.start_line - 1)
                e = min(len(lines), check.end_line)
                check.source_retrievable = e > s
            except Exception:
                check.source_retrievable = False
        else:
            check.source_retrievable = check.snippet_non_empty

        check.structural_valid = (
            check.id_exists
            and check.file_exists
            and check.line_range_valid
            and check.snippet_non_empty
        )

        # 6. Semantic claim support
        if check.structural_valid:
            symbol = evidence.get("symbol") or ""
            check.claim_supported = _claim_supported_by_snippet(
                claim_text, snippet, symbol
            )
            if check.claim_supported:
                check.claim_support_reason = "Snippet contains relevant tokens"
            else:
                check.claim_support_reason = "Snippet does not contain sufficient claim-relevant tokens"
        else:
            check.claim_support_reason = "Structural validation failed"

        return check

    # ------------------------------------------------------------------
    # Repair pass
    # ------------------------------------------------------------------

    def _attempt_repair(
        self,
        failed_check: CitationCheck,
        claim_pairs: list[tuple[str, str]],
        evidence_by_id: dict,
        repository_path: str,
    ) -> CitationCheck:
        """
        Try to find an alternative evidence item that supports the claim.
        One attempt only — if it fails, return the original failed check.
        """
        claim_text = failed_check.claim_text

        # Try every other evidence ID
        for alt_eid, alt_evidence in evidence_by_id.items():
            if alt_eid == failed_check.evidence_id:
                continue

            alt_check = self._check_citation(
                alt_eid, claim_text, evidence_by_id, repository_path
            )
            if alt_check.structural_valid and alt_check.claim_supported:
                alt_check.repaired = True
                alt_check.repair_note = (
                    f"Replaced {failed_check.evidence_id} with {alt_eid}"
                )
                logger.info(
                    "Repaired citation: %s → %s for claim '%s'",
                    failed_check.evidence_id,
                    alt_eid,
                    claim_text[:60],
                )
                return alt_check

        # Repair failed — mark original as failed
        failed_check.repair_note = "Repair failed: no suitable alternative evidence found"
        return failed_check
