"""Tests for classifier.classify_crash()."""
from __future__ import annotations

import pytest

from classifier import classify_crash


class TestClassifyCrash:
    # ------------------------------------------------------------------
    # OOM
    # ------------------------------------------------------------------
    def test_oom_with_oom_keyword(self):
        assert classify_crash(137, "oom killer activated", 60) == "OOM"

    def test_oom_with_out_of_memory_phrase(self):
        assert classify_crash(137, "Out of memory: kill process 1234", 60) == "OOM"

    def test_oom_with_killed_process_phrase(self):
        assert classify_crash(137, "Killed process 5678 (java)", 90) == "OOM"

    def test_exit_137_without_oom_text(self):
        # exit 137 but no OOM keywords → falls through to Exit 137 rule
        assert classify_crash(137, "something unrelated", 120) == "Exit 137"

    # ------------------------------------------------------------------
    # Network
    # ------------------------------------------------------------------
    def test_network_connection_refused(self):
        assert classify_crash(1, "connection refused to 127.0.0.1:5432", 120) == "Network"

    def test_network_unreachable(self):
        assert classify_crash(1, "network is unreachable", 30) == "Network"

    def test_network_timed_out(self):
        assert classify_crash(0, "operation timed out", 200) == "Network"

    def test_network_name_or_service_not_known(self):
        assert classify_crash(1, "name or service not known", 120) == "Network"

    def test_network_temporary_failure_in_name_resolution(self):
        assert classify_crash(1, "temporary failure in name resolution", 120) == "Network"

    def test_network_dns_marker(self):
        assert classify_crash(1, "DNS lookup failed", 120) == "Network"

    # ------------------------------------------------------------------
    # Config error
    # ------------------------------------------------------------------
    def test_config_error_exit1_fast_exit(self):
        assert classify_crash(1, "some config error in logs", 30) == "Config error"

    def test_config_error_exit1_uptime_boundary(self):
        # exactly 59 seconds → still Config error
        assert classify_crash(1, "missing env var", 59) == "Config error"

    def test_config_error_exit1_uptime_zero(self):
        assert classify_crash(1, "", 0) == "Config error"

    # ------------------------------------------------------------------
    # Exit 1
    # ------------------------------------------------------------------
    def test_exit_1_long_running(self):
        assert classify_crash(1, "some error", 120) == "Exit 1"

    def test_exit_1_uptime_exactly_60(self):
        # uptime == 60 is NOT < 60, so should be "Exit 1"
        assert classify_crash(1, "some error", 60) == "Exit 1"

    def test_exit_1_no_uptime(self):
        # uptime is None → condition `uptime_seconds is not None and uptime_seconds < 60` is False
        assert classify_crash(1, "some error", None) == "Exit 1"

    # ------------------------------------------------------------------
    # Clean exit
    # ------------------------------------------------------------------
    def test_clean_exit(self):
        assert classify_crash(0, "", 300) == "Clean exit"

    def test_clean_exit_no_logs(self):
        assert classify_crash(0, None, None) == "Clean exit"

    # ------------------------------------------------------------------
    # Unknown
    # ------------------------------------------------------------------
    def test_unknown_exit_code_2(self):
        assert classify_crash(2, "generic error", 120) == "Unknown"

    def test_unknown_exit_code_none(self):
        assert classify_crash(None, "no specific pattern", 120) == "Unknown"

    def test_unknown_negative_exit_code(self):
        assert classify_crash(-1, "", 60) == "Unknown"

    # ------------------------------------------------------------------
    # Case-insensitivity of log matching
    # ------------------------------------------------------------------
    def test_oom_case_insensitive(self):
        assert classify_crash(137, "OOM Killed", 60) == "OOM"

    def test_network_case_insensitive(self):
        assert classify_crash(1, "Connection Refused", 200) == "Network"

    # ------------------------------------------------------------------
    # None / empty logs
    # ------------------------------------------------------------------
    def test_none_logs_does_not_raise(self):
        # Should not raise; None logs → defaults to ""
        result = classify_crash(137, None, None)
        assert isinstance(result, str)

    def test_empty_logs_exit_0(self):
        assert classify_crash(0, "", 100) == "Clean exit"
