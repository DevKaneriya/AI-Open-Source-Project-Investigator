"""
Tests for citation validation and answer generation fixes.

Tests cover:
1. Evidence ID validation
2. Line range validation  
3. Truncation detection
4. Citation schema compliance
"""

import pytest
from unittest.mock import Mock, patch

from backend.app.services.answer_generation_service import AnswerGenerationService
from backend.app.models.investigation_answer import InvestigationAnswer, Citation


class TestCitationValidation:
    """Test citation validation logic."""
    
    @pytest.fixture
    def llm_service(self):
        """Mock LLM service."""
        mock = Mock()
        return mock
    
    @pytest.fixture
    def service(self, llm_service):
        """Answer generation service with mocked LLM."""
        return AnswerGenerationService(llm_service)
    
    @pytest.fixture
    def sample_evidence(self):
        """Sample evidence with IDs."""
        return [
            {
                'evidence_id': 'E1',
                'file': 'backend/accounts/models.py',
                'start_line': 7,
                'end_line': 19,
                'symbol': 'CartItem',
                'symbol_type': 'class',
                'snippet': 'class CartItem(models.Model):\n    user = ...',
            },
            {
                'evidence_id': 'E2',
                'file': 'backend/accounts/admin.py',
                'start_line': 5,
                'end_line': 8,
                'symbol': 'CartItemAdmin',
                'symbol_type': 'class',
                'snippet': 'class CartItemAdmin(admin.ModelAdmin):\n    list_display = ...',
            },
        ]
    
    def test_valid_citations_with_evidence_ids(self, service, llm_service, sample_evidence):
        """Test that valid citations with evidence IDs are accepted."""
        llm_service.generate_structured.return_value = {
            'answer': 'CartItem is defined in backend/accounts/models.py [E1] and managed via CartItemAdmin [E2].',
            'citations': [
                {
                    'evidence_id': 'E1',
                    'file': 'backend/accounts/models.py',
                    'start_line': 7,
                    'end_line': 19,
                    'symbol': 'CartItem',
                    'snippet': 'class CartItem(models.Model):',
                    'relevance': 'Defines the CartItem model'
                },
                {
                    'evidence_id': 'E2',
                    'file': 'backend/accounts/admin.py',
                    'start_line': 5,
                    'end_line': 8,
                    'symbol': 'CartItemAdmin',
                    'snippet': 'class CartItemAdmin(admin.ModelAdmin):',
                    'relevance': 'Defines admin interface for CartItem'
                }
            ],
            'confidence': 'high',
            'limitations': [],
            '_metadata': {'finish_reason': 'stop', 'was_truncated': False}
        }
        
        result = service.generate_answer(
            question="Where is CartItem defined?",
            evidence=sample_evidence,
            question_type="symbol_lookup"
        )
        
        assert isinstance(result, InvestigationAnswer)
        assert len(result.citations) == 2
        assert result.citations[0].evidence_id == 'E1'
        assert result.citations[1].evidence_id == 'E2'
        assert result.confidence == 'high'
    
    def test_reject_unknown_evidence_id(self, service, llm_service, sample_evidence):
        """Test that citations with unknown evidence IDs are rejected."""
        llm_service.generate_structured.return_value = {
            'answer': 'CartItem is defined [E1] and used in views [E99].',
            'citations': [
                {
                    'evidence_id': 'E1',
                    'file': 'backend/accounts/models.py',
                    'start_line': 7,
                    'end_line': 19,
                    'symbol': 'CartItem',
                    'snippet': 'class CartItem(models.Model):',
                    'relevance': 'Defines CartItem'
                },
                {
                    'evidence_id': 'E99',  # Unknown ID
                    'file': 'backend/accounts/views.py',
                    'start_line': 100,
                    'end_line': 120,
                    'symbol': 'cart_view',
                    'snippet': 'def cart_view():',
                    'relevance': 'Uses CartItem'
                }
            ],
            'confidence': 'high',
            'limitations': [],
            '_metadata': {'finish_reason': 'stop', 'was_truncated': False}
        }
        
        result = service.generate_answer(
            question="Where is CartItem used?",
            evidence=sample_evidence,
            question_type="symbol_lookup"
        )
        
        # Only E1 should be accepted, E99 rejected
        assert len(result.citations) == 1
        assert result.citations[0].evidence_id == 'E1'
        # Confidence downgraded due to insufficient citations
        assert result.confidence == 'partial'
    
    def test_reject_reversed_line_ranges(self, service, llm_service, sample_evidence):
        """Test that reversed line ranges are rejected."""
        llm_service.generate_structured.return_value = {
            'answer': 'CartItem is defined [E1].',
            'citations': [
                {
                    'evidence_id': 'E1',
                    'file': 'backend/accounts/models.py',
                    'start_line': 19,  # Reversed!
                    'end_line': 7,
                    'symbol': 'CartItem',
                    'snippet': 'class CartItem(models.Model):',
                    'relevance': 'Defines CartItem'
                }
            ],
            'confidence': 'high',
            'limitations': [],
            '_metadata': {'finish_reason': 'stop', 'was_truncated': False}
        }
        
        result = service.generate_answer(
            question="Where is CartItem defined?",
            evidence=sample_evidence,
            question_type="symbol_lookup"
        )
        
        # Citation should be rejected
        assert len(result.citations) == 0
        # Confidence downgraded since no valid citations (originally 'high')
        assert result.confidence in ['partial', 'insufficient']
    
    def test_reject_negative_line_numbers(self, service, llm_service, sample_evidence):
        """Test that negative line numbers are rejected."""
        llm_service.generate_structured.return_value = {
            'answer': 'CartItem is defined [E1].',
            'citations': [
                {
                    'evidence_id': 'E1',
                    'file': 'backend/accounts/models.py',
                    'start_line': -5,  # Negative!
                    'end_line': 19,
                    'symbol': 'CartItem',
                    'snippet': 'class CartItem(models.Model):',
                    'relevance': 'Defines CartItem'
                }
            ],
            'confidence': 'high',
            'limitations': [],
            '_metadata': {'finish_reason': 'stop', 'was_truncated': False}
        }
        
        result = service.generate_answer(
            question="Where is CartItem defined?",
            evidence=sample_evidence,
            question_type="symbol_lookup"
        )
        
        assert len(result.citations) == 0
        assert result.confidence in ['partial', 'insufficient']
    
    def test_reject_mismatched_file_path(self, service, llm_service, sample_evidence):
        """Test that citations with mismatched file paths are rejected."""
        llm_service.generate_structured.return_value = {
            'answer': 'CartItem is defined [E1].',
            'citations': [
                {
                    'evidence_id': 'E1',
                    'file': 'backend/accounts/WRONG.py',  # File mismatch
                    'start_line': 7,
                    'end_line': 19,
                    'symbol': 'CartItem',
                    'snippet': 'class CartItem(models.Model):',
                    'relevance': 'Defines CartItem'
                }
            ],
            'confidence': 'high',
            'limitations': [],
            '_metadata': {'finish_reason': 'stop', 'was_truncated': False}
        }
        
        result = service.generate_answer(
            question="Where is CartItem defined?",
            evidence=sample_evidence,
            question_type="symbol_lookup"
        )
        
        assert len(result.citations) == 0
        assert result.confidence in ['partial', 'insufficient']
    
    def test_allow_zero_line_numbers_for_unavailable(self, service, llm_service, sample_evidence):
        """Test that 0 line numbers are allowed when lines are unavailable."""
        llm_service.generate_structured.return_value = {
            'answer': 'CartItem is defined [E1].',
            'citations': [
                {
                    'evidence_id': 'E1',
                    'file': 'backend/accounts/models.py',
                    'start_line': 0,  # Allowed: not available
                    'end_line': 0,
                    'symbol': 'CartItem',
                    'snippet': 'class CartItem(models.Model):',
                    'relevance': 'Defines CartItem'
                },
                {
                    'evidence_id': 'E2',
                    'file': 'backend/accounts/admin.py',
                    'start_line': 5,
                    'end_line': 8,
                    'symbol': 'CartItemAdmin',
                    'snippet': 'class CartItemAdmin(admin.ModelAdmin):',
                    'relevance': 'Admin interface'
                }
            ],
            'confidence': 'high',
            'limitations': [],
            '_metadata': {'finish_reason': 'stop', 'was_truncated': False}
        }
        
        result = service.generate_answer(
            question="Where is CartItem defined?",
            evidence=sample_evidence,
            question_type="symbol_lookup"
        )
        
        assert len(result.citations) == 2
        assert result.citations[0].start_line == 0
        assert result.citations[0].end_line == 0


class TestTruncationDetection:
    """Test answer truncation detection."""
    
    @pytest.fixture
    def llm_service(self):
        """Mock LLM service."""
        return Mock()
    
    @pytest.fixture
    def service(self, llm_service):
        """Answer generation service."""
        return AnswerGenerationService(llm_service)
    
    @pytest.fixture
    def sample_evidence(self):
        """Sample evidence."""
        return [
            {
                'evidence_id': 'E1',
                'file': 'test.py',
                'start_line': 1,
                'end_line': 10,
                'symbol': 'test_func',
                'snippet': 'def test_func():\n    pass'
            }
        ]
    
    def test_detect_truncation_from_finish_reason(self, service, llm_service, sample_evidence):
        """Test truncation detection from LLM finish_reason."""
        llm_service.generate_structured.return_value = {
            'answer': 'This is a partial answer that was cut off',
            'citations': [],
            'confidence': 'high',
            'limitations': [],
            '_metadata': {'finish_reason': 'length', 'was_truncated': True}  # Truncated!
        }
        
        result = service.generate_answer(
            question="Test question",
            evidence=sample_evidence
        )
        
        assert 'incomplete due to length constraints' in ' '.join(result.limitations).lower()
        assert result.confidence != 'high'  # Downgraded
    
    def test_detect_incomplete_sentence(self, service, llm_service, sample_evidence):
        """Test detection of answers ending mid-sentence."""
        llm_service.generate_structured.return_value = {
            'answer': 'The function is defined in test.py and it does',  # No ending punctuation
            'citations': [],
            'confidence': 'high',
            'limitations': [],
            '_metadata': {'finish_reason': 'stop', 'was_truncated': False}
        }
        
        result = service.generate_answer(
            question="Test question",
            evidence=sample_evidence
        )
        
        # Should detect truncation from missing punctuation
        assert 'incomplete' in ' '.join(result.limitations).lower() or result.confidence == 'partial'
    
    def test_detect_unclosed_code_block(self, service, llm_service, sample_evidence):
        """Test detection of unclosed code blocks."""
        llm_service.generate_structured.return_value = {
            'answer': 'The function is:\n```python\ndef test():\n    pass',  # Missing closing ```
            'citations': [],
            'confidence': 'high',
            'limitations': [],
            '_metadata': {'finish_reason': 'stop', 'was_truncated': False}
        }
        
        result = service.generate_answer(
            question="Test question",
            evidence=sample_evidence
        )
        
        assert 'incomplete' in ' '.join(result.limitations).lower()
    
    def test_detect_continuation_words(self, service, llm_service, sample_evidence):
        """Test detection of answers ending with continuation words."""
        llm_service.generate_structured.return_value = {
            'answer': 'The function does several things, and',  # Ends with 'and'
            'citations': [],
            'confidence': 'high',
            'limitations': [],
            '_metadata': {'finish_reason': 'stop', 'was_truncated': False}
        }
        
        result = service.generate_answer(
            question="Test question",
            evidence=sample_evidence
        )
        
        assert 'incomplete' in ' '.join(result.limitations).lower()
    
    def test_complete_answer_not_flagged(self, service, llm_service, sample_evidence):
        """Test that complete answers are not flagged as truncated."""
        llm_service.generate_structured.return_value = {
            'answer': 'The function is defined in test.py at lines 1-10. It performs basic operations.',
            'citations': [
                {
                    'evidence_id': 'E1',
                    'file': 'test.py',
                    'start_line': 1,
                    'end_line': 10,
                    'symbol': 'test_func',
                    'snippet': 'def test_func():',
                    'relevance': 'Function definition'
                }
            ],
            'confidence': 'high',
            'limitations': [],
            '_metadata': {'finish_reason': 'stop', 'was_truncated': False}
        }
        
        result = service.generate_answer(
            question="Test question",
            evidence=sample_evidence
        )
        
        assert 'incomplete' not in ' '.join(result.limitations).lower()
        # Confidence should remain high with valid complete answer and citations
        assert result.confidence in ['high', 'partial']  # Allow partial if only 1 citation


class TestEvidencePrepration:
    """Test evidence preparation with stable IDs."""
    
    @pytest.fixture
    def llm_service(self):
        """Mock LLM service."""
        return Mock()
    
    @pytest.fixture
    def service(self, llm_service):
        """Answer generation service."""
        return AnswerGenerationService(llm_service)
    
    def test_evidence_gets_stable_ids(self, service):
        """Test that evidence items get stable E1, E2, E3 IDs."""
        evidence = [
            {'file': 'a.py', 'start_line': 1, 'end_line': 10, 'snippet': 'code1'},
            {'file': 'b.py', 'start_line': 20, 'end_line': 30, 'snippet': 'code2'},
            {'file': 'c.py', 'start_line': 40, 'end_line': 50, 'snippet': 'code3'},
        ]
        
        prepared = service._prepare_evidence(evidence)
        
        assert len(prepared) == 3
        assert prepared[0]['evidence_id'] == 'E1'
        assert prepared[1]['evidence_id'] == 'E2'
        assert prepared[2]['evidence_id'] == 'E3'
    
    def test_evidence_deduplication(self, service):
        """Test that duplicate evidence is removed."""
        evidence = [
            {'file': 'a.py', 'start_line': 1, 'end_line': 10, 'snippet': 'code1'},
            {'file': 'a.py', 'start_line': 1, 'end_line': 10, 'snippet': 'code1'},  # Duplicate
            {'file': 'b.py', 'start_line': 20, 'end_line': 30, 'snippet': 'code2'},
        ]
        
        prepared = service._prepare_evidence(evidence)
        
        assert len(prepared) == 2
        assert prepared[0]['evidence_id'] == 'E1'
        assert prepared[1]['evidence_id'] == 'E2'
    
    def test_long_snippets_truncated(self, service):
        """Test that long code snippets are truncated."""
        long_code = 'x = 1\n' * 100  # Very long snippet
        evidence = [
            {'file': 'a.py', 'start_line': 1, 'end_line': 100, 'snippet': long_code},
        ]
        
        prepared = service._prepare_evidence(evidence)
        
        assert len(prepared[0]['snippet']) <= service.MAX_SNIPPET_LENGTH + 20  # Allow for truncation marker
        assert '(truncated)' in prepared[0]['snippet']


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
