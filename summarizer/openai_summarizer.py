from __future__ import annotations

import asyncio
import os

from openai import OpenAI

from .base import BaseSummarizer


class OpenAISummarizer(BaseSummarizer):
    def __init__(self) -> None:
        self.api_key = os.getenv("OPENAI_API_KEY", "").strip()
        self.model = "gpt-4o-mini"

    async def summarize(self, payload: dict) -> str:
        if not self.api_key:
            return self.fallback_summary(payload, "missing OPENAI_API_KEY")

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
            client = OpenAI(api_key=self.api_key)
            response = client.responses.create(
                model=self.model,
                input=prompt,
                temperature=0.2,
                max_output_tokens=220,
            )
            return (response.output_text or "").strip()

        try:
            result = await asyncio.to_thread(_call)
            return result or self.fallback_summary(payload, "empty AI response")
        except Exception:
            return self.fallback_summary(payload, "openai request failed")
