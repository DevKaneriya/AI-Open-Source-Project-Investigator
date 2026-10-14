"""
Tests for LLM Service structured output generation.

Tests different model capabilities and error scenarios.
"""
import pytest
from unittest.mock import Mock, patch, MagicMock
from backend.app.services.llm_service import LLMService


@pytest.fixture
def mock_groq_client():
    """Create a mock Groq client."""
    with patch('backend.app.services.llm_service.Groq') as mock:
        client = MagicMock()
        mock.return_value = client
        yield client


@pytest.fixture
def mock_settings():
    """Mock settings with test configuration."""
    with patch('backend.app.services.llm_service.settings') as mock:
        mock.groq_api_key = 'test-api-key'
        mock.query_analyzer_model = 'qwen/qwen3.8-27b'
        yield mock


class TestLLMServiceInitialization:
    """Test LLM service initialization with different models."""
    
    def test_init_with_qwen_model(self, mock_groq_client, mock_settings):
        """Qwen model should use json_schema output mode."""
        mock_settings.query_analyzer_model = 'qwen/qwen3.8-27b'
        
        service = LLMService()
        
        assert service.model == 'qwen/qwen3.8-27b'
        assert service.output_mode == 'json_schema'
    
    def test_init_with_gpt_oss_20b(self, mock_groq_client, mock_settings):
        """GPT-OSS-20B should use json_object output mode."""
        mock_settings.query_analyzer_model = 'openai/gpt-oss-20b'
        
        service = LLMService()
        
        assert service.model == 'openai/gpt-oss-20b'
        assert service.output_mode == 'json_object'
    
    def test_init_with_gpt_oss_120b(self, mock_groq_client, mock_settings):
        """GPT-OSS-120B should use prompt_only output mode."""
        mock_settings.query_analyzer_model = 'openai/gpt-oss-120b'
        
        service = LLMService()
        
        assert service.model == 'openai/gpt-oss-120b'
        assert service.output_mode == 'prompt_only'
    
    def test_init_missing_api_key(self, mock_settings):
        """Should raise error when API key is missing."""
        mock_settings.groq_api_key = None
        
        with pytest.raises(RuntimeError, match="GROQ_API_KEY is not configured"):
            LLMService()


class TestStructuredOutputGeneration:
    """Test structured output generation for different models."""
    
    @pytest.fixture
    def test_schema(self):
        """Test JSON schema."""
        return {
            'type': 'object',
            'properties': {
                'intent': {'type': 'string'},
                'question_type': {'type': 'string'}
            },
            'required': ['intent', 'question_type']
        }
    
    def test_qwen_model_uses_json_schema(self, mock_groq_client, mock_settings, test_schema):
        """Qwen model should use json_schema response format."""
        mock_settings.query_analyzer_model = 'qwen/qwen3.8-27b'
        
        # Mock successful response
        mock_response = Mock()
        mock_response.choices = [Mock(
            message=Mock(content='{"intent": "test", "question_type": "symbol_lookup"}'),
            finish_reason='stop'
        )]
        mock_groq_client.chat.completions.create.return_value = mock_response
        
        service = LLMService()
        result = service.generate_structured(
            system_prompt="Test system",
            user_prompt="Test query",
            response_schema=test_schema,
            max_tokens=512
        )
        
        # Verify json_schema was used
        call_args = mock_groq_client.chat.completions.create.call_args
        assert call_args[1]['response_format']['type'] == 'json_schema'
        assert call_args[1]['response_format']['json_schema']['schema'] == test_schema
        assert call_args[1]['response_format']['json_schema']['strict'] == False
        
        # Verify result
        assert result['intent'] == 'test'
        assert result['question_type'] == 'symbol_lookup'
        assert result['__llm_metadata__']['output_mode'] == 'json_schema'
    
    def test_gpt_oss_20b_uses_json_object(self, mock_groq_client, mock_settings, test_schema):
        """GPT-OSS-20B should use json_object response format."""
        mock_settings.query_analyzer_model = 'openai/gpt-oss-20b'
        
        # Mock successful response
        mock_response = Mock()
        mock_response.choices = [Mock(
            message=Mock(content='{"intent": "test", "question_type": "feature_flow"}'),
            finish_reason='stop'
        )]
        mock_groq_client.chat.completions.create.return_value = mock_response
        
        service = LLMService()
        result = service.generate_structured(
            system_prompt="Test system",
            user_prompt="Test query",
            response_schema=test_schema,
            max_tokens=512
        )
        
        # Verify json_object was used
        call_args = mock_groq_client.chat.completions.create.call_args
        assert call_args[1]['response_format']['type'] == 'json_object'
        assert 'json_schema' not in call_args[1]['response_format']
        
        # Verify result
        assert result['intent'] == 'test'
        assert result['__llm_metadata__']['output_mode'] == 'json_object'
    
    def test_gpt_oss_120b_uses_prompt_only(self, mock_groq_client, mock_settings, test_schema):
        """GPT-OSS-120B should inject schema into prompt and not use response_format."""
        mock_settings.query_analyzer_model = 'openai/gpt-oss-120b'
        
        # Mock successful response
        mock_response = Mock()
        mock_response.choices = [Mock(
            message=Mock(content='{"intent": "test", "question_type": "architectural_overview"}'),
            finish_reason='stop'
        )]
        mock_groq_client.chat.completions.create.return_value = mock_response
        
        service = LLMService()
        result = service.generate_structured(
            system_prompt="Test system",
            user_prompt="Test query",
            response_schema=test_schema,
            max_tokens=512
        )
        
        # Verify no response_format was used
        call_args = mock_groq_client.chat.completions.create.call_args
        assert 'response_format' not in call_args[1]
        
        # Verify schema was injected into system prompt
        system_message = call_args[1]['messages'][0]
        assert 'You MUST return ONLY valid JSON' in system_message['content']
        assert 'Required fields: intent, question_type' in system_message['content']
        
        # Verify result
        assert result['intent'] == 'test'
        assert result['__llm_metadata__']['output_mode'] == 'prompt_only'


class TestErrorHandling:
    """Test error handling scenarios."""
    
    @pytest.fixture
    def test_schema(self):
        return {
            'type': 'object',
            'properties': {'test': {'type': 'string'}},
            'required': ['test']
        }
    
    def test_json_validate_failed_error(self, mock_groq_client, mock_settings, test_schema):
        """Should enhance json_validate_failed errors with diagnostics."""
        mock_groq_client.chat.completions.create.side_effect = Exception(
            "Error code: 400 - json_validate_failed: max completion tokens reached"
        )
        
        service = LLMService()
        
        with pytest.raises(RuntimeError) as exc:
            service.generate_structured("sys", "user", test_schema, 512)
        
        error_msg = str(exc.value)
        assert "json_validate_failed" in error_msg
        assert "may not support structured output" in error_msg
        assert "qwen/qwen3.8-27b" in error_msg
    
    def test_empty_response(self, mock_groq_client, mock_settings, test_schema):
        """Should raise error for empty response."""
        mock_response = Mock()
        mock_response.choices = [Mock(message=Mock(content=''), finish_reason='stop')]
        mock_groq_client.chat.completions.create.return_value = mock_response
        
        service = LLMService()
        
        with pytest.raises(RuntimeError, match="empty response"):
            service.generate_structured("sys", "user", test_schema, 512)
    
    def test_invalid_json_response(self, mock_groq_client, mock_settings, test_schema):
        """Should raise error for invalid JSON."""
        mock_response = Mock()
        mock_response.choices = [Mock(
            message=Mock(content='not valid json {'),
            finish_reason='stop'
        )]
        mock_groq_client.chat.completions.create.return_value = mock_response
        
        service = LLMService()
        
        with pytest.raises(RuntimeError, match="Failed to parse LLM response as JSON"):
            service.generate_structured("sys", "user", test_schema, 512)
    
    def test_missing_required_fields_prompt_only_mode(self, mock_groq_client, mock_settings, test_schema):
        """Should validate required fields in prompt_only mode."""
        mock_settings.query_analyzer_model = 'openai/gpt-oss-120b'
        
        mock_response = Mock()
        mock_response.choices = [Mock(
            message=Mock(content='{"wrong_field": "value"}'),
            finish_reason='stop'
        )]
        mock_groq_client.chat.completions.create.return_value = mock_response
        
        service = LLMService()
        
        with pytest.raises(RuntimeError, match="Missing required fields: test"):
            service.generate_structured("sys", "user", test_schema, 512)
    
    def test_truncated_response_metadata(self, mock_groq_client, mock_settings, test_schema):
        """Should mark truncated responses in metadata."""
        mock_response = Mock()
        mock_response.choices = [Mock(
            message=Mock(content='{"test": "value"}'),
            finish_reason='length'
        )]
        mock_groq_client.chat.completions.create.return_value = mock_response
        
        service = LLMService()
        result = service.generate_structured("sys", "user", test_schema, 512)
        
        assert result['__llm_metadata__']['was_truncated'] == True
        assert result['__llm_metadata__']['finish_reason'] == 'length'


class TestValidationHelpers:
    """Test validation helper methods."""
    
    def test_validate_response_schema_valid(self, mock_groq_client, mock_settings):
        """Should pass validation for valid response."""
        service = LLMService()
        
        schema = {
            'type': 'object',
            'required': ['field1', 'field2']
        }
        response = {'field1': 'value1', 'field2': 'value2'}
        
        # Should not raise
        service._validate_response_schema(response, schema)
    
    def test_validate_response_schema_missing_fields(self, mock_groq_client, mock_settings):
        """Should fail validation for missing required fields."""
        service = LLMService()
        
        schema = {
            'type': 'object',
            'required': ['field1', 'field2', 'field3']
        }
        response = {'field1': 'value1'}
        
        with pytest.raises(ValueError, match="Missing required fields: field2, field3"):
            service._validate_response_schema(response, schema)
    
    def test_format_schema_for_prompt(self, mock_groq_client, mock_settings):
        """Should format schema as compact representation."""
        service = LLMService()
        
        schema = {
            'type': 'object',
            'properties': {
                'test': {'type': 'string'},
                'items': {'type': 'array', 'items': {'type': 'string'}},
                'count': {'type': 'number'}
            },
            'required': ['test', 'items']
        }
        formatted = service._format_schema_for_prompt(schema)
        
        # Should contain field definitions
        assert '"test": string' in formatted
        assert '"items": [string]' in formatted
        assert '"count": number' in formatted
        # Should contain required fields list
        assert 'Required fields: test, items' in formatted
