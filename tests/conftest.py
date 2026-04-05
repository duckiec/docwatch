"""Shared pytest fixtures for the test suite."""
from __future__ import annotations

import os
import sys

import pytest

# Ensure the project root is on sys.path so modules can be imported directly.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
