from __future__ import annotations

import os

import httpx

from .base import BaseSummarizer


class OllamaSummarizer(BaseSummarizer):
    def __init__(self) -> None:
        self.model = os.getenv("OLLAMA_MODEL", "llama3")
        self.url = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")

    async def summarize(self, payload: dict) -> str:
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

        body = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.2},
        }

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(f"{self.url}/api/generate", json=body)
                response.raise_for_status()
                data = response.json()
                text = (data.get("response") or "").strip()
                return text or self.fallback_summary(payload, "empty AI response")
        except Exception:
            return self.fallback_summary(payload, "ollama request failed")
