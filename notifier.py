from __future__ import annotations

import asyncio
import json
import os
import smtplib
from email.mime.text import MIMEText

import httpx
from telegram import Bot


async def send_telegram_message(message: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return False

    try:
        bot = Bot(token=token)
        await bot.send_message(chat_id=chat_id, text=message)
        return True
    except Exception:
        return False


def _send_email_sync(subject: str, message: str) -> bool:
    smtp_host = os.getenv("SMTP_HOST", "").strip()
    try:
        smtp_port = int(os.getenv("SMTP_PORT", "587"))
    except ValueError:
        smtp_port = 587
    smtp_user = os.getenv("SMTP_USER", "").strip()
    smtp_password = os.getenv("SMTP_PASSWORD", "").strip()
    recipient = os.getenv("ALERT_EMAIL_TO", "").strip()

    if not smtp_host or not recipient:
        return False

    msg = MIMEText(message)
    msg["Subject"] = subject
    msg["From"] = smtp_user or "docwatch@localhost"
    msg["To"] = recipient

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
            server.starttls()
            if smtp_user and smtp_password:
                server.login(smtp_user, smtp_password)
            server.sendmail(msg["From"], [recipient], msg.as_string())
        return True
    except Exception:
        return False


async def send_email_message(subject: str, message: str) -> bool:
    return await asyncio.to_thread(_send_email_sync, subject, message)


async def send_ntfy_message(subject: str, message: str) -> bool:
    base_url = os.getenv("NTFY_URL", "https://ntfy.sh").strip().rstrip("/")
    topic = os.getenv("NTFY_TOPIC", "").strip()
    if not topic:
        return False

    username = os.getenv("NTFY_USERNAME", "").strip()
    password = os.getenv("NTFY_PASSWORD", "").strip()
    priority = os.getenv("NTFY_PRIORITY", "3").strip()

    headers = {
        "Title": subject,
        "Priority": priority,
    }

    auth = None
    if username and password:
        auth = (username, password)

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(f"{base_url}/{topic}", content=message.encode("utf-8"), headers=headers, auth=auth)
            return response.status_code < 400
    except Exception:
        return False


async def send_webhook_message(subject: str, message: str) -> bool:
    webhook_url = os.getenv("WEBHOOK_URL", "").strip()
    if not webhook_url:
        return False

    secret = os.getenv("WEBHOOK_SECRET", "").strip()
    timeout = float(os.getenv("WEBHOOK_TIMEOUT_SECONDS", "15"))

    payload = {
        "title": subject,
        "message": message,
    }

    headers = {"Content-Type": "application/json"}
    if secret:
        headers["Authorization"] = f"Bearer {secret}"

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(webhook_url, content=json.dumps(payload).encode("utf-8"), headers=headers)
            return response.status_code < 400
    except Exception:
        return False


async def send_notifications(subject: str, message: str) -> dict:
    telegram_res, email_res, ntfy_res, webhook_res = await asyncio.gather(
        send_telegram_message(message),
        send_email_message(subject, message),
        send_ntfy_message(subject, message),
        send_webhook_message(subject, message),
        return_exceptions=True,
    )
    telegram_ok = bool(telegram_res) if not isinstance(telegram_res, Exception) else False
    email_ok = bool(email_res) if not isinstance(email_res, Exception) else False
    ntfy_ok = bool(ntfy_res) if not isinstance(ntfy_res, Exception) else False
    webhook_ok = bool(webhook_res) if not isinstance(webhook_res, Exception) else False
    return {"telegram": telegram_ok, "email": email_ok, "ntfy": ntfy_ok, "webhook": webhook_ok}


async def send_crash_notification(crash: dict) -> dict:
    subject = f"DocWatch Alert: {crash.get('container_name', 'unknown')}"
    body = (
        f"Container: {crash.get('container_name')}\n"
        f"Crash type: {crash.get('crash_type')}\n"
        f"Exit code: {crash.get('exit_code')}\n"
        f"Restart count: {crash.get('restart_count')}\n\n"
        f"AI Summary:\n{crash.get('ai_summary')}\n"
    )
    return await send_notifications(subject, body)
