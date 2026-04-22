"""Pytest session fixtures.

Pin the localisation layer to English for the whole suite so existing
string-match assertions in ``test_builder.py``/``test_comparison.py``/
``test_templates.py`` keep working regardless of the developer's
system locale or leftover ``NVA_LANG`` env vars.
"""

from __future__ import annotations

import pytest

from nonvisualaudio import localization


@pytest.fixture(autouse=True, scope="session")
def _force_english_locale() -> None:
    localization.load("en")
