from __future__ import annotations

import os

from .anthropic_summarizer import AnthropicSummarizer
from .base import BaseSummarizer
from .ollama_summarizer import OllamaSummarizer
from .openai_summarizer import OpenAISummarizer
from .openrouter_summarizer import OpenRouterSummarizer


class NoopSummarizer(BaseSummarizer):
    async def summarize(self, payload: dict) -> str:
        return self.fallback_summary(payload, "no provider configured")


def get_summarizer() -> BaseSummarizer:
    provider = os.getenv("AI_PROVIDER", "anthropic").strip().lower()

    mapping = {
        "anthropic": AnthropicSummarizer,
        "openai": OpenAISummarizer,
        "ollama": OllamaSummarizer,
        "openrouter": OpenRouterSummarizer,
    }
    summarizer_cls = mapping.get(provider)
    return summarizer_cls() if summarizer_cls else NoopSummarizer()
