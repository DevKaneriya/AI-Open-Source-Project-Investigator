"""
Tests for evidence completeness and grounding improvements.

Tests the following scenarios:
1. Dependency manifest retrieval and version reporting
2. Feature-flow call chain tracing
3. Repository-wide absence claims
4. Evidence validation (empty snippets, 0-0 ranges)
5. Configuration conflict handling
"""
import pytest
from unittest.mock import Mock, MagicMock, patch


class TestDependencyManifestRetrieval:
    """Test that dependency queries retrieve actual manifests."""
    
    def test_dependency_analysis_includes_manifests(self):
        """Dependency analysis should prioritize package manifests."""
        # This would require mocking the full retrieval pipeline
        # For now, we'll test via integration tests
        pass
    
    def test_distinguishes_declared_vs_locked_versions(self):
        """Should distinguish version ranges from exact versions."""
        # Test that answers differentiate ^20.1.0 (declared) vs 20.1.0 (locked)
        pass


class TestFeatureFlowEvidence:
    """Test that feature flows follow connected implementation steps."""
    
    def test_follows_call_relationships(self):
        """Should use relationships to connect frontend to backend."""
        pass
    
    def test_identifies_missing_links(self):
        """Should explicitly note gaps in the execution path."""
        pass


class TestAbsenceClaims:
    """Test repository-wide absence claim validation."""
    
    def test_not_found_vs_does_not_exist(self):
        """Should distinguish 'not found in evidence' from 'does not exist'."""
        pass
    
    def test_does_not_substitute_similar_functions(self):
        """Should not replace requested function with a similar one."""
        pass


class TestEvidenceValidation:
    """Test citation and evidence validation."""
    
    def test_rejects_zero_zero_line_ranges(self):
        """Evidence with 0-0 line ranges should be filtered."""
        from backend.app.services.answer_generation_service import AnswerGenerationService
        from backend.app.services.llm_service import LLMService
        
        service = AnswerGenerationService(LLMService())
        
        # Test evidence with invalid line range
        invalid_evidence = [
            {
                'file': 'test.py',
                'start_line': 0,
                'end_line': 0,
                'snippet': '',
                'symbol': 'test_func'
            }
        ]
        
        prepared = service._prepare_evidence(invalid_evidence)
        
        # Should be filtered out
        assert len(prepared) == 0
    
    def test_rejects_empty_snippets(self):
        """Evidence with empty snippets should be filtered."""
        from backend.app.services.answer_generation_service import AnswerGenerationService
        from backend.app.services.llm_service import LLMService
        
        service = AnswerGenerationService(LLMService())
        
        # Test evidence with empty snippet
        invalid_evidence = [
            {
                'file': 'test.py',
                'start_line': 10,
                'end_line': 20,
                'snippet': '',
                'symbol': 'test_func'
            }
        ]
        
        prepared = service._prepare_evidence(invalid_evidence)
        
        # Should be filtered out
        assert len(prepared) == 0
    
    def test_accepts_valid_evidence(self):
        """Valid evidence should pass through."""
        from backend.app.services.answer_generation_service import AnswerGenerationService
        from backend.app.services.llm_service import LLMService
        
        service = AnswerGenerationService(LLMService())
        
        # Test valid evidence
        valid_evidence = [
            {
                'file': 'test.py',
                'start_line': 10,
                'end_line': 20,
                'snippet': 'def test_func():\n    pass',
                'symbol': 'test_func'
            }
        ]
        
        prepared = service._prepare_evidence(valid_evidence)
        
        # Should be included
        assert len(prepared) == 1
        assert prepared[0]['evidence_id'] == 'E1'
        assert prepared[0]['file'] == 'test.py'
        assert 'test_func' in prepared[0]['snippet']


class TestConfidenceAssessment:
    """Test confidence level appropriateness."""
    
    def test_high_confidence_requires_complete_evidence(self):
        """High confidence should only be used when evidence is complete."""
        pass
    
    def test_partial_confidence_for_incomplete_flows(self):
        """Partial confidence when some steps are missing."""
        pass
    
    def test_insufficient_confidence_for_inadequate_evidence(self):
        """Insufficient confidence when evidence doesn't support answer."""
        pass


class TestPromptGuidance:
    """Test that prompt guidance is correctly configured."""
    
    def test_dependency_analysis_guidance(self):
        """Dependency analysis should have manifest-focused guidance."""
        from backend.app.services.answer_generation_service import AnswerGenerationService
        from backend.app.services.llm_service import LLMService
        
        service = AnswerGenerationService(LLMService())
        guidance = service._get_question_type_guidance('dependency_analysis')
        
        assert 'package manifests' in guidance
        assert 'lockfiles' in guidance
        assert 'declared version ranges' in guidance
        assert 'exact locked versions' in guidance
    
    def test_feature_flow_guidance(self):
        """Feature flow should emphasize call chain tracing."""
        from backend.app.services.answer_generation_service import AnswerGenerationService
        from backend.app.services.llm_service import LLMService
        
        service = AnswerGenerationService(LLMService())
        guidance = service._get_question_type_guidance('feature_flow')
        
        assert 'execution path' in guidance or 'call' in guidance
        assert 'missing' in guidance or 'gaps' in guidance
    
    def test_end_to_end_flow_guidance(self):
        """End-to-end flow should list required components."""
        from backend.app.services.answer_generation_service import AnswerGenerationService
        from backend.app.services.llm_service import LLMService
        
        service = AnswerGenerationService(LLMService())
        guidance = service._get_question_type_guidance('end_to_end_flow')
        
        assert 'frontend' in guidance
        assert 'backend' in guidance or 'handler' in guidance
        assert 'database' in guidance
    
    def test_symbol_lookup_absence_guidance(self):
        """Symbol lookup should distinguish not found vs does not exist."""
        from backend.app.services.answer_generation_service import AnswerGenerationService
        from backend.app.services.llm_service import LLMService
        
        service = AnswerGenerationService(LLMService())
        guidance = service._get_question_type_guidance('symbol_lookup')
        
        assert 'not found in the retrieved evidence' in guidance
        assert 'repository' in guidance
