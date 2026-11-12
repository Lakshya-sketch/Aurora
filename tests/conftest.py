"""Shared pytest fixtures for Aurora v2 tests."""

from __future__ import annotations

import asyncio
import logging

import pytest


@pytest.fixture(autouse=True)
def _quiet_logs() -> None:
    """Make plugin/kernel logs visible at WARNING+ during tests.

    Tests that want to inspect log output should attach their own
    caplog handler.
    """
    logging.basicConfig(level=logging.WARNING, force=True)


@pytest.fixture
def event_loop_policy():
    """Use the default policy; placeholder for future overrides."""
    return asyncio.DefaultEventLoopPolicy()
