import re
import litellm
from litellm import completion

from typing import Optional

from core.llm.base import BaseLLM, LLMMessage, LLMResponse
from core.exceptions import LLMError

_THINK_TAG_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def _strip_think_tags(text: str) -> str:
    return _THINK_TAG_RE.sub("", text).strip()



class LiteLLMClient(BaseLLM):
    """
    LLM client using LiteLLM. Supports:
    - OpenAI cloud: model="gpt-4o", api_key=...
    - Anthropic: model="anthropic/claude-sonnet-4-6", api_key=...
    - Ollama: model="qwen3:4b", api_base="http://localhost:11434"
    - vLLM / OpenAI-compatible: model="Qwen/Qwen3-4B", api_base="http://host:8002/v1"

    When api_base is set, /v1 is appended automatically if missing, and
    custom_llm_provider="openai" is used so no provider prefix is needed in the model name.
    """

    def __init__(
        self,
        model: str,
        api_key: str = "",
        api_base: str = "",
        temperature: float = 0.0,
        max_tokens: Optional[int] = 16384,
        top_p: Optional[float] = None,
    ) -> None:
        self.model = model
        self.api_key = api_key or None
        self.api_base = api_base or None
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.top_p = top_p
        litellm.drop_params = True

    def complete(self, messages: list[LLMMessage], **kwargs) -> LLMResponse:
        try:
            call_kwargs: dict = {
                "model": self.model,
                "messages": [{"role": m.role, "content": m.content} for m in messages],
                "temperature": kwargs.get("temperature", self.temperature),
                "max_completion_tokens": kwargs.get("max_tokens", self.max_tokens),
            }
            if self.top_p is not None:
                call_kwargs["top_p"] = self.top_p
            if self.api_key:
                call_kwargs["api_key"] = self.api_key
            if self.api_base:
                # Ensure api_base ends with /v1 for OpenAI-compatible endpoints
                api_base = self.api_base.rstrip('/')
                if not api_base.endswith('/v1'):
                    api_base += '/v1'
                call_kwargs["api_base"] = api_base
                call_kwargs["custom_llm_provider"] = "openai"
            response = completion(**call_kwargs)
            raw_content = response.choices[0].message.content or ""
            return LLMResponse(
                content=_strip_think_tags(raw_content),
                model=response.model or self.model,
                input_tokens=response.usage.prompt_tokens if response.usage else 0,
                output_tokens=response.usage.completion_tokens if response.usage else 0,
            )
        except Exception as e:
            raise LLMError(f"LLM call failed: {e}") from e

    @classmethod
    def from_settings(cls) -> "LiteLLMClient":
        from core.config import settings
        return cls(
            model=settings.llm_model,
            api_key=settings.llm_api_key,
            api_base=settings.llm_base_url,
        )
