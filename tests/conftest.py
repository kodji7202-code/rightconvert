"""Shared test configuration.

Tests decide explicitly where LibreOffice lives (a temp fixture, PATH, or the
isolated QA extraction). A developer machine's machine-wide LibreOffice must
not leak into "LibreOffice missing" assertions, so the Program Files / HKLM
fallback is disabled here; tests that cover it re-enable it.
"""

import pytest


@pytest.fixture(autouse=True)
def _no_system_libreoffice(monkeypatch):
    monkeypatch.setenv("RIGHTCONVERT_SYSTEM_OFFICE_LOOKUP", "0")
