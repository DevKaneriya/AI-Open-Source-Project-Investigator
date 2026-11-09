import json as json_module
import logging
import re
import time

from groq import Groq
from backend.app.core.config import settings

logger = logging.getLogger(__name__)


class LLMService:
    # Model capabilities mapping
    STRUCTURED_OUTPUT_SUPPORT = {
        'qwen/qwen3.8-27b': 'json_schema',  # Full schema support
        'openai/gpt-oss-20b': 'json_object',  # Basic JSON object support
        'openai/gpt-oss-120b': 'prompt_only',  # No response_format support
    }
    
    def __init__(self):
        if not settings.groq_api_key:
            raise RuntimeError(
                "GROQ_API_KEY is not configured. Please set the GROQ_API_KEY environment variable."
            )

        try:
            # Retries are handled in _create_with_retry, which honours the
            # wait time Groq reports for rate limits.
            self.client = Groq(
                api_key=settings.groq_api_key,
                max_retries=0,
            )
        except Exception as e:
            raise RuntimeError(
                f"Failed to initialize Groq client: {str(e)}"
            )

        self.model = settings.query_analyzer_model
        self.output_mode = self.STRUCTURED_OUTPUT_SUPPORT.get(self.model, 'json_schema')

    def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict,
        max_tokens: int = 512,  # Default for query analysis
    ) -> dict:
        """
        Generate structured JSON response from LLM.
        
        Adapts to model capabilities:
        - qwen models: Use json_schema with strict validation
        - gpt-oss-20b: Use json_object format
        - gpt-oss-120b: Use prompt-based JSON generation (no response_format)
        
        Args:
            system_prompt: System instructions
            user_prompt: User query with evidence
            response_schema: JSON schema for structured output
            max_tokens: Maximum completion tokens (configurable per use case)
            
        Returns:
            dict: Parsed JSON response with metadata
            
        Raises:
            RuntimeError: If API call fails or response is invalid
        """
        # Build messages based on output mode
        messages = [
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": user_prompt,
            },
        ]
        
        # Add JSON format instructions for prompt-only mode
        if self.output_mode == 'prompt_only':
            # Inject JSON schema into system prompt
            schema_desc = self._format_schema_for_prompt(response_schema)
            messages[0]["content"] += f"\n\nYou MUST return ONLY valid JSON matching this exact schema:\n{schema_desc}\n\nReturn ONLY the JSON object. No other text."
        
        # Build request parameters
        request_params = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.1,  # Low temperature for more deterministic responses
        }
        
        # Add response_format based on model capability
        if self.output_mode == 'json_schema':
            request_params["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "structured_response",
                    "schema": response_schema,
                    "strict": False,  # Avoid strict mode issues
                },
            }
        elif self.output_mode == 'json_object':
            request_params["response_format"] = {
                "type": "json_object",
            }
        # For 'prompt_only', no response_format parameter
        
        response = self._create_with_retry(request_params)

        choice = response.choices[0]
        content = choice.message.content
        finish_reason = choice.finish_reason

        if not content:
            raise RuntimeError(
                "LLM returned an empty response."
            )

        try:
            parsed_response = json_module.loads(self._strip_json_fences(content))
            
            # Validate against schema for prompt-only mode
            if self.output_mode == 'prompt_only':
                self._validate_response_schema(parsed_response, response_schema)
            
            # Store metadata
            if isinstance(parsed_response, dict):
                parsed_response['__llm_metadata__'] = {
                    'finish_reason': finish_reason,
                    'was_truncated': finish_reason == 'length',
                    'model': self.model,
                    'output_mode': self.output_mode,
                }
            
            return parsed_response
            
        except json_module.JSONDecodeError as e:
            raise RuntimeError(
                f"Failed to parse LLM response as JSON: {str(e)}. Response: {content[:200]}"
            )
        except ValueError as e:
            # Schema validation error
            raise RuntimeError(
                f"LLM response does not match required schema: {str(e)}"
            )
    
    # Rate limits (HTTP 429) and transient server/connection errors are retried.
    # Groq's free tier allows a few thousand tokens per minute, so a single
    # investigation (analysis + answer + repair) routinely exceeds it; the
    # error says how long to wait, and waiting is far better than returning
    # the raw error as the answer.
    MAX_ATTEMPTS = 4
    MAX_SINGLE_WAIT_SECONDS = 65.0
    MAX_TOTAL_WAIT_SECONDS = 150.0
    _RETRY_AFTER_RE = re.compile(r"try again in\s+(?:(\d+)m)?\s*([\d.]+)\s*(ms|s)\b", re.IGNORECASE)

    @classmethod
    def _retry_delay(cls, error: Exception, attempt: int) -> float | None:
        """Seconds to wait before retrying *error*, or None when it is not retryable."""
        message = str(error)
        status = getattr(error, "status_code", None)
        lowered = message.lower()
        transient = (
            status in (429, 500, 502, 503, 504)
            or "rate limit" in lowered
            or "rate_limit" in lowered
            or "over capacity" in lowered
            or type(error).__name__ in ("APIConnectionError", "APITimeoutError", "InternalServerError", "RateLimitError")
        )
        if not transient:
            return None
        # A daily quota does not reset within a request's lifetime; waiting is pointless.
        if re.search(r"per day|\(TPD\)|\(RPD\)|daily", message, re.IGNORECASE):
            return None
        headers = getattr(getattr(error, "response", None), "headers", None) or {}
        try:
            header_wait = float(headers.get("retry-after")) if headers.get("retry-after") else None
        except (TypeError, ValueError):
            header_wait = None
        match = cls._RETRY_AFTER_RE.search(message)
        if match:
            minutes, value, unit = match.groups()
            wait = float(value) / (1000.0 if unit.lower() == "ms" else 1.0) + 60.0 * float(minutes or 0)
        elif header_wait is not None:
            wait = header_wait
        else:
            wait = 2.0 * (2 ** attempt)
        return min(cls.MAX_SINGLE_WAIT_SECONDS, wait + 0.75)

    def _create_with_retry(self, request_params: dict):
        waited = 0.0
        for attempt in range(self.MAX_ATTEMPTS):
            try:
                return self.client.chat.completions.create(**request_params)
            except Exception as e:
                error_msg = str(e)
                delay = self._retry_delay(e, attempt)
                if delay is not None and attempt + 1 < self.MAX_ATTEMPTS and waited + delay <= self.MAX_TOTAL_WAIT_SECONDS:
                    logger.warning("LLM request failed (attempt %d/%d), retrying in %.1fs: %s",
                                   attempt + 1, self.MAX_ATTEMPTS, delay, error_msg[:200])
                    time.sleep(delay)
                    waited += delay
                    continue
                if "json_validate_failed" in error_msg:
                    raise RuntimeError(
                        f"LLM API request failed: {error_msg}. "
                        f"Model '{self.model}' may not support structured output. "
                        f"Consider switching to 'qwen/qwen3.8-27b' for better JSON schema support."
                    )
                if re.search(r"per day|\(TPD\)|\(RPD\)", error_msg, re.IGNORECASE):
                    raise RuntimeError(
                        "LLM API request failed: the language model's daily token quota is used up "
                        f"(rate limit, tokens per day). Details: {error_msg}"
                    )
                if delay is not None:
                    raise RuntimeError(
                        "LLM API request failed: the language model is rate limited or temporarily unavailable "
                        f"(retried {attempt + 1} times). Details: {error_msg}"
                    )
                raise RuntimeError(f"LLM API request failed: {error_msg}")
        raise RuntimeError("LLM API request failed after retries.")

    @staticmethod
    def _strip_json_fences(content: str) -> str:
        """Prompt-only models sometimes wrap the JSON in ``` fences or add prose around it."""
        text = content.strip()
        fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
        if fenced:
            return fenced.group(1)
        if not text.startswith("{"):
            start, end = text.find("{"), text.rfind("}")
            if 0 <= start < end:
                return text[start:end + 1]
        return text

    def _format_schema_for_prompt(self, schema: dict) -> str:
        """Format JSON schema as compact instructions for the model."""
        # For prompt-only mode, provide compact schema representation
        if schema.get('type') != 'object':
            return json_module.dumps(schema)
        
        properties = schema.get('properties', {})
        required = schema.get('required', [])
        
        # Build compact field descriptions
        fields = []
        for field_name, field_spec in properties.items():
            field_type = field_spec.get('type', 'string')
            if field_type == 'array':
                item_type = field_spec.get('items', {}).get('type', 'string')
                fields.append(f'"{field_name}": [{item_type}]')
            else:
                fields.append(f'"{field_name}": {field_type}')
        
        return f'{{\n  ' + ',\n  '.join(fields) + f'\n}}\nRequired fields: {", ".join(required)}'
    
    def _validate_response_schema(self, response: dict, schema: dict):
        """Basic validation that response contains required fields."""
        if schema.get('type') != 'object':
            return
        
        required_fields = schema.get('required', [])
        missing_fields = [f for f in required_fields if f not in response]
        
        if missing_fields:
            raise ValueError(
                f"Missing required fields: {', '.join(missing_fields)}. "
                f"Got fields: {', '.join(response.keys())}"
            )