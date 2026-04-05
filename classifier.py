from __future__ import annotations


def classify_crash(exit_code: int | None, logs: str, uptime_seconds: int | None) -> str:
    text = (logs or "").lower()

    if exit_code == 137 and ("oom" in text or "out of memory" in text or "killed process" in text):
        return "OOM"

    network_markers = [
        "connection refused",
        "network is unreachable",
        "timed out",
        "name or service not known",
        "temporary failure in name resolution",
        "dns",
    ]
    if any(marker in text for marker in network_markers):
        return "Network"

    if exit_code == 1:
        if uptime_seconds is not None and uptime_seconds < 60:
            return "Config error"
        return "Exit 1"

    if exit_code == 0:
        return "Clean exit"

    if exit_code == 137:
        return "Exit 137"

    return "Unknown"
