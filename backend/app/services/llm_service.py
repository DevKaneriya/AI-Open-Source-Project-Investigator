from groq import Groq
from backend.app.core.config import settings


class LLMService:
    def __init__(self):
        if not settings.groq_api_key:
            raise RuntimeError(
                "GROQ_API_KEY is not configured. Please set the GROQ_API_KEY environment variable."
            )

        try:
            self.client = Groq(
                api_key=settings.groq_api_key
            )
        except Exception as e:
            raise RuntimeError(
                f"Failed to initialize Groq client: {str(e)}"
            )

        self.model = settings.query_analyzer_model

    def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict,
    ) -> dict:

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": system_prompt,
                    },
                    {
                        "role": "user",
                        "content": user_prompt,
                    },
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "structured_response",
                        "schema": response_schema,
                        "strict": True,
                    },
                },
            )
        except Exception as e:
            raise RuntimeError(
                f"LLM API request failed: {str(e)}"
            )

        content = response.choices[0].message.content

        if not content:
            raise RuntimeError(
                "LLM returned an empty response."
            )

        import json

        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            raise RuntimeError(
                f"Failed to parse LLM response as JSON: {str(e)}"
            )