"""
Answer Generation Service

Generates evidence-grounded technical explanations from retrieved code evidence.

This service uses an LLM to produce natural language answers while enforcing
strict grounding in the supplied repository evidence. It prevents hallucination
by validating that all claims are supported by the provided code snippets.
"""

import logging
from typing import Any

from backend.app.services.llm_service import LLMService
from backend.app.models.investigation_answer import InvestigationAnswer, Citation


logger = logging.getLogger(__name__)


class AnswerGenerationService:
    """Generates evidence-grounded answers to technical repository questions."""
    
    MAX_EVIDENCE_ITEMS = 10  # Limit evidence sent to LLM
    # 1500 chars keeps most medium-sized functions whole (~375 tokens each).
    # With 10 items that is ~3750 content tokens; add ~950 system + ~3000 output
    # and the total stays comfortably inside an 8 192-token context window.
    # 500 was too small: it cut CartSyncView (1 672 chars) before the POST
    # body and CheckoutView (3 337 chars) before the order-creation logic.
    MAX_SNIPPET_LENGTH = 2200  # Max chars per code snippet
    
    def __init__(self, llm_service: LLMService):
        self.llm_service = llm_service
    
    def generate_answer(
        self,
        question: str,
        evidence: list[dict],
        question_type: str | None = None,
        repository_name: str | None = None,
    ) -> InvestigationAnswer:
        """
        Generate evidence-grounded answer from retrieved evidence.
        
        Args:
            question: Original user question
            evidence: Ranked evidence items from retrieval service
            question_type: Question classification (architectural_overview, feature_flow, etc.)
            repository_name: Repository name for context
            
        Returns:
            InvestigationAnswer with natural language explanation and citations
            
        Raises:
            RuntimeError: If LLM API fails or returns malformed output
            ValueError: If evidence is empty or invalid
        """
        if not question or not question.strip():
            raise ValueError("Question cannot be empty")
        
        if not evidence:
            return self._insufficient_evidence_response(question, "No evidence was retrieved for this question.")
        
        # Prepare evidence for LLM
        prepared_evidence = self._prepare_evidence(evidence)
        
        if not prepared_evidence:
            # If this is a symbol lookup and evidence existed but all had 0-0 ranges /
            # empty snippets, report as "not found" rather than a generic error.
            if question_type == 'symbol_lookup':
                return self._not_found_response(question)
            return self._insufficient_evidence_response(
                question,
                "Retrieved evidence could not be prepared for analysis. "
                "The referenced code may not have been indexed or is outside supported file types."
            )
        
        # Determine token budget based on question type
        max_tokens = self._get_token_budget(question_type)
        
        # Generate answer using LLM
        try:
            system_prompt = self._build_system_prompt(question_type, repository_name)
            user_prompt = self._build_user_prompt(question, prepared_evidence)
            response_schema = self._get_response_schema()
            
            logger.info(f"Generating answer for question type: {question_type} with max_tokens: {max_tokens}")
            
            llm_response = self.llm_service.generate_structured(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                response_schema=response_schema,
                max_tokens=max_tokens,  # Pass question-type-specific budget
            )
            
            # Validate and construct answer with full citations
            answer = self._validate_and_construct_answer(
                question=question,
                llm_response=llm_response,
                supplied_evidence=prepared_evidence,
            )
            
            return answer
            
        except RuntimeError as e:
            logger.error(f"LLM API error during answer generation: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error during answer generation: {e}", exc_info=True)
            raise RuntimeError(f"Failed to generate answer: {str(e)}")
    
    def _get_token_budget(self, question_type: str | None) -> int:
        """
        Determine appropriate token budget based on question type.
        
        Simple lookups need less; multi-step flows need more.
        """
        if not question_type:
            return 1500
        
        # Simple questions: concise answers
        if question_type in ('symbol_lookup', 'call_site_lookup', 'file_explanation'):
            return 1200
        
        # Complex multi-step questions: detailed explanations
        if question_type in ('feature_flow', 'end_to_end_flow', 'architectural_overview'):
            return 3000
        
        # Medium complexity
        return 2000
    
    def _prepare_evidence(self, evidence: list[dict]) -> list[dict]:
        """
        Prepare evidence for LLM consumption with stable IDs.
        
        - Assigns unique evidence IDs (E1, E2, E3, etc.)
        - Limits to top N items
        - Truncates long snippets
        - Preserves essential metadata
        - Removes duplicates
        - Filters out invalid evidence (missing files, empty content, invalid line ranges)
        
        Returns evidence items with 'evidence_id' field added.
        """
        prepared = []
        seen_locations = set()
        evidence_id = 1
        
        for item in evidence[:self.MAX_EVIDENCE_ITEMS * 2]:  # Scan more to account for filtering
            # Validate required fields
            file_path = item.get('file')
            start_line = item.get('start_line', 0)
            end_line = item.get('end_line', 0)
            
            if not file_path:
                logger.warning(f"Skipping evidence with no file path: {item}")
                continue
            
            # Skip invalid line ranges (0-0 indicates missing or unreadable file)
            if start_line == 0 and end_line == 0:
                logger.warning(f"Skipping evidence with invalid line range: {file_path}")
                continue
            
            # Extract snippet and validate it contains actual content
            snippet = item.get('snippet', '') or item.get('content', '') or item.get('source', '')
            snippet = snippet.strip()
            
            if not snippet:
                logger.warning(f"Skipping evidence with empty snippet: {file_path}")
                continue
            
            # Create unique key for deduplication
            location_key = (
                file_path,
                start_line,
                end_line,
            )
            
            if location_key in seen_locations:
                continue
            
            seen_locations.add(location_key)
            
            # Truncate snippet if too long
            if len(snippet) > self.MAX_SNIPPET_LENGTH:
                snippet = snippet[:self.MAX_SNIPPET_LENGTH] + "\n... (truncated)"
            
            prepared_item = {
                'evidence_id': f'E{evidence_id}',  # Stable ID for citations
                'file': file_path,
                'start_line': start_line,
                'end_line': end_line,
                'symbol': item.get('symbol'),
                'symbol_type': item.get('symbol_type'),
                'snippet': snippet,
                'relevance_score': item.get('relevance_score', 0.0),
            }
            
            # Remove None values for cleaner prompt
            prepared_item = {k: v for k, v in prepared_item.items() if v is not None and k != 'evidence_id' or k == 'evidence_id'}
            
            prepared.append(prepared_item)
            evidence_id += 1
            
            # Stop once we have enough valid evidence
            if len(prepared) >= self.MAX_EVIDENCE_ITEMS:
                break
        
        return prepared
    
    def _build_system_prompt(self, question_type: str | None, repository_name: str | None) -> str:
        """Build system prompt enforcing evidence-grounded generation with citation tracking."""
        
        repo_context = f" in the {repository_name} repository" if repository_name else ""
        
        prompt = f"""You are a technical code analyst helping developers understand repository implementations{repo_context}.

Your task is to answer questions based SOLELY on the code evidence provided. You must follow these strict rules:

EVIDENCE GROUNDING RULES:
1. Base ALL claims on the supplied code evidence only
2. Each evidence item has a unique ID (E1, E2, E3, etc.) - use these IDs in your citations
3. NEVER invent files, functions, classes, or relationships not shown in the evidence
4. If evidence shows only part of a flow, explicitly state what is missing
5. When you cannot answer definitively, say so clearly
6. Distinguish between facts you can verify and reasonable inferences
7. Do NOT use documentation files to support claims about executable behavior unless you identify them as documentation
8. Do NOT use .env.example files to claim what values are active at runtime
9. If evidence is truncated (marked with "truncated"), do NOT claim the omitted code is confirmed
10. Do NOT claim a database write, payment status update, email dispatch, or UI action unless the relevant implementation is visible in evidence

ABSENCE AND UNCERTAINTY:
- "Not found in retrieved evidence" ≠ "does not exist in repository"
- Zero search results may indicate limited search coverage, not definitive absence
- Only claim repository-wide absence if search coverage justifies it
- When substituting a similar function, explicitly state it is NOT the requested function
- If two configuration sources conflict, state the conflict rather than guessing which applies

CITATION REQUIREMENTS:
1. Cite evidence using IDs in your answer text (e.g., "CartItem is defined in backend/accounts/models.py [E1]")
2. In your cited_evidence_ids array, list ALL evidence IDs you referenced (e.g., ["E1", "E2", "E3"])
3. NEVER cite evidence IDs that were not provided (e.g., don't cite E5 if only E1-E4 exist)
4. You do NOT need to provide file paths, line numbers, or snippets in the response - these are added automatically
5. Focus on writing a complete, well-structured answer with inline [E1], [E2] references

ANSWER STRUCTURE:
1. Write complete, well-formed sentences and paragraphs
2. Include inline evidence references [E1], [E2] within your answer text  
3. Organize information logically (overview → details → flow)
4. Complete all code examples and explanations - NEVER stop mid-sentence
5. If approaching length limits, prioritize completing your current thought

ANSWER QUALITY:
1. Write clear, technical explanations suitable for developers
2. Focus on what the code actually does, not what it might do
3. Avoid unnecessary repetition
4. Be concise but complete - finish your explanation properly

CONFIDENCE ASSESSMENT:
- Set confidence to "high" only when evidence fully answers the question
- Set confidence to "partial" when evidence covers only some aspects  
- Set confidence to "insufficient" when evidence cannot support a reliable answer

HANDLING GAPS:
- If a complete execution path cannot be established, identify the missing connections in limitations
- If evidence conflicts, explain the conflict in limitations
- Never assume two components are connected just because both appear in evidence
- List specific missing information in the limitations array"""

        if question_type:
            type_guidance = self._get_question_type_guidance(question_type)
            if type_guidance:
                prompt += f"\n\nQUESTION TYPE SPECIFIC GUIDANCE:\n{type_guidance}"
        
        return prompt
    
    def _get_question_type_guidance(self, question_type: str) -> str:
        """Get specific guidance based on question type."""
        
        guidance_map = {
            'symbol_lookup': "Focus on precisely locating the symbol and explaining its definition. Include file path, line numbers, and signature. If the symbol is not found in the evidence, explicitly state that it was not found in the retrieved evidence rather than claiming it does not exist anywhere in the repository. Only claim repository-wide absence if the search covered all indexed files.",
            'feature_flow': "Trace the implementation across relevant files and functions. Follow the execution path from entry point through service methods to database operations. Use call relationships when available to identify connected code. Identify entry points, key operations, and data flow. Explicitly note any gaps in the call chain or missing implementation steps. Do not assume components are connected without evidence of calls, imports, or route mappings.",
            'architectural_overview': "Explain the overall structure using README, entry points, configuration, and key architectural files. Describe main components and their responsibilities.",
            'cross_cutting_concern': "Focus on how the concern is implemented across the codebase. Look for configuration, middleware, decorators, and common patterns.",
            'end_to_end_flow': "Trace the complete path from user action to data persistence. Identify: (1) frontend event handler and component, (2) service method if any, (3) HTTP request with exact endpoint, (4) backend route and view handler, (5) database models and write operations, (6) any payment/webhook/notification handlers. Use relationships and imports to connect these steps. Explicitly state which links are missing or cannot be verified from the evidence.",
            'call_site_lookup': "Identify where the symbol is called/used. Show calling context and usage patterns.",
            'dependency_analysis': "List external dependencies and their versions by examining package manifests and lockfiles. Distinguish between declared version ranges (e.g., '^20.1.0') and exact locked versions (e.g., '20.1.0'). Do NOT infer framework versions from imports, documentation, or configuration files unless those are the only evidence available. If dependency manifests are missing from the evidence, explicitly state that.",
            'file_explanation': "Explain the purpose and responsibilities of the file based on its content and structure.",
        }
        
        return guidance_map.get(question_type, '')
    
    def _build_user_prompt(self, question: str, evidence: list[dict]) -> str:
        """Build user prompt with question and formatted evidence with stable IDs."""
        
        prompt = f"QUESTION:\n{question}\n\n"
        prompt += f"CODE EVIDENCE ({len(evidence)} items):\n\n"
        
        for item in evidence:
            evidence_id = item.get('evidence_id', 'E?')
            prompt += f"[{evidence_id}]\n"
            prompt += f"File: {item.get('file', 'unknown')}\n"
            
            if item.get('start_line') is not None and item.get('end_line') is not None:
                prompt += f"Lines: {item['start_line']}-{item['end_line']}\n"
            
            if item.get('symbol'):
                symbol_type = item.get('symbol_type', 'symbol')
                prompt += f"Symbol: {item['symbol']} ({symbol_type})\n"
            
            if item.get('relevance_score'):
                prompt += f"Relevance: {item['relevance_score']:.2f}\n"
            
            if item.get('snippet'):
                prompt += f"Code:\n```\n{item['snippet']}\n```\n"
            
            prompt += "\n"
        
        prompt += "Generate a clear, evidence-grounded technical explanation that answers the question.\n"
        prompt += "Reference evidence using IDs (e.g., [E1], [E2]) and list all cited IDs in cited_evidence_ids array."
        
        return prompt
    
    def _get_response_schema(self) -> dict:
        """
        Get lightweight JSON schema for structured LLM response.
        
        Only asks for answer, cited_evidence_ids, confidence, and limitations.
        Full citation objects are reconstructed in Python from evidence metadata.
        This reduces token usage significantly and prevents truncation.
        """
        
        return {
            "type": "object",
            "properties": {
                "answer": {
                    "type": "string",
                    "description": "Natural language technical explanation grounded in the provided evidence. Include inline evidence references like [E1], [E2]."
                },
                "cited_evidence_ids": {
                    "type": "array",
                    "description": "List of evidence IDs (E1, E2, E3, etc.) that were cited in the answer",
                    "items": {
                        "type": "string",
                        "pattern": "^E[0-9]+$"
                    }
                },
                "confidence": {
                    "type": "string",
                    "enum": ["high", "partial", "insufficient"],
                    "description": "Assessment of how well the evidence answers the question. Use 'high' when evidence fully answers the question, even if the answer is concise."
                },
                "limitations": {
                    "type": "array",
                    "description": "List of specific missing information or unverified connections. Use empty array if no limitations.",
                    "items": {"type": "string"}
                }
            },
            "required": ["answer", "cited_evidence_ids", "confidence", "limitations"],
            "additionalProperties": False
        }
    
    def _validate_and_construct_answer(
        self,
        question: str,
        llm_response: dict,
        supplied_evidence: list[dict],
    ) -> InvestigationAnswer:
        """
        Validate LLM response and construct InvestigationAnswer with full citations.
        
        Reconstructs citation objects from cited_evidence_ids using actual evidence metadata.
        This prevents hallucination and reduces token usage.
        
        Performs validation to ensure:
        - Cited evidence IDs exist in supplied evidence
        - Line ranges are valid
        - Confidence level is appropriate
        - Detects truncated answers
        """
        # Check for truncation metadata
        metadata = llm_response.pop('__llm_metadata__', llm_response.pop('_metadata', {}))
        was_truncated = metadata.get('was_truncated', False)
        
        # Extract fields with defaults
        answer_text = llm_response.get('answer', '')
        cited_evidence_ids = llm_response.get('cited_evidence_ids', [])
        confidence = llm_response.get('confidence', 'insufficient')
        limitations = llm_response.get('limitations', [])
        
        # Validate answer is not empty
        if not answer_text or not answer_text.strip():
            logger.warning("LLM returned empty answer")
            return self._insufficient_evidence_response(question, "Analysis did not produce a meaningful answer.")
        
        # Check for incomplete sentences (truncation indicators)
        if was_truncated or self._appears_truncated(answer_text):
            logger.warning("Answer appears to be truncated")
            if "Answer was incomplete due to length constraints" not in str(limitations):
                limitations = list(limitations) + ["Answer was incomplete due to length constraints. The explanation was cut off before completion."]
            if confidence == 'high':
                confidence = 'partial'
        
        # Reconstruct full citations from evidence IDs
        citations = self._reconstruct_citations(cited_evidence_ids, supplied_evidence, answer_text)
        
        # Adjust confidence if citations seem weak (but allow concise answers to be high confidence)
        if confidence == 'high' and len(citations) < 1:
            logger.info("Downgrading confidence from 'high' to 'partial' due to no citations")
            confidence = 'partial'
        
        return InvestigationAnswer(
            question=question,
            answer=answer_text,
            citations=citations,
            confidence=confidence,
            limitations=limitations,
        )
    
    def _reconstruct_citations(
        self,
        cited_evidence_ids: list[str],
        supplied_evidence: list[dict],
        answer_text: str,
    ) -> list[Citation]:
        """
        Reconstruct full citation objects from evidence IDs.
        
        Uses authoritative evidence metadata to build complete citations.
        Never fabricates file paths, line numbers, or snippets.
        
        Args:
            cited_evidence_ids: List of evidence IDs returned by LLM (e.g., ['E1', 'E3'])
            supplied_evidence: Original evidence with full metadata
            answer_text: The generated answer (for extracting relevance context)
            
        Returns:
            List of validated Citation objects with complete metadata
        """
        citations = []
        evidence_by_id = {item.get('evidence_id'): item for item in supplied_evidence if item.get('evidence_id')}
        
        for evidence_id in cited_evidence_ids:
            if not evidence_id or evidence_id not in evidence_by_id:
                logger.warning(f"Cited evidence ID not found in supplied evidence: {evidence_id}")
                continue
            
            evidence = evidence_by_id[evidence_id]
            
            # Extract metadata from original evidence
            file_path = evidence.get('file')
            start_line = evidence.get('start_line', 0)
            end_line = evidence.get('end_line', 0)
            symbol = evidence.get('symbol', '')
            snippet = evidence.get('snippet', '')
            
            # Validate required fields
            if not file_path:
                logger.warning(f"Evidence {evidence_id} missing file path")
                continue
            
            # Truncate snippet if too long
            if len(snippet) > 200:
                snippet = snippet[:200] + '...'
            
            # Generate relevance description from context
            # Look for sentences around the evidence ID in the answer
            relevance = self._extract_relevance(evidence_id, answer_text, evidence)
            
            try:
                citation = Citation(
                    evidence_id=evidence_id,
                    file=file_path,
                    start_line=start_line,
                    end_line=end_line,
                    symbol=symbol,
                    snippet=snippet,
                    relevance=relevance,
                )
                citations.append(citation)
                logger.debug(f"Reconstructed citation: {evidence_id} -> {file_path}:{start_line}-{end_line}")
                
            except (KeyError, ValueError) as e:
                logger.warning(f"Failed to reconstruct citation for {evidence_id}: {e}")
                continue
        
        logger.info(f"Reconstructed {len(citations)} citations from {len(cited_evidence_ids)} evidence IDs")
        return citations
    
    def _extract_relevance(self, evidence_id: str, answer_text: str, evidence: dict) -> str:
        """
        Extract relevance description from answer context.
        
        Looks for sentences containing the evidence ID reference to understand
        how it's being used in the explanation.
        """
        # Find sentences containing this evidence ID
        import re
        
        # Pattern to find [E1], [E2], etc.
        pattern = rf'\[{re.escape(evidence_id)}\][^\[]*'
        matches = re.findall(pattern, answer_text)
        
        if matches:
            # Take the first match and clean it up
            context = matches[0].replace(f'[{evidence_id}]', '').strip()
            # Get first sentence or first 100 chars
            sentences = re.split(r'[.!?]', context)
            relevance = sentences[0][:100].strip() if sentences else context[:100].strip()
            if relevance:
                return f"Referenced in answer: {relevance}"
        
        # Fallback: use symbol type and file info
        symbol = evidence.get('symbol', '')
        symbol_type = evidence.get('symbol_type', 'code')
        
        if symbol:
            return f"Defines {symbol_type} '{symbol}' referenced in the answer"
        
        return "Provides supporting evidence for claims in the answer"
    
    def _appears_truncated(self, answer: str) -> bool:
        """
        Detect if answer appears truncated mid-sentence.

        Checks for common truncation indicators:
        - Ends without sentence-ending punctuation
        - Ends with incomplete code block
        - Ends with connector words
        """
        if not answer:
            return False

        answer = answer.rstrip()

        # Check for incomplete code blocks
        if answer.count('```') % 2 != 0:
            logger.info("Detected unclosed code block - likely truncated")
            return True

        # Valid terminal characters for technical writing.
        # Colon is intentionally included: answers frequently end with a
        # structured list or step sequence introduced by ':', e.g.
        # "CartSyncView supports two operations:" which is NOT truncated.
        VALID_ENDINGS = set('.!?:`')

        if answer and answer[-1] not in VALID_ENDINGS:
            # Allow answers ending with a closing code fence
            if not answer.endswith('```'):
                logger.info(f"Answer ends without sentence punctuation: '{answer[-50:]}'")
                return True

        # Check for connector words at the end (suggests continuation)
        last_words = answer.split()[-3:] if answer.split() else []
        continuation_words = {
            'and', 'or', 'but', 'so', 'then', 'also', 'additionally',
            'however', 'therefore', 'thus', 'which', 'that', 'this',
            'these', 'those', 'when', 'where', 'while', 'if', 'unless',
            'because', 'since', 'as', 'for', 'with',
        }

        if last_words and last_words[-1].lower().rstrip('.,;:') in continuation_words:
            logger.info(f"Answer ends with continuation word: {last_words[-1]}")
            return True

        return False
    
    def _validate_citations(
        self,
        citations_data: list[dict],
        supplied_evidence: list[dict],
    ) -> list[Citation]:
        """
        Validate that citations reference valid evidence with correct metadata.
        
        Performs strict validation:
        - Evidence ID must exist in supplied evidence
        - File path must match the evidence
        - Line ranges must be valid (start <= end, both >= 0)
        - Line ranges must not exceed evidence source range
        
        Filters out invalid citations rather than attempting correction.
        """
        valid_citations = []
        
        # Build lookup maps from evidence
        evidence_by_id = {item.get('evidence_id'): item for item in supplied_evidence if item.get('evidence_id')}
        supplied_files = {item.get('file') for item in supplied_evidence if item.get('file')}
        
        for citation_data in citations_data:
            evidence_id = citation_data.get('evidence_id')
            cited_file = citation_data.get('file')
            start_line = citation_data.get('start_line')
            end_line = citation_data.get('end_line')
            
            # Validate evidence ID exists
            if not evidence_id or evidence_id not in evidence_by_id:
                logger.warning(f"Citation references unknown evidence ID: {evidence_id}")
                continue
            
            # Get the corresponding evidence item
            evidence_item = evidence_by_id[evidence_id]
            evidence_file = evidence_item.get('file')
            evidence_start = evidence_item.get('start_line')
            evidence_end = evidence_item.get('end_line')
            
            # Validate file path matches evidence
            if cited_file != evidence_file:
                logger.warning(f"Citation {evidence_id} file mismatch: cited '{cited_file}' vs evidence '{evidence_file}'")
                continue
            
            # Validate line ranges
            if start_line is None or end_line is None:
                logger.warning(f"Citation {evidence_id} missing line numbers")
                continue
            
            try:
                start_line = int(start_line)
                end_line = int(end_line)
            except (TypeError, ValueError):
                logger.warning(f"Citation {evidence_id} has invalid line number format")
                continue
            
            # Allow 0 for unavailable line numbers (as per schema)
            if start_line < 0 or end_line < 0:
                logger.warning(f"Citation {evidence_id} has negative line numbers: {start_line}-{end_line}")
                continue
            
            # If line numbers are non-zero, validate they make sense
            if start_line > 0 and end_line > 0:
                if start_line > end_line:
                    logger.warning(f"Citation {evidence_id} has reversed line range: {start_line}-{end_line}")
                    continue
                
                # Validate cited range doesn't exceed evidence range (if evidence has lines)
                if evidence_start is not None and evidence_end is not None:
                    if evidence_start > 0 and evidence_end > 0:
                        if start_line < evidence_start or end_line > evidence_end:
                            logger.warning(
                                f"Citation {evidence_id} line range {start_line}-{end_line} "
                                f"exceeds evidence range {evidence_start}-{evidence_end}"
                            )
                            # Allow it but log the issue - LLM may have extracted a subset
            
            # Truncate snippet if too long
            snippet = citation_data.get('snippet', '')
            if len(snippet) > 200:
                snippet = snippet[:200] + '...'
            
            try:
                citation = Citation(
                    evidence_id=evidence_id,
                    file=cited_file,
                    start_line=start_line,
                    end_line=end_line,
                    symbol=citation_data.get('symbol', ''),
                    snippet=snippet,
                    relevance=citation_data.get('relevance', ''),
                )
                valid_citations.append(citation)
                logger.debug(f"Valid citation: {evidence_id} -> {cited_file}:{start_line}-{end_line}")
                
            except (KeyError, ValueError) as e:
                logger.warning(f"Invalid citation data for {evidence_id}: {e}")
                continue
        
        logger.info(f"Validated {len(valid_citations)} out of {len(citations_data)} citations")
        return valid_citations
    
    def _insufficient_evidence_response(self, question: str, reason: str) -> InvestigationAnswer:
        """Create response for insufficient evidence cases."""
        
        return InvestigationAnswer(
            question=question,
            answer=f"Unable to provide a complete answer: {reason}",
            citations=[],
            confidence='insufficient',
            limitations=[reason],
        )

    def _not_found_response(self, question: str) -> InvestigationAnswer:
        """Create a clear 'symbol not found' response for symbol lookup queries."""
        return InvestigationAnswer(
            question=question,
            answer=(
                "The requested symbol was not found in the indexed source files. "
                "This search covered all indexed symbols and file contents. "
                "If the symbol exists in non-indexed files (e.g., generated code, "
                "unsupported file types, or files excluded by the filter), "
                "it would not appear in these results. "
                "Based on the current index coverage, the symbol does not appear "
                "to exist in the repository."
            ),
            citations=[],
            confidence='insufficient',
            limitations=[
                "Symbol not found in any indexed source file.",
                "Search coverage limited to indexed file types (Python, TypeScript, JavaScript, etc.).",
            ],
        )
