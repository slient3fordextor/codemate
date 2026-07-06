from app.adapters.models.anthropic import AnthropicClaudeModelAdapter
from app.adapters.models.base import ModelAdapter
from app.adapters.models.mock import MockModelAdapter
from app.adapters.models.openai_compatible import OpenAICompatibleModelAdapter
from app.core.config import ModelProviderConfig


def build_model_adapter(config: ModelProviderConfig) -> ModelAdapter:
    if config.provider == "mock":
        return MockModelAdapter()
    if config.provider in {"anthropic", "claude"}:
        return AnthropicClaudeModelAdapter(config)
    if config.provider == "openai_compatible":
        return OpenAICompatibleModelAdapter(config)
    if config.provider in {
        "ollama",
        "deepseek",
        "qwen",
        "zhipu",
        "moonshot",
        "baichuan",
    }:
        return OpenAICompatibleModelAdapter(config)

    msg = f"Unsupported model provider: {config.provider}"
    raise ValueError(msg)
