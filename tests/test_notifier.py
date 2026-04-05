"""Tests for notifier.py."""
from __future__ import annotations

import smtplib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import notifier


# ---------------------------------------------------------------------------
# send_telegram_message
# ---------------------------------------------------------------------------

async def test_telegram_no_token(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    result = await notifier.send_telegram_message("hello")
    assert result is False


async def test_telegram_no_chat_id(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok123")
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    result = await notifier.send_telegram_message("hello")
    assert result is False


async def test_telegram_success(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok123")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat456")

    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock(return_value=True)

    with patch("notifier.Bot", return_value=mock_bot):
        result = await notifier.send_telegram_message("test message")

    assert result is True
    mock_bot.send_message.assert_awaited_once_with(chat_id="chat456", text="test message")


async def test_telegram_exception_returns_false(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok123")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat456")

    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock(side_effect=RuntimeError("network error"))

    with patch("notifier.Bot", return_value=mock_bot):
        result = await notifier.send_telegram_message("test message")

    assert result is False


# ---------------------------------------------------------------------------
# _send_email_sync
# ---------------------------------------------------------------------------

def test_email_no_host(monkeypatch):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.setenv("ALERT_EMAIL_TO", "dest@example.com")
    result = notifier._send_email_sync("subject", "body")
    assert result is False


def test_email_no_recipient(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.delenv("ALERT_EMAIL_TO", raising=False)
    result = notifier._send_email_sync("subject", "body")
    assert result is False


def test_email_success(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USER", "user@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")
    monkeypatch.setenv("ALERT_EMAIL_TO", "dest@example.com")

    mock_smtp = MagicMock()
    mock_smtp.__enter__ = MagicMock(return_value=mock_smtp)
    mock_smtp.__exit__ = MagicMock(return_value=False)

    with patch("smtplib.SMTP", return_value=mock_smtp):
        result = notifier._send_email_sync("Test subject", "Test body")

    assert result is True
    mock_smtp.starttls.assert_called_once()
    mock_smtp.login.assert_called_once_with("user@example.com", "secret")
    mock_smtp.sendmail.assert_called_once()


def test_email_success_no_credentials(monkeypatch):
    """Email with no user/password should skip login."""
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.delenv("SMTP_USER", raising=False)
    monkeypatch.delenv("SMTP_PASSWORD", raising=False)
    monkeypatch.setenv("ALERT_EMAIL_TO", "dest@example.com")

    mock_smtp = MagicMock()
    mock_smtp.__enter__ = MagicMock(return_value=mock_smtp)
    mock_smtp.__exit__ = MagicMock(return_value=False)

    with patch("smtplib.SMTP", return_value=mock_smtp):
        result = notifier._send_email_sync("subject", "body")

    assert result is True
    mock_smtp.login.assert_not_called()


def test_email_invalid_port_defaults_to_587(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "notanumber")
    monkeypatch.setenv("ALERT_EMAIL_TO", "dest@example.com")

    mock_smtp = MagicMock()
    mock_smtp.__enter__ = MagicMock(return_value=mock_smtp)
    mock_smtp.__exit__ = MagicMock(return_value=False)

    with patch("smtplib.SMTP", return_value=mock_smtp) as smtp_cls:
        result = notifier._send_email_sync("subject", "body")

    assert result is True
    smtp_cls.assert_called_once_with("smtp.example.com", 587, timeout=15)


def test_email_smtp_exception_returns_false(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("ALERT_EMAIL_TO", "dest@example.com")

    with patch("smtplib.SMTP", side_effect=smtplib.SMTPException("connection failed")):
        result = notifier._send_email_sync("subject", "body")

    assert result is False


# ---------------------------------------------------------------------------
# send_notifications
# ---------------------------------------------------------------------------

async def test_send_notifications_both_false(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SMTP_HOST", raising=False)

    result = await notifier.send_notifications("subject", "body")
    assert result == {"telegram": False, "email": False}


async def test_send_notifications_telegram_only(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "cid")
    monkeypatch.delenv("SMTP_HOST", raising=False)

    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock(return_value=True)

    with patch("notifier.Bot", return_value=mock_bot):
        result = await notifier.send_notifications("subject", "body")

    assert result["telegram"] is True
    assert result["email"] is False


async def test_send_notifications_email_only(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("ALERT_EMAIL_TO", "dest@example.com")

    mock_smtp = MagicMock()
    mock_smtp.__enter__ = MagicMock(return_value=mock_smtp)
    mock_smtp.__exit__ = MagicMock(return_value=False)

    with patch("smtplib.SMTP", return_value=mock_smtp):
        result = await notifier.send_notifications("subject", "body")

    assert result["telegram"] is False
    assert result["email"] is True


# ---------------------------------------------------------------------------
# send_crash_notification
# ---------------------------------------------------------------------------

async def test_send_crash_notification_formats_body(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SMTP_HOST", raising=False)

    crash = {
        "container_name": "api",
        "crash_type": "OOM",
        "exit_code": 137,
        "restart_count": 3,
        "ai_summary": "Memory exhausted.",
    }

    captured_subject: list[str] = []
    captured_body: list[str] = []

    original = notifier.send_notifications

    async def _capture(subject, message):
        captured_subject.append(subject)
        captured_body.append(message)
        return {"telegram": False, "email": False}

    with patch.object(notifier, "send_notifications", _capture):
        await notifier.send_crash_notification(crash)

    assert "api" in captured_subject[0]
    assert "OOM" in captured_body[0]
    assert "137" in captured_body[0]
    assert "Memory exhausted." in captured_body[0]


async def test_send_crash_notification_unknown_container(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SMTP_HOST", raising=False)

    crash = {}
    captured_subjects: list[str] = []

    async def _capture(subject, message):
        captured_subjects.append(subject)
        return {"telegram": False, "email": False}

    with patch.object(notifier, "send_notifications", _capture):
        await notifier.send_crash_notification(crash)

    assert "unknown" in captured_subjects[0]
