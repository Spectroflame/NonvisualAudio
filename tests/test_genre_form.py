"""Unit tests for the pure helpers inside genre_form_dialog.

The wx widgets themselves need a display and are covered by manual
smoke tests, but ``slugify``/``_parse_num`` are plain functions and
worth pinning down.
"""

from __future__ import annotations

import pytest

from nonvisualaudio.ui.genre_form_dialog import (
    _ValidationError,
    _parse_num,
    normalise_number_field,
    slugify,
)


def test_slugify_basic():
    assert slugify("Audio Drama — Modern Commercial") == "audio_drama_modern_commercial"
    assert slugify("Pop — 80s or 90s") == "pop_80s_or_90s"


def test_slugify_transliterates_german_umlauts():
    assert slugify("Hörspiel") == "hoerspiel"
    assert slugify("Lärm und Stille") == "laerm_und_stille"
    assert slugify("Straße") == "strasse"
    assert slugify("Über") == "ueber"


def test_slugify_strips_edge_underscores():
    assert slugify("   weird!!!  name  ") == "weird_name"
    assert slugify("---") == ""


def test_parse_num_accepts_point_and_comma():
    assert _parse_num("-14.0", "X") == -14.0
    assert _parse_num("-14,0", "X") == -14.0
    assert _parse_num("  7.5  ", "X") == 7.5


def test_parse_num_rejects_text():
    with pytest.raises(_ValidationError):
        _parse_num("abc", "X")


def test_parse_num_rejects_empty():
    with pytest.raises(_ValidationError):
        _parse_num("", "X")


def test_parse_num_rejects_infinity():
    with pytest.raises(_ValidationError):
        _parse_num("inf", "X")


def test_normalise_number_field_appends_decimal_to_integer():
    assert normalise_number_field("3") == "3.0"
    assert normalise_number_field("-15") == "-15.0"
    assert normalise_number_field("+8") == "+8.0"


def test_normalise_number_field_keeps_existing_decimal():
    assert normalise_number_field("3.5") == "3.5"
    assert normalise_number_field("-14.2") == "-14.2"


def test_normalise_number_field_replaces_comma_with_period():
    assert normalise_number_field("3,5") == "3.5"
    assert normalise_number_field("-14,2") == "-14.2"


def test_normalise_number_field_strips_surrounding_whitespace():
    assert normalise_number_field("  -7  ") == "-7.0"
    assert normalise_number_field("  3,5  ") == "3.5"


def test_normalise_number_field_preserves_non_numeric_text():
    # Validation runs on save and will surface a real error there;
    # the blur handler must not mutate text that doesn't parse.
    assert normalise_number_field("abc") == "abc"
    assert normalise_number_field("") == ""
    assert normalise_number_field("3.5.5") == "3.5.5"
