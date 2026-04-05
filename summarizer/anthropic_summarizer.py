from __future__ import annotations

import asyncio
import os

from anthropic import Anthropic

from .base import BaseSummarizer


class AnthropicSummarizer(BaseSummarizer):
    def __init__(self) -> None:
        self.api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        self.model = "claude-haiku-4-5"

    async def summarize(self, payload: dict) -> str:
        if not self.api_key:
            return self.fallback_summary(payload, "missing ANTHROPIC_API_KEY")

        prompt = (
            "Summarize this Docker crash in 2-4 plain-English sentences with: "
            "what happened, likely cause, and suggested fix.\n\n"
            f"Container: {payload.get('container_name')}\n"
            f"Exit code: {payload.get('exit_code')}\n"
            f"Restart count: {payload.get('restart_count')}\n"
            f"Memory usage bytes: {payload.get('memory_usage')}\n"
            f"Memory limit bytes: {payload.get('memory_limit')}\n"
            f"Recent logs:\n{payload.get('logs')}\n"
        )

        def _call() -> str:
            client = Anthropic(api_key=self.api_key)
            message = client.messages.create(
                model=self.model,
                max_tokens=220,
                temperature=0.2,
                messages=[{"role": "user", "content": prompt}],
            )
            return "".join(
                block.text for block in message.content if getattr(block, "type", "") == "text"
            ).strip()

        try:
            result = await asyncio.to_thread(_call)
            return result or self.fallback_summary(payload, "empty AI response")
        except Exception:
            return self.fallback_summary(payload, "anthropic request failed")
