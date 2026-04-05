from __future__ import annotations

from abc import ABC, abstractmethod


class BaseSummarizer(ABC):
    @abstractmethod
    async def summarize(self, payload: dict) -> str:
        raise NotImplementedError

    def fallback_summary(self, payload: dict, reason: str | None = None) -> str:
        container_name = payload.get("container_name", "unknown")
        exit_code = payload.get("exit_code")
        crash_type = payload.get("crash_type", "Unknown")
        logs = (payload.get("logs") or "").strip()

        if logs:
            first_line = logs.splitlines()[0][:180]
            summary = (
                f"Container '{container_name}' stopped with exit code {exit_code}. "
                f"Crash category appears to be {crash_type}. "
                f"First log signal: {first_line}."
            )
        else:
            summary = (
                f"Container '{container_name}' stopped with exit code {exit_code}. "
                f"Crash category appears to be {crash_type}. "
                "No logs were available."
            )

        if reason:
            return f"AI summary unavailable ({reason}). {summary}"

        return summary
