"""Tests for summarizer package — base, NoopSummarizer, get_summarizer, and all providers."""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from summarizer.base import BaseSummarizer
from summarizer import NoopSummarizer, get_summarizer
from summarizer.openai_summarizer import OpenAISummarizer
from summarizer.anthropic_summarizer import AnthropicSummarizer
from summarizer.ollama_summarizer import OllamaSummarizer
from summarizer.openrouter_summarizer import OpenRouterSummarizer


# ---------------------------------------------------------------------------
# BaseSummarizer.fallback_summary
# ---------------------------------------------------------------------------

class _ConcreteSummarizer(BaseSummarizer):
    """Minimal concrete subclass for testing BaseSummarizer."""
    async def summarize(self, payload: dict) -> str:
        return ""


class TestFallbackSummary:
    def setup_method(self):
        self.s = _ConcreteSummarizer()

    def test_with_logs_and_reason(self):
        payload = {
            "container_name": "web",
            "exit_code": 1,
            "crash_type": "Exit 1",
            "logs": "ERROR: something failed\nmore lines",
        }
        result = self.s.fallback_summary(payload, reason="test reason")
        assert "AI summary unavailable" in result
        assert "test reason" in result
        assert "web" in result
        assert "Exit 1" in result
        assert "ERROR: something failed" in result  # first line

    def test_with_logs_no_reason(self):
        payload = {
            "container_name": "api",
            "exit_code": 137,
            "crash_type": "OOM",
            "logs": "Killed process 42",
        }
        result = self.s.fallback_summary(payload)
        assert "AI summary unavailable" not in result
        assert "api" in result
        assert "OOM" in result

    def test_without_logs(self):
        payload = {
            "container_name": "db",
            "exit_code": 0,
            "crash_type": "Clean exit",
            "logs": "",
        }
        result = self.s.fallback_summary(payload)
        assert "No logs were available" in result

    def test_none_logs_treated_as_empty(self):
        payload = {
            "container_name": "db",
            "exit_code": 0,
            "crash_type": "Clean exit",
            "logs": None,
        }
        result = self.s.fallback_summary(payload)
        assert "No logs were available" in result

    def test_long_first_log_line_truncated(self):
        payload = {
            "container_name": "svc",
            "exit_code": 1,
            "crash_type": "Exit 1",
            "logs": "X" * 300,
        }
        result = self.s.fallback_summary(payload)
        # First line is 300 X's, but truncated to 180 chars
        assert "X" * 181 not in result
        assert "X" * 180 in result


# ---------------------------------------------------------------------------
# NoopSummarizer
# ---------------------------------------------------------------------------

async def test_noop_summarizer_returns_fallback():
    s = NoopSummarizer()
    payload = {"container_name": "x", "exit_code": 1, "crash_type": "Exit 1", "logs": "oops"}
    result = await s.summarize(payload)
    assert "no provider configured" in result
    assert "x" in result


# ---------------------------------------------------------------------------
# get_summarizer
# ---------------------------------------------------------------------------

def test_get_summarizer_anthropic(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "anthropic")
    s = get_summarizer()
    assert isinstance(s, AnthropicSummarizer)


def test_get_summarizer_openai(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    s = get_summarizer()
    assert isinstance(s, OpenAISummarizer)


def test_get_summarizer_ollama(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    s = get_summarizer()
    assert isinstance(s, OllamaSummarizer)


def test_get_summarizer_openrouter(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openrouter")
    s = get_summarizer()
    assert isinstance(s, OpenRouterSummarizer)


def test_get_summarizer_unknown_returns_noop(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "unknown_provider")
    s = get_summarizer()
    assert isinstance(s, NoopSummarizer)


def test_get_summarizer_case_insensitive(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "OPENAI")
    s = get_summarizer()
    assert isinstance(s, OpenAISummarizer)


# ---------------------------------------------------------------------------
# OpenAISummarizer
# ---------------------------------------------------------------------------

async def test_openai_no_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    s = OpenAISummarizer()
    payload = {"container_name": "svc", "exit_code": 1, "crash_type": "Exit 1", "logs": "err"}
    result = await s.summarize(payload)
    assert "OPENAI_API_KEY" in result


async def test_openai_success(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    mock_response = MagicMock()
    mock_response.output_text = "Container crashed due to OOM."

    mock_client = MagicMock()
    mock_client.responses.create.return_value = mock_response

    s = OpenAISummarizer()
    payload = {"container_name": "svc", "exit_code": 137, "crash_type": "OOM", "logs": "killed"}

    with patch("summarizer.openai_summarizer.OpenAI", return_value=mock_client):
        result = await s.summarize(payload)

    assert result == "Container crashed due to OOM."


async def test_openai_empty_response_returns_fallback(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    mock_response = MagicMock()
    mock_response.output_text = "  "  # whitespace only

    mock_client = MagicMock()
    mock_client.responses.create.return_value = mock_response

    s = OpenAISummarizer()
    payload = {"container_name": "svc", "exit_code": 1, "crash_type": "Exit 1", "logs": ""}

    with patch("summarizer.openai_summarizer.OpenAI", return_value=mock_client):
        result = await s.summarize(payload)

    assert "empty AI response" in result


async def test_openai_exception_returns_fallback(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    mock_client = MagicMock()
    mock_client.responses.create.side_effect = RuntimeError("API error")

    s = OpenAISummarizer()
    payload = {"container_name": "svc", "exit_code": 1, "crash_type": "Exit 1", "logs": ""}

    with patch("summarizer.openai_summarizer.OpenAI", return_value=mock_client):
        result = await s.summarize(payload)

    assert "openai request failed" in result


# ---------------------------------------------------------------------------
# AnthropicSummarizer
# ---------------------------------------------------------------------------

async def test_anthropic_no_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    s = AnthropicSummarizer()
    payload = {"container_name": "svc", "exit_code": 1, "crash_type": "Exit 1", "logs": ""}
    result = await s.summarize(payload)
    assert "ANTHROPIC_API_KEY" in result


async def test_anthropic_success(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = "Memory issue."

    mock_message = MagicMock()
    mock_message.content = [text_block]

    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_message

    s = AnthropicSummarizer()
    payload = {"container_name": "svc", "exit_code": 137, "crash_type": "OOM", "logs": "oom"}

    with patch("summarizer.anthropic_summarizer.Anthropic", return_value=mock_client):
        result = await s.summarize(payload)

    assert result == "Memory issue."


async def test_anthropic_non_text_blocks_ignored(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    tool_block = MagicMock()
    tool_block.type = "tool_use"

    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = "Real summary."

    mock_message = MagicMock()
    mock_message.content = [tool_block, text_block]

    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_message

    s = AnthropicSummarizer()
    payload = {"container_name": "svc", "exit_code": 1, "crash_type": "Exit 1", "logs": ""}

    with patch("summarizer.anthropic_summarizer.Anthropic", return_value=mock_client):
        result = await s.summarize(payload)

    assert result == "Real summary."


async def test_anthropic_empty_response_returns_fallback(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    mock_message = MagicMock()
    mock_message.content = []

    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_message

    s = AnthropicSummarizer()
    payload = {"container_name": "svc", "exit_code": 1, "crash_type": "Exit 1", "logs": ""}

    with patch("summarizer.anthropic_summarizer.Anthropic", return_value=mock_client):
        result = await s.summarize(payload)

    assert "empty AI response" in result


async def test_anthropic_exception_returns_fallback(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    mock_client = MagicMock()
    mock_client.messages.create.side_effect = RuntimeError("API error")

    s = AnthropicSummarizer()
    payload = {"container_name": "svc", "exit_code": 1, "crash_type": "Exit 1", "logs": ""}

    with patch("summarizer.anthropic_summarizer.Anthropic", return_value=mock_client):
        result = await s.summarize(payload)

    assert "anthropic request failed" in result


# ---------------------------------------------------------------------------
# OllamaSummarizer
# ---------------------------------------------------------------------------

async def test_ollama_success(monkeypatch):
    monkeypatch.setenv("OLLAMA_MODEL", "llama3")
    monkeypatch.setenv("OLLAMA_URL", "http://localhost:11434")

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"response": "OOM crash detected."}

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_response)

    s = OllamaSummarizer()
    payload = {"container_name": "svc", "exit_code": 137, "crash_type": "OOM", "logs": "oom"}

    with patch("summarizer.ollama_summarizer.httpx.AsyncClient", return_value=mock_client):
        result = await s.summarize(payload)

    assert result == "OOM crash detected."


async def test_ollama_empty_response_returns_fallback(monkeypatch):
    monkeypatch.setenv("OLLAMA_MODEL", "llama3")

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"response": ""}

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_response)

    s = OllamaSummarizer()
    payload = {"container_name": "svc", "exit_code": 1, "crash_type": "Exit 1", "logs": ""}

    with patch("summarizer.ollama_summarizer.httpx.AsyncClient", return_value=mock_client):
        result = await s.summarize(payload)

    assert "empty AI response" in result


async def test_ollama_exception_returns_fallback():
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(side_effect=RuntimeError("connection refused"))
    mock_client.__aexit__ = AsyncMock(return_value=False)

    s = OllamaSummarizer()
    payload = {"container_name": "svc", "exit_code": 1, "crash_type": "Exit 1", "logs": ""}

    with patch("summarizer.ollama_summarizer.httpx.AsyncClient", return_value=mock_client):
        result = await s.summarize(payload)

    assert "ollama request failed" in result


async def test_ollama_url_env_used(monkeypatch):
    monkeypatch.setenv("OLLAMA_URL", "http://custom-host:9999")
    s = OllamaSummarizer()
    assert s.url == "http://custom-host:9999"


async def test_ollama_trailing_slash_stripped(monkeypatch):
    monkeypatch.setenv("OLLAMA_URL", "http://localhost:11434/")
    s = OllamaSummarizer()
    assert s.url == "http://localhost:11434"


# ---------------------------------------------------------------------------
# OpenRouterSummarizer
# ---------------------------------------------------------------------------

async def test_openrouter_no_api_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    s = OpenRouterSummarizer()
    payload = {"container_name": "svc", "exit_code": 1, "crash_type": "Exit 1", "logs": ""}
    result = await s.summarize(payload)
    assert "OPENROUTER_API_KEY" in result


async def test_openrouter_success(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")

    mock_response = MagicMock()
    mock_response.output_text = "Router summary."

    mock_client = MagicMock()
    mock_client.responses.create.return_value = mock_response

    s = OpenRouterSummarizer()
    payload = {"container_name": "svc", "exit_code": 1, "crash_type": "Exit 1", "logs": "err"}

    with patch("summarizer.openrouter_summarizer.OpenAI", return_value=mock_client):
        result = await s.summarize(payload)

    assert result == "Router summary."


async def test_openrouter_exception_returns_fallback(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")

    mock_client = MagicMock()
    mock_client.responses.create.side_effect = RuntimeError("network error")

    s = OpenRouterSummarizer()
    payload = {"container_name": "svc", "exit_code": 1, "crash_type": "Exit 1", "logs": ""}

    with patch("summarizer.openrouter_summarizer.OpenAI", return_value=mock_client):
        result = await s.summarize(payload)

    assert "openrouter request failed" in result


async def test_openrouter_model_env(monkeypatch):
    monkeypatch.setenv("OPENROUTER_MODEL", "meta-llama/llama-3-8b")
    s = OpenRouterSummarizer()
    assert s.model == "meta-llama/llama-3-8b"
