"""
Citation Validation Service

Validates that citations referenced in technical answers correspond
to real, retrieved evidence and actual code lines in the repository.

Two separate checks are performed and never conflated:

1. Structural validity of each citation against the investigation's canonical
   evidence registry: evidence ID, repository ID, file path, start/end lines
   (within the real file) and the snippet (present at those lines).
2. Semantic support of each factual claim (sentences, list items, table rows
   and code blocks) by the evidence it cites.

Zero evaluated claims is reported as ``not_evaluated`` and is never valid.
"""

import logging
import os
import re
from pathlib import Path
from typing import Any

from backend.app.models.investigation_answer import Citation, InvestigationAnswer
from backend.app.services import evidence_signals as sig
from backend.app.services.llm_service import LLMService

logger = logging.getLogger(__name__)

_EID_RE = re.compile(r"\[(E\d+)\]")
_FENCE_RE = re.compile(r"```([\w+-]*)[^\n]*\n(.*?)```", re.DOTALL)
_LIST_ITEM_RE = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s+")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_LIMITATION_RE = re.compile(
    r"\b(not found|not present|not shown|not visible|not included|not retrieved|not verified|"
    r"unverified|could not|cannot be (?:verified|confirmed|found|located|identified)|missing|no evidence|"
    r"does not appear|is unclear|unknown|not (?:directly )?observed|not in the (?:retrieved )?evidence|"
    r"none of|do(?:es)? not (?:contain|include|show|define|reference|mention)|"
    r"no (?:definition|reference|mention|import)s?)\b",
    re.IGNORECASE,
)
# Stronger subset used for claims that carry citations: only explicit
# statements about evidence absence are treated as limitations.
_ABSENCE_RE = re.compile(
    r"\bnot (?:found|shown|visible|included|retrieved|verified|present)\b|\bunverified\b|\bno evidence\b|"
    r"\bnot in the (?:retrieved )?evidence\b|\bcould not be (?:verified|confirmed)\b|\bnone of\b|"
    r"\bdoes not appear\b|\bdo(?:es)? not (?:contain|define|reference|mention)\b",
    re.IGNORECASE,
)
_INFERENCE_RE = re.compile(
    r"\(inferred\)|\binferred\b|\billustrative\b|\bpresumably\b|\blikely\b|\bprobably\b|"
    r"\bwould (?:generate|produce|translate)\b|\bequivalent sql\b|\bassum\w+\b",
    re.IGNORECASE,
)
_PERSISTENCE_CLAIM_RE = re.compile(
    r"\b(?:sav(?:e|es|ed|ing)|writ(?:e|es|ten|ing)|persist\w*|stor(?:e|es|ed|ing)|insert\w*)\b"
    r"[^.]{0,60}\b(?:database|db|table|orm|record|row)\b"
    r"|\b(?:database|db) (?:write|update|insert|record)s?\b",
    re.IGNORECASE,
)
_SQL_CLAIM_RE = re.compile(
    r"\braw sql\b|\bsql (?:query|statement)s?\b|\bexecutes? (?:an? )?sql\b|"
    r"\bSELECT\b[^.]*\bFROM\b|\bINSERT INTO\b|\bUPDATE\s+\w+\s+SET\b",
    re.IGNORECASE,
)
_ORM_CONTEXT_RE = re.compile(r"\borm\b|\btranslat\w+\b|\bgenerat\w+\b|\bunder the hood\b|\bequivalent\b", re.IGNORECASE)
_CALL_CLAIM_RE = re.compile(r"\b(?:calls|invokes)\s+`?([A-Za-z_$][\w$.]*)`?\s*(?:\(\))?", re.IGNORECASE)
_SQL_LINE_RE = re.compile(r"^\s*(SELECT|INSERT|UPDATE|DELETE)\b", re.IGNORECASE | re.MULTILINE)
# Absence asserted about the repository (not just about the retrieved
# evidence or the behaviour of cited code). Local wording such as
# "decrements it, never below 1 [E4]" is a claim about the cited code.
_NEGATION_RE = re.compile(
    r"\bthere (?:is|are) no\b|\bdoes(?:n't| not) exist\b|\bnever\b|\bno\b|\bnot\b|n't\b", re.IGNORECASE,
)
_REPO_SCOPE_RE = re.compile(
    r"\banywhere\b|\bnowhere\b|\belsewhere\b|\bno other\b|\bany other\b|"
    r"\b(?:in|across|throughout) the (?:entire |whole )?(?:code ?base|repository|repo|project|application|app)\b",
    re.IGNORECASE,
)
_SIDE_ABSENCE_RE = re.compile(
    r"\b(?:backend|frontend|server|client|api)\b[^.]{0,25}\b(?:does(?:n't| not)|never)\s+"
    r"(?:\w+\s+)?(?:calculat|comput|perform|handl|stor|persist|updat|delet|validat|call|use|recalculat)\w*|"
    r"\bonly (?:on|in) the (?:frontend|backend|client|server)\b",
    re.IGNORECASE,
)
_EXISTENCE_ABSENCE_RE = re.compile(
    r"\bthere (?:is|are) no\b|\bdoes(?:n't| not) exist\b|"
    r"\bno (?:\w+ ){0,3}(?:endpoint|handler|route|view|api|calculation|computation)s? (?:exists?|is (?:defined|implemented|provided))\b",
    re.IGNORECASE,
)
_EVIDENCE_SCOPED_RE = re.compile(
    r"\b(?:retrieved|provided|supplied|shown|available|cited) (?:evidence|code|snippets?|files?)\b|\bin the evidence\b|"
    r"\bnot (?:shown|retrieved|visible|included|verified)\b|\bevidence (?:does not|doesn't)\b|\bnot in the (?:retrieved )?evidence\b",
    re.IGNORECASE,
)
_EXHAUSTIVE_RE = re.compile(
    r"\ball (?:the )?(?:\w+ ){0,2}(?:call sites|usages|references|places|uses|operations|occurrences)\b|"
    r"\b(?:complete|exhaustive|full) list\b|\bexhaustive\b|\bthese are the only\b|"
    r"\bthe only (?:places?|call sites?|usages?|references?|operations?)\b|\bno other\b|\bnowhere else\b",
    re.IGNORECASE,
)
_MUTATION_RE = re.compile(
    r"\b(?:delet|remov|clear|updat|sav|creat|insert|writ|modif|mark)\w*\b", re.IGNORECASE,
)
_SIDE_RE = re.compile(r"\b(backend|server|api|frontend|client|ui|browser)\b", re.IGNORECASE)
# Quantity-change words and the code forms that implement them.
_DELTA_WORDS_RE = re.compile(r"\b(increment|increas|decrement|decreas|reduc|add(?:s|ed)? one|subtract)\w*", re.IGNORECASE)
_DELTA_CODE_RE = re.compile(r"\+\+|--|[+-]=|,\s*-?1\s*\)|\+\s*1\b|-\s*1\b")
_SCAN_EXTS = sig.FRONTEND_EXTS + sig.BACKEND_EXTS
_SCAN_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".angular", ".next"}


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
        validation_status: str | None = None,
        supported_claims: list[str] | None = None,
        missing_citation_ids: list[str] | None = None,
        unreferenced_citation_ids: list[str] | None = None,
        unsupported_claim_texts: list[str] | None = None,
    ):
        self.is_valid = is_valid
        self.valid_citations = valid_citations
        self.invalid_citations = invalid_citations
        self.hallucinated_ids = hallucinated_ids
        self.unsupported_claims = unsupported_claims
        self.evaluated_claims = evaluated_claims or []
        self.repair_needed = repair_needed
        self.details = details
        self.supported_claims = supported_claims or []
        self.missing_citation_ids = missing_citation_ids or []
        self.unreferenced_citation_ids = unreferenced_citation_ids or []
        self.unsupported_claim_texts = unsupported_claim_texts or []
        if validation_status is None:
            if not self.evaluated_claims:
                validation_status = "not_evaluated"
            else:
                validation_status = "verified" if is_valid else "issues_found"
        self.validation_status = validation_status

    def to_dict(self) -> dict:
        return {
            "is_valid": self.is_valid,
            "validation_status": self.validation_status,
            "valid_citation_count": len(self.valid_citations),
            "invalid_citation_count": len(self.invalid_citations),
            "invalid_citations": self.invalid_citations,
            "hallucinated_ids": self.hallucinated_ids,
            "missing_citation_ids": self.missing_citation_ids,
            "unreferenced_citation_ids": self.unreferenced_citation_ids,
            "unsupported_claims": self.unsupported_claims,
            "evaluated_claims_count": len(self.evaluated_claims),
            "supported_claims_count": len(self.supported_claims),
            "repair_needed": self.repair_needed,
            "details": self.details,
        }


def _normalize_ws(text: str) -> str:
    return " ".join((text or "").split())


_PAREN_IDS_RE = re.compile(r"\(\s*(?:see\s+)?(E\d+(?:\s*(?:,|and|&)\s*E\d+)*)\s*\)", re.IGNORECASE)
_MULTI_BRACKET_RE = re.compile(r"\[\s*(E\d+(?:\s*[,;]\s*E\d+)+)\s*\]")
_SEE_ID_RE = re.compile(r"\bsee\s+(E\d+)\b(?!\])", re.IGNORECASE)


_BARE_ID_RE = re.compile(r"(?<![\w\[])(E\d+)(?![\w\]])")
_CODE_SPAN_RE = re.compile(r"```.*?```|`[^`\n]*`", re.DOTALL)


def normalize_citation_markers(text: str) -> str:
    """Rewrite citation variants to the canonical ``[E3]`` form.

    Handles ``(E3)``, ``(E3, E4)``, ``[E3, E4]``, ``see E3`` and bare ``E3``
    in prose. Code blocks and inline code are left untouched.
    """
    def expand(match: re.Match) -> str:
        return "".join(f"[{eid}]" for eid in re.findall(r"E\d+", match.group(1)))

    def normalize_prose(segment: str) -> str:
        # Full-width brackets some models emit: 【[E14]】 / 【E14】 -> [E14]
        segment = re.sub(r"【\s*\[?(E\d+)\]?\s*】", r"[\1]", segment)
        segment = _PAREN_IDS_RE.sub(expand, segment)
        segment = _MULTI_BRACKET_RE.sub(expand, segment)
        return _BARE_ID_RE.sub(r"[\1]", segment)

    text = text or ""
    out, position = [], 0
    for match in _CODE_SPAN_RE.finditer(text):
        out.append(normalize_prose(text[position:match.start()]))
        out.append(match.group(0))
        position = match.end()
    out.append(normalize_prose(text[position:]))
    return "".join(out)


def _registry(evidence: list[dict]) -> dict[str, dict]:
    return {item["evidence_id"]: item for item in evidence if item.get("evidence_id")}


def _haystack(items: list[dict]) -> str:
    parts = []
    for item in items:
        parts.append(sig.evidence_text(item))
        parts.append(sig.normalize_path(item.get("file")))
        parts.append(str(item.get("symbol") or ""))
        parts.append(str(item.get("qualified_name") or ""))
    return "\n".join(parts).lower()


def build_citations_from_registry(answer_text: str, evidence: list[dict]) -> list[Citation]:
    """Build the citation list server-side from inline [E#] references.

    Only IDs present in the canonical registry produce citations; file, lines,
    symbol and snippet always come from the registry, never from the LLM.
    """
    registry = _registry(evidence)
    citations = []
    for eid in dict.fromkeys(_EID_RE.findall(answer_text or "")):
        item = registry.get(eid)
        if not item or not item.get("file"):
            continue
        snippet = sig.evidence_text(item)
        if len(snippet) > 200:
            snippet = snippet[:200] + "..."
        citations.append(Citation(
            evidence_id=eid,
            file=item.get("file"),
            start_line=int(item.get("start_line") or 0),
            end_line=int(item.get("end_line") or 0),
            symbol=item.get("symbol") or "",
            snippet=snippet,
            relevance=f"Cited in answer as [{eid}]",
        ))
    return citations


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

    # =========================================================================
    # VALIDATION
    # =========================================================================

    def validate_answer_citations(
        self,
        answer: InvestigationAnswer,
        evidence: list[dict],
        repository_path: str | None = None,
        repository_id: int | None = None,
        search_coverage: dict | None = None,
    ) -> CitationValidationResult:
        """
        Validate an answer's citations (structure) and claims (semantic support).

        Repository-wide claims ("there is no X", "these are all the call
        sites") are checked against *search_coverage* or a scan of the
        repository at *repository_path*.

        Structural checks per citation: evidence ID exists in the registry,
        repository ID, file path and line range match the registry entry, the
        file exists inside the repository, the line range is within the file
        and the snippet is present at those lines.
        """
        registry = _registry(evidence)
        valid_eids = set(registry)

        inline_refs = list(dict.fromkeys(_EID_RE.findall(answer.answer or "")))
        hallucinated_inline = [eid for eid in inline_refs if eid not in valid_eids]

        valid_citations: list[Citation] = []
        invalid_citations: list[dict] = []
        hallucinated_cited_ids = []

        root_path = Path(repository_path).resolve() if repository_path else None

        for citation in answer.citations:
            reason = self._citation_problem(citation, registry, root_path, repository_id)
            if reason is None:
                valid_citations.append(citation)
                continue
            if citation.evidence_id not in registry:
                hallucinated_cited_ids.append(citation.evidence_id)
            invalid_citations.append({"evidence_id": citation.evidence_id, "reason": reason})

        all_hallucinated = list(dict.fromkeys(hallucinated_inline + hallucinated_cited_ids))

        cited_ids = {c.evidence_id for c in answer.citations}
        missing_citation_ids = [eid for eid in inline_refs if eid in valid_eids and eid not in cited_ids]
        unreferenced_ids = [eid for eid in cited_ids if eid not in inline_refs]

        claims = self.analyze_claims(answer.answer or "", registry, self.scope(repository_path, search_coverage))
        evaluated = [c for c in claims if c["status"] in ("supported", "unsupported")]
        evaluated_claims = [self._claim_label(c) for c in evaluated]
        supported_claims = [self._claim_label(c) for c in evaluated if c["status"] == "supported"]
        unsupported_claims: list[str] = []
        unsupported_texts: list[str] = []
        for claim in evaluated:
            if claim["status"] == "unsupported":
                unsupported_claims.extend(claim["reasons"])
                unsupported_texts.append(claim["text"])

        for eid in all_hallucinated:
            msg = f"Answer cites hallucinated evidence ID [{eid}] which does not exist."
            if msg not in unsupported_claims:
                unsupported_claims.append(msg)
        for eid in missing_citation_ids:
            unsupported_claims.append(f"Answer references [{eid}] but the citation list does not include it.")

        # Claims that are merely consistent with *some* evidence are not
        # verified against their own cited evidence when nothing is cited.
        uncited_answer = bool(evaluated) and not inline_refs
        if uncited_answer:
            unsupported_claims.append(
                f"Answer makes {len(evaluated)} factual claims but cites no evidence inline ([E#])."
            )

        structural_issue = bool(all_hallucinated or invalid_citations or missing_citation_ids or uncited_answer)
        repair_needed = bool(structural_issue or unsupported_texts)

        if not evaluated:
            return CitationValidationResult(
                is_valid=False,
                valid_citations=valid_citations,
                invalid_citations=invalid_citations,
                hallucinated_ids=all_hallucinated,
                unsupported_claims=unsupported_claims,
                evaluated_claims=[],
                repair_needed=structural_issue,
                details=(
                    "Not evaluated: no factual claims with code references or [E#] citations could be "
                    "extracted, so claim support could not be verified."
                ),
                validation_status="not_evaluated",
                missing_citation_ids=missing_citation_ids,
                unreferenced_citation_ids=unreferenced_ids,
            )

        if repair_needed:
            issues = []
            if all_hallucinated:
                issues.append(f"{len(all_hallucinated)} hallucinated IDs ({all_hallucinated})")
            if invalid_citations:
                issues.append(f"{len(invalid_citations)} invalid citation locations")
            if missing_citation_ids:
                issues.append(f"{len(missing_citation_ids)} referenced IDs missing from citations")
            if uncited_answer:
                issues.append("no inline citations")
            if unsupported_texts:
                issues.append(f"{len(unsupported_texts)} unsupported claims")
            details = (
                f"Validation issues detected: {'; '.join(issues)}. "
                f"{len(supported_claims)}/{len(evaluated)} evaluated claims supported."
            )
        else:
            details = f"{len(evaluated)} factual claims evaluated; all supported by their cited evidence."

        return CitationValidationResult(
            is_valid=not repair_needed,
            valid_citations=valid_citations,
            invalid_citations=invalid_citations,
            hallucinated_ids=all_hallucinated,
            unsupported_claims=unsupported_claims,
            evaluated_claims=evaluated_claims,
            repair_needed=repair_needed,
            details=details,
            supported_claims=supported_claims,
            missing_citation_ids=missing_citation_ids,
            unreferenced_citation_ids=unreferenced_ids,
            unsupported_claim_texts=unsupported_texts,
        )

    @staticmethod
    def _claim_label(claim: dict) -> str:
        ids = ",".join(claim["ids"]) or "uncited"
        return f"[{ids}] {claim['kind']}: {_normalize_ws(claim['text'])[:90]}"

    def _citation_problem(
        self,
        citation: Citation,
        registry: dict[str, dict],
        root_path: Path | None,
        repository_id: int | None,
    ) -> str | None:
        eid = citation.evidence_id
        item = registry.get(eid)
        if item is None:
            return f"Evidence ID '{eid}' does not exist in supplied evidence."

        if repository_id is not None and item.get("repository_id") not in (None, repository_id):
            return f"Evidence {eid} belongs to repository {item.get('repository_id')}, not {repository_id}."

        ev_file = item.get("file")
        if citation.file and ev_file and sig.normalize_path(citation.file) != sig.normalize_path(ev_file):
            return f"File mismatch: cited '{citation.file}' vs evidence '{ev_file}'."

        if citation.start_line > 0 and citation.end_line > 0 and citation.start_line > citation.end_line:
            return f"Invalid line range: {citation.start_line}-{citation.end_line}."

        ev_start, ev_end = item.get("start_line"), item.get("end_line")
        if ev_start and ev_end and (citation.start_line, citation.end_line) != (int(ev_start), int(ev_end)):
            return (
                f"Line mismatch: cited {citation.start_line}-{citation.end_line} "
                f"but evidence {eid} is {ev_start}-{ev_end}."
            )

        if root_path and citation.file:
            target_file = (root_path / citation.file).resolve()
            if not target_file.is_relative_to(root_path):
                return f"Security: Citation file '{citation.file}' is outside repository root."
            if not target_file.is_file():
                return f"File does not exist in repository: {citation.file}."
            try:
                lines = target_file.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError as exc:
                logger.debug("Could not read %s: %s", citation.file, exc)
                return None
            if citation.start_line > len(lines) or citation.end_line > len(lines):
                return (
                    f"Citation lines {citation.start_line}-{citation.end_line} exceed file length "
                    f"({len(lines)}) in {citation.file}."
                )
            if citation.start_line > 0 and citation.end_line > 0:
                actual = _normalize_ws("\n".join(lines[citation.start_line - 1: citation.end_line]))
                snippet = (citation.snippet or "")
                for marker in ("\n... (truncated)", "..."):
                    if snippet.endswith(marker):
                        snippet = snippet[: -len(marker)]
                probe = _normalize_ws(snippet)[:150]
                if probe and probe not in actual:
                    return f"Snippet for {eid} does not match {citation.file}:{citation.start_line}-{citation.end_line}."
        return None

    # =========================================================================
    # CLAIM EXTRACTION & SEMANTIC SUPPORT
    # =========================================================================

    @staticmethod
    def extract_claims(answer_text: str) -> list[dict]:
        """Split an answer into claim units: sentences, list items, table rows, code blocks."""
        claims: list[dict] = []
        text = answer_text or ""
        position = 0
        pending_ids: list[str] = []

        def add_prose(segment: str) -> None:
            for raw_line_block in re.split(r"\n\s*\n", segment):
                lines = raw_line_block.split("\n")
                units: list[str] = []
                buffer: list[str] = []
                for line in lines:
                    stripped = line.strip()
                    if not stripped or re.fullmatch(r"\|?[\s:|-]+\|?", stripped) and "-" in stripped:
                        continue
                    if stripped.startswith("#"):
                        continue
                    if _LIST_ITEM_RE.match(line) or stripped.startswith("|"):
                        if buffer:
                            units.append(" ".join(buffer))
                            buffer = []
                        units.append(stripped)
                    else:
                        buffer.append(stripped)
                if buffer:
                    units.append(" ".join(buffer))
                for unit in units:
                    kind = "list_item" if _LIST_ITEM_RE.match(unit) else "table_row" if unit.startswith("|") else "sentence"
                    for sentence in _SENTENCE_SPLIT_RE.split(unit):
                        sentence = sentence.strip()
                        if not sentence:
                            continue
                        if re.fullmatch(r"(?:\[E\d+\][\s,.;]*)+", sentence) and claims:
                            # Citation placed after the full stop belongs to the previous claim.
                            claims[-1]["ids"] = list(dict.fromkeys(claims[-1]["ids"] + _EID_RE.findall(sentence)))
                            continue
                        claims.append({
                            "text": sentence,
                            "kind": kind,
                            "ids": list(dict.fromkeys(_EID_RE.findall(sentence))),
                        })

        for match in _FENCE_RE.finditer(text):
            before = text[position: match.start()]
            add_prose(before)
            intro_ids = _EID_RE.findall(before.strip().split("\n")[-1]) if before.strip() else []
            intro_text = before.strip().split("\n")[-1] if before.strip() else ""
            body = match.group(2)
            claims.append({
                "text": match.group(0),
                "kind": "code_block",
                "language": (match.group(1) or "").lower(),
                "code": body,
                "intro": intro_text,
                "ids": list(dict.fromkeys(_EID_RE.findall(body) + intro_ids)),
            })
            position = match.end()
        add_prose(text[position:])
        return claims

    @staticmethod
    def scope(repository_path: str | None = None, search_coverage: dict | None = None) -> dict:
        """What repository-wide checks may use: recorded search coverage and the repository root."""
        root = Path(repository_path).resolve() if repository_path else None
        return {"coverage": search_coverage or {}, "root": root if root and root.is_dir() else None, "index": None}

    @staticmethod
    def _repo_index(scope: dict) -> list[tuple[str, list[str]]]:
        """Runtime source files of the repository as (relative path, lines); built once per scope."""
        if scope.get("index") is not None:
            return scope["index"]
        index: list[tuple[str, list[str]]] = []
        root = scope.get("root")
        if root:
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d not in _SCAN_SKIP_DIRS]
                for name in filenames:
                    if not name.endswith(_SCAN_EXTS) or len(index) >= 4000:
                        continue
                    path = Path(dirpath) / name
                    rel = path.relative_to(root).as_posix()
                    if sig.is_indirect(rel):
                        continue
                    try:
                        if path.stat().st_size > 400_000:
                            continue
                        index.append((rel, path.read_text(encoding="utf-8", errors="ignore").split("\n")))
                    except OSError:
                        continue
        scope["index"] = index
        return index

    def _occurrences(self, scope: dict, identifier: str, limit: int = 20) -> list[tuple[str, int]]:
        word = re.compile(rf"(?<![\w$]){re.escape(identifier)}(?![\w$])")
        hits = []
        for rel, lines in self._repo_index(scope):
            for number, line in enumerate(lines, start=1):
                if word.search(line):
                    hits.append((rel, number))
                    if len(hits) >= limit:
                        return hits
        return hits

    @staticmethod
    def _in_registry(registry: dict[str, dict], file: str, line: int) -> bool:
        for item in registry.values():
            try:
                if (
                    sig.normalize_path(item.get("file")) == file
                    and int(item.get("start_line")) <= line <= int(item.get("end_line"))
                ):
                    return True
            except (TypeError, ValueError):
                continue
        return False

    # Absence topics a complete repository inventory scan can establish.
    _INVENTORY_TOPICS = (
        (re.compile(r"\b(?:backend|server[- ]side|server code|web server)\b", re.IGNORECASE),
         lambda inv: not inv.get("has_backend")),
        (re.compile(r"\b(?:api|apis|endpoints?|routes?|rest)\b", re.IGNORECASE),
         lambda inv: not [s for s in inv.get("sites", {}).get("endpoints", []) if s.get("kind") not in ("dev-server", "include")]),
        (re.compile(r"\b(?:database|db|persist\w*|orm|sql|tables?|data models?|models?)\b", re.IGNORECASE),
         lambda inv: not inv.get("has_persistence")),
        (re.compile(r"\b(?:auth\w*|log ?ins?|sign[- ]?in|sessions?|tokens?|jwt|password\w*|credentials?)\b", re.IGNORECASE),
         lambda inv: not inv.get("counts", {}).get("auth")),
        (re.compile(r"\b(?:http|fetch|axios|network|requests?|api calls?|xhr|websockets?)\b", re.IGNORECASE),
         lambda inv: not inv.get("counts", {}).get("http_calls")),
    )

    @classmethod
    def _inventory_backs_absence(cls, text: str, inventory: dict | None) -> bool:
        """A repository-wide negative claim is backed when every topic it names is empty in a complete scan."""
        if not inventory or not inventory.get("complete"):
            return False
        if cls._UNUSED_RE.search(text):
            unused = {u["name"].lower() for u in inventory.get("unused_packages", [])}
            known = unused | {p["name"].lower() for p in inventory.get("packages", [])}
            named = {n for n in known if re.search(rf"(?<![\w@/-]){re.escape(n)}(?![\w/-])", text, re.IGNORECASE)}
            if named:
                return named <= unused
        topics = [check for pattern, check in cls._INVENTORY_TOPICS if pattern.search(text)]
        return bool(topics) and all(check(inventory) for check in topics)

    _UNUSED_RE = re.compile(
        r"\bnot (?:actually )?(?:imported|used|referenced)\b|\bunused\b|\bnever (?:imported|used)\b|\bno (?:source )?file imports\b",
        re.IGNORECASE,
    )

    _INVENTORY_LIST_TOPICS = (
        (re.compile(r"\b(?:endpoints?|routes?|apis?)\b", re.IGNORECASE), "endpoints"),
        (re.compile(r"\b(?:models?|entit(?:y|ies)|tables?|schemas?)\b", re.IGNORECASE), "data_models"),
        (re.compile(r"\b(?:auth\w*|log ?ins?)\b", re.IGNORECASE), "auth"),
        (re.compile(r"\b(?:http|network|fetch|axios|api calls?)\b", re.IGNORECASE), "http_calls"),
    )

    def _inventory_backs_exhaustive(self, text: str, inventory: dict | None, registry: dict) -> bool:
        """An exhaustive claim ("no other endpoints", "the only routes") is backed by a complete scan
        whose every site of the named category is in the evidence (or which found none)."""
        if not inventory or not inventory.get("complete"):
            return False
        if self._inventory_backs_absence(text, inventory):
            return True
        sites = inventory.get("sites", {})
        counts = inventory.get("counts", {})
        categories = [cat for pattern, cat in self._INVENTORY_LIST_TOPICS if pattern.search(text)]
        if not categories:
            return False
        for cat in categories:
            listed = sites.get(cat, [])
            if counts.get(cat, 0) > len(listed):
                return False
            if not all(self._in_registry(registry, s["file"], s["line"]) for s in listed):
                return False
        return True

    def _check_repository_claim(self, text: str, identifiers: set[str], registry: dict, scope: dict | None) -> list[str]:
        """Reasons why a repository-wide negative or exhaustive claim lacks search coverage."""
        scope = scope or self.scope()
        coverage = scope.get("coverage") or {}
        short_text = _normalize_ws(_EID_RE.sub("", text))[:90]
        reasons: list[str] = []
        names = {t.split(".")[-1].rstrip("()") for t in identifiers if len(t.split(".")[-1]) > 2}

        if _EXHAUSTIVE_RE.search(text):
            if self._inventory_backs_exhaustive(text, coverage.get("inventory"), registry):
                return reasons
            refs = coverage.get("references", {})
            relevant = [refs[n] for n in names if n in refs] or (list(refs.values()) if len(refs) == 1 else [])
            complete = [
                cov for cov in relevant
                if cov.get("complete") and all(self._in_registry(registry, s["file"], s["line"]) for s in cov.get("sites", []))
            ]
            if not complete:
                reasons.append(
                    f"Claim asserts a complete/exhaustive list, but no complete repository-wide search with every "
                    f"site retrieved supports it: \"{short_text}\"."
                )
            return reasons

        cited = bool(_EID_RE.search(text))
        repository_scoped = (
            (_REPO_SCOPE_RE.search(text) and _NEGATION_RE.search(text))
            or _SIDE_ABSENCE_RE.search(text)
            or (_EXISTENCE_ABSENCE_RE.search(text) and not cited)
        )
        if not repository_scoped or _EVIDENCE_SCOPED_RE.search(text) or _INFERENCE_RE.search(text):
            return reasons

        # Absence of a computation on one side, checked against the
        # repository-wide computation search.
        computations = coverage.get("computations", {})
        terms = [t for t in sig.numeric_terms(text) if t in computations]
        sides = {m.lower() for m in _SIDE_RE.findall(text)}
        if terms and sides:
            for term in terms:
                record = computations[term]
                for side in ("backend", "frontend"):
                    mentioned = side in sides or (side == "backend" and sides & {"server", "api"}) or (
                        side == "frontend" and sides & {"client", "ui", "browser"})
                    if mentioned and record.get(side):
                        where = ", ".join(f"{s['file']}:{s['line']}" for s in record[side][:3])
                        reasons.append(
                            f"Claim asserts something about {term} on the {side} that the repository contradicts: "
                            f"{side} code computing {term} exists at {where}: \"{short_text}\"."
                        )
                if not record.get("complete"):
                    reasons.append(f"Claim about the absence of {term} computation is not backed by a complete search.")
            return reasons

        if names and scope.get("root"):
            for name in sorted(names)[:3]:
                outside = [(f, l) for f, l in self._occurrences(scope, name) if not self._in_registry(registry, f, l)]
                if outside:
                    where = ", ".join(f"{f}:{l}" for f, l in outside[:3])
                    reasons.append(
                        f"Claim asserts absence about {name}, but {name} also appears in code that was not examined "
                        f"({where}): \"{short_text}\"."
                    )
            return reasons

        refs = coverage.get("references", {})
        if names and any(n in refs and refs[n].get("complete") for n in names):
            return reasons
        if self._inventory_backs_absence(text, coverage.get("inventory")):
            return reasons
        reasons.append(
            f"Repository-wide negative claim without search coverage (only the retrieved evidence was examined): "
            f"\"{short_text}\"."
        )
        return reasons

    def analyze_claims(self, answer_text: str, registry: dict[str, dict], scope: dict | None = None) -> list[dict]:
        """Classify every claim unit as supported / unsupported / limitation / inference / prose."""
        all_items = list(registry.values())
        all_haystack = _haystack(all_items)
        claims = self.extract_claims(answer_text)

        for claim in claims:
            claim["reasons"] = []
            if claim["kind"] == "code_block":
                self._check_code_block(claim, registry)
                continue

            text = claim["text"]
            # Repair notes quote the removed statements; they are not claims.
            if re.match(r"\s*(?:[-*]\s*)?Not verified:", text):
                claim["status"] = "limitation"
                continue
            identifiers = sig.claim_identifiers(_EID_RE.sub(" ", text))
            repository_reasons = self._check_repository_claim(text, identifiers, registry, scope)
            if repository_reasons:
                claim["reasons"] = repository_reasons
                claim["status"] = "unsupported"
                continue
            if (_ABSENCE_RE if claim["ids"] else _LIMITATION_RE).search(text):
                claim["status"] = "limitation"
                continue
            content_words = [
                w for w in re.findall(r"[a-z][a-z0-9_]{2,}", _EID_RE.sub(" ", text).lower())
                if w not in sig.STOP_WORDS
            ]
            if not identifiers and (not claim["ids"] or len(content_words) < 2):
                # No code reference and too little content to be a factual
                # claim (headings, "Code [E4]:" labels, connective prose).
                claim["status"] = "prose"
                continue
            if _INFERENCE_RE.search(text) and not claim["ids"]:
                claim["status"] = "inference"
                continue

            hallucinated = [eid for eid in claim["ids"] if eid not in registry]
            for eid in hallucinated:
                claim["reasons"].append(
                    f"Citation [{eid}] references an evidence ID that does not exist in supplied evidence."
                )
            cited = [registry[eid] for eid in claim["ids"] if eid in registry]

            # A negative claim ("makes no fetch/axios calls") names things that
            # by definition are not in the evidence; when the complete
            # repository scan backs the absence, those names are not fabricated.
            if _NEGATION_RE.search(text) and self._inventory_backs_absence(
                text, ((scope or {}).get("coverage") or {}).get("inventory"),
            ):
                identifiers = {t for t in identifiers if sig.identifier_grounded(t, all_haystack)}

            if cited:
                self._check_cited_claim(claim, text, identifiers, cited, all_items, all_haystack)
            elif not hallucinated:
                # Uncited factual claim: every code identifier must exist somewhere in the registry.
                ungrounded = [t for t in identifiers if not sig.identifier_grounded(t, all_haystack)]
                if ungrounded:
                    claim["reasons"].append(
                        f"Uncited claim mentions {', '.join(sorted(ungrounded)[:3])}, which does not appear "
                        f"in any retrieved evidence: \"{_normalize_ws(text)[:80]}\"."
                    )
            claim["status"] = "unsupported" if claim["reasons"] else "supported"
        return claims

    def _check_cited_claim(
        self,
        claim: dict,
        text: str,
        identifiers: set[str],
        cited: list[dict],
        all_items: list[dict],
        all_haystack: str,
    ) -> None:
        reasons = claim["reasons"]
        ids = ",".join(f"[{i}]" for i in claim["ids"])
        cited_haystack = _haystack(cited)
        text_lower = text.lower()
        labelled_inference = bool(_INFERENCE_RE.search(text))

        # 1. Identifier grounding.
        if identifiers:
            in_cited = [t for t in identifiers if sig.identifier_grounded(t, cited_haystack)]
            fabricated = [t for t in identifiers if not sig.identifier_grounded(t, all_haystack)]
            if fabricated:
                reasons.append(
                    f"Claim citing {ids} mentions {', '.join(sorted(fabricated)[:3])}, which does not appear "
                    f"in any retrieved evidence."
                )
            elif not in_cited:
                reasons.append(
                    f"Claim citing {ids} mentions {', '.join(sorted(identifiers)[:3])}, none of which appear "
                    f"in the cited evidence."
                )
        elif any(sig.evidence_text(i).strip() for i in cited):
            words = {
                w for w in re.findall(r"[a-z][a-z0-9_]{3,}", text_lower)
                if w not in sig.STOP_WORDS
            }
            # "increments/decrements" is implemented as ++/--/+=/, -1) in code.
            delta_match = bool(_DELTA_WORDS_RE.search(text)) and bool(_DELTA_CODE_RE.search(cited_haystack))
            if words and not delta_match and not any(sig._stem(w) in cited_haystack for w in words):
                reasons.append(f"Claim citing {ids} shares no terms with the cited evidence.")

        # 2. Backend persistence must come from backend ORM/SQL code.
        if _PERSISTENCE_CLAIM_RE.search(text) and not _LIMITATION_RE.search(text):
            if not any(
                any(sig.backend_persistence_ops(i)[k] for k in ("writes", "reads", "raw_sql")) for i in cited
            ):
                kinds = []
                if any(sig.has_client_storage(sig.evidence_text(i)) for i in cited):
                    kinds.append("client-side storage (localStorage/sessionStorage)")
                if any(sig.file_role(i.get("file")) == sig.ROLE_ADMIN for i in cited):
                    kinds.append("an admin registration")
                what = " and ".join(kinds) or "code without any backend ORM/SQL operation"
                reasons.append(f"Claim citing {ids} asserts database persistence, but the cited evidence is {what}.")

        # 3. SQL presented as observed when only ORM code exists.
        if _SQL_CLAIM_RE.search(text) and not labelled_inference and not _ORM_CONTEXT_RE.search(text):
            if not any(sig.backend_persistence_ops(i)["raw_sql"] for i in cited):
                reasons.append(f"Claim citing {ids} describes SQL as observed, but the cited evidence contains no raw SQL.")

        # 4. Database engine must be established by configuration evidence.
        if not labelled_inference:
            for engine in sig.engines_mentioned(text):
                if not sig.engine_established(engine, all_items):
                    reasons.append(
                        f"Claim citing {ids} names the database engine '{engine}', which no retrieved configuration establishes."
                    )

        # 5. "X calls Y" requires the call site in the cited code.
        for target in _CALL_CLAIM_RE.findall(text):
            short = target.split(".")[-1].lower()
            if len(short) > 2 and not re.search(rf"\b{re.escape(short)}\s*\(", cited_haystack):
                reasons.append(f"Claim citing {ids} says something calls {target}, but no such call appears in the cited code.")

        # 6. A data change attributed to a named function must be cited from
        # that function's code, not from another view touching the same model.
        if _MUTATION_RE.search(text) and not _LIMITATION_RE.search(text):
            for token in identifiers:
                name = token.split(".")[-1].rstrip("()")
                if not re.fullmatch(r"_*[a-z][a-z0-9]*(?:_[a-z0-9]+)+|_*[a-z]+(?:[A-Z][a-z0-9]*)+", name):
                    continue
                if not re.search(rf"(?<![\w$]){re.escape(name)}(?![\w$])", cited_haystack) and \
                        re.search(rf"(?<![\w$]){re.escape(name)}(?![\w$])", all_haystack):
                    reasons.append(
                        f"Claim citing {ids} attributes a data change to {name}, but the cited evidence does not "
                        f"contain {name}."
                    )

        # 7. Known false-attribution patterns.
        reasons.extend(self._pattern_checks(text, claim["ids"], cited))

    def _check_code_block(self, claim: dict, registry: dict[str, dict]) -> None:
        code = claim.get("code", "")
        intro = claim.get("intro", "")
        labelled_inference = bool(_INFERENCE_RE.search(intro) or _INFERENCE_RE.search(code))
        is_sql = claim.get("language") == "sql" or bool(_SQL_LINE_RE.search(code))
        hallucinated = [eid for eid in claim["ids"] if eid not in registry]
        cited = [registry[eid] for eid in claim["ids"] if eid in registry]
        reasons = claim["reasons"]
        for eid in hallucinated:
            reasons.append(f"Code block cites [{eid}], which does not exist in supplied evidence.")

        if labelled_inference:
            # Explicitly labelled illustrative/inferred code (incl. SQL) is allowed.
            claim["status"] = "inference"
            return

        if is_sql and not any(sig.backend_persistence_ops(i)["raw_sql"] for i in (cited or registry.values())):
            reasons.append("SQL code block is presented as repository code, but no raw SQL exists in the evidence; it must be labelled illustrative.")

        code_lines = [
            _normalize_ws(line) for line in code.split("\n")
            if len(line.strip()) >= 8 and not re.fullmatch(r"[\s{}()\[\];,.]*|\s*(#|//).*|\s*\.\.\.\s*", line)
        ]
        if code_lines and not reasons:
            source = cited or list(registry.values())
            haystack = _normalize_ws("\n".join(sig.evidence_text(i) for i in source))
            found = sum(1 for line in code_lines if line in haystack)
            if found / len(code_lines) < 0.6:
                where = f"cited evidence {','.join(claim['ids'])}" if cited else "any retrieved evidence"
                reasons.append(
                    f"Code block contains lines not present in {where} ({found}/{len(code_lines)} lines verified)."
                )
        if not code_lines and not reasons:
            claim["status"] = "prose"
            return
        claim["status"] = "unsupported" if reasons else "supported"

    @staticmethod
    def _pattern_checks(text: str, ids: list[str], cited: list[dict]) -> list[str]:
        reasons = []
        sentence_lower = text.lower()
        for eid, ev_item in zip([i for i in ids], cited):
            snippet_lower = sig.evidence_text(ev_item).lower()
            file_lower = sig.normalize_path(ev_item.get("file")).lower()
            symbol = str(ev_item.get("symbol") or "")

            # A guest-cart merge handler described as the normal quantity-update path.
            is_claiming_quantity_update = any(
                kw in sentence_lower for kw in (
                    "quantity update", "update quantity", "increase quantity", "decrease quantity",
                    "normal quantity", "adjust quantity", "updating quantity", "increment quantity",
                    "decrement quantity", "user adjusts", "user clicks", "cart quantity is updated",
                    "processes quantity",
                )
            )
            if is_claiming_quantity_update and ("cartsyncview" in snippet_lower or "cartsyncview" in symbol.lower()):
                if "fresh login only" in snippet_lower or "guest cart" in snippet_lower or "cart/sync" in snippet_lower:
                    if "cartaddview" not in snippet_lower and "increaseqty" not in snippet_lower:
                        reasons.append(
                            f"Claim citing [{eid}] describes CartSyncView as handling normal quantity updates, "
                            f"but the cited code is implemented for guest-cart merging upon fresh login only."
                        )

            # URL routing asserted from a file without route configuration.
            is_claiming_route = any(
                kw in sentence_lower for kw in ("url route", "routed via", "endpoint mapping", "routes to", "path(")
            )
            if is_claiming_route and not sig.extract_routes(sig.evidence_text(ev_item)) and not any(
                kw in file_lower for kw in ("urls.py", "routes", "router")
            ):
                reasons.append(
                    f"Claim citing [{eid}] asserts a URL route mapping, but cited file {ev_item.get('file')} "
                    f"does not contain URL configuration."
                )

            # UI event handling asserted from a backend file.
            is_claiming_ui = any(
                kw in sentence_lower for kw in ("ui event handler", "button click", "click event", "template button")
            )
            if is_claiming_ui and not sig.is_frontend_runtime(ev_item.get("file")):
                reasons.append(
                    f"Claim citing [{eid}] asserts UI event handling, but cited file {ev_item.get('file')} "
                    f"is a backend file."
                )

            # Existence/implementation asserted from admin/test/migration evidence.
            is_positive_existence_claim = any(
                kw in sentence_lower for kw in (
                    "is defined", "is implemented", "handles", "processes",
                    "exists in", "can be found", "is located", "lives in",
                )
            ) and not any(neg in sentence_lower for neg in ("not", "missing", "absent", "no "))
            if sig.is_indirect(ev_item.get("file")) and is_positive_existence_claim:
                reasons.append(
                    f"Claim citing [{eid}] positively asserts existence/implementation using "
                    f"an indirect file ({ev_item.get('file')}). Admin registrations, tests, and "
                    f"migrations do not prove that the primary implementation exists."
                )
        return reasons

    # Kept for callers/tests that used the previous helper name.
    def _evaluate_claim_to_source_support(
        self,
        answer_text: str,
        evidence_by_id: dict[str, dict],
    ) -> tuple[list[str], list[str]]:
        claims = self.analyze_claims(answer_text, evidence_by_id)
        evaluated = [self._claim_label(c) for c in claims if c["status"] in ("supported", "unsupported")]
        unsupported = [r for c in claims if c["status"] == "unsupported" for r in c["reasons"]]
        return evaluated, unsupported

    # =========================================================================
    # REPAIR
    # =========================================================================

    def repair_answer(
        self,
        answer: InvestigationAnswer,
        evidence: list[dict],
        validation_result: CitationValidationResult,
        repository_path: str | None = None,
        search_coverage: dict | None = None,
    ) -> InvestigationAnswer:
        """
        Repair an answer with hallucinated citations, invalid locations or unsupported claims.

        1. Optional LLM rewrite restricted to registry evidence IDs.
        2. Deterministic safe-partial pass: strip unknown IDs, remove claims that are
           still unsupported, rebuild citations from the registry.
        No new evidence IDs are ever created.
        """
        registry = _registry(evidence)
        scope = self.scope(repository_path, search_coverage)
        text = normalize_citation_markers(answer.answer or "")
        limitations = list(answer.limitations)
        confidence = answer.confidence

        # Deterministic removal is preferred: it keeps every verified statement
        # and costs no LLM call. A rewrite is only worth it when citations are
        # structurally broken or a large share of the claims is unsupported.
        claims = self.analyze_claims(text, registry, scope)
        evaluated = [c for c in claims if c["status"] in ("supported", "unsupported")]
        unsupported = [c for c in evaluated if c["status"] == "unsupported"]
        needs_rewrite = bool(validation_result.hallucinated_ids or validation_result.invalid_citations) or (
            evaluated and len(unsupported) / len(evaluated) > 1 / 3
        ) or (evaluated and not _EID_RE.search(text))

        if self.llm_service and needs_rewrite:
            try:
                rewritten = self._llm_rewrite(answer, evidence, validation_result)
                if rewritten:
                    text, llm_limitations, llm_confidence = rewritten
                    text = normalize_citation_markers(text)
                    limitations.extend(l for l in llm_limitations if l not in limitations)
                    confidence = llm_confidence
            except Exception as exc:
                logger.warning("LLM citation repair failed, using deterministic repair: %s", exc)

        # Statements whose only citations are nonexistent IDs were never
        # grounded: remove them entirely rather than leave them uncited.
        removed_reasons: list[str] = []
        for claim in self.extract_claims(text):
            bad = [eid for eid in claim["ids"] if eid not in registry]
            if bad and len(bad) == len(claim["ids"]):
                text = self._remove_fragment(text, claim["text"])
                removed_reasons.append(
                    f"Statement cited only nonexistent evidence ({', '.join(bad)}): \"{_normalize_ws(claim['text'])[:80]}\""
                )

        # Strip IDs that are not in the registry (from the LLM or the original).
        stripped_ids = sorted({eid for eid in _EID_RE.findall(text) if eid not in registry} | set(validation_result.hallucinated_ids))
        for bad_id in stripped_ids:
            text = re.sub(rf"\s?\[{re.escape(bad_id)}\]", "", text)

        # Remove claims that remain unsupported.
        for claim in self.analyze_claims(text, registry, scope):
            if claim["status"] != "unsupported":
                continue
            text = self._remove_fragment(text, claim["text"])
            removed_reasons.extend(claim["reasons"])

        text = re.sub(r"[ \t]{2,}", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()

        for reason in list(validation_result.unsupported_claims) + removed_reasons:
            entry = f"Unverified claim corrected: {reason}"
            if entry not in limitations:
                limitations.append(entry)
        if stripped_ids:
            limitations.append(f"Removed unsupported citations ({', '.join(stripped_ids)}) during validation.")

        if removed_reasons:
            notes = "\n".join(f"- Not verified: {_EID_RE.sub(lambda m: m.group(1), r)}" for r in dict.fromkeys(removed_reasons))
            text = (text + "\n\n" if text else "") + "**Unverified statements removed:**\n" + notes

        remaining = [
            c for c in self.analyze_claims(text, registry, scope) if c["status"] == "supported"
        ]
        if not remaining:
            confidence = "insufficient"
        elif confidence == "high" and (removed_reasons or stripped_ids or validation_result.unsupported_claims):
            confidence = "partial"

        return InvestigationAnswer(
            question=answer.question,
            answer=text,
            citations=build_citations_from_registry(text, evidence),
            confidence=confidence,
            limitations=limitations,
            execution_time_seconds=answer.execution_time_seconds,
        )

    @staticmethod
    def _remove_fragment(text: str, fragment: str) -> str:
        """Remove a claim unit from the answer, tolerating re-wrapped whitespace."""
        if fragment in text:
            return text.replace(fragment, "", 1)
        tokens = fragment.split()
        if not tokens:
            return text
        pattern = r"\s+".join(re.escape(token) for token in tokens)
        return re.sub(pattern, "", text, count=1)

    def _llm_rewrite(
        self,
        answer: InvestigationAnswer,
        evidence: list[dict],
        validation_result: CitationValidationResult,
    ) -> tuple[str, list[str], str] | None:
        system_prompt = """\
You are a Citation and Factual Grounding Repair Assistant.
The previous technical answer contained invalid citations or unsupported claims.
REWRITE the answer following these strict rules:
1. ONLY reference the evidence IDs listed below, written exactly as [E1], [E2]. Never invent IDs.
   Keep every statement that is not listed as unsupported, with its citation.
2. Remove or explicitly qualify every claim listed as unsupported.
3. Do not attribute an operation to a handler, function or route unless the cited code shows it.
4. If stages or connections are missing from the evidence, state clearly that they are unverified.
5. Set confidence to "partial" or "insufficient" when key parts are unverified."""

        # Evidence already cited by the answer first, then the rest in the
        # caller's (relevance) order, so the rewrite sees what the answer used.
        cited_ids = list(dict.fromkeys(_EID_RE.findall(answer.answer or "")))
        ordered = sorted(
            (ev for ev in evidence if ev.get("evidence_id")),
            key=lambda ev: (ev["evidence_id"] not in cited_ids, cited_ids.index(ev["evidence_id"]) if ev["evidence_id"] in cited_ids else 0),
        )
        valid_ev_summary = [
            f"[{ev['evidence_id']}] {sig.normalize_path(ev.get('file'))}:{ev.get('start_line')}-{ev.get('end_line')} "
            f"({ev.get('symbol') or ''}): {sig.evidence_text(ev)[:180]}"
            for ev in ordered
        ][:12]
        registry = _registry(evidence)

        user_prompt = f"""ORIGINAL QUESTION:
{answer.question}

ORIGINAL ANSWER WITH DEFECTS:
{answer.answer}

UNSUPPORTED CLAIMS DETECTED:
{validation_result.unsupported_claims[:8]}

HALLUCINATED IDS DETECTED:
{validation_result.hallucinated_ids}

VALID AVAILABLE EVIDENCE ({len(registry)} items):
{chr(10).join(valid_ev_summary)}

Return the repaired answer with ONLY valid citations [E#] and appropriate confidence."""

        raw = self.llm_service.generate_structured(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_schema=self.REPAIR_SCHEMA,
            max_tokens=1500,
        )
        text = raw.get("answer", "")
        if not text.strip():
            return None
        return text, list(raw.get("limitations", [])), raw.get("confidence", "partial")
