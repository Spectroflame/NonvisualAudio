"""Tests for the theme module."""

from __future__ import annotations

import pytest

import wx

from nonvisualaudio.ui import theme


@pytest.fixture(scope="module")
def wx_app() -> wx.App:
    """Create the wxApp once — wx.Colour and friends need it in scope."""
    app = wx.App(False)
    yield app


@pytest.fixture(autouse=True)
def restore_current_theme() -> None:
    """Reset the module-level theme after each test."""
    previous = theme.current()
    yield
    theme.set_current(previous)


def test_resolve_auto_returns_concrete_key(wx_app):
    result = theme.resolve_auto()
    assert result in ("light", "dark")


def test_resolve_maps_known_keys(wx_app):
    assert theme.resolve("light") == "light"
    assert theme.resolve("dark") == "dark"
    assert theme.resolve("high_contrast") == "high_contrast"


def test_resolve_unknown_falls_back_to_light(wx_app):
    assert theme.resolve("martian") == "light"
    assert theme.resolve("") == "light"


def test_resolve_auto_is_replaced_by_concrete_key(wx_app):
    # Whatever the system returns, it must never be "auto" itself.
    assert theme.resolve("auto") in ("light", "dark")


def test_set_current_tracks_choice():
    theme.set_current("dark")
    assert theme.current() == "dark"
    theme.set_current("auto")
    assert theme.current() == "auto"


def test_set_current_rejects_unknown():
    theme.set_current("martian")
    assert theme.current() == theme.DEFAULT_THEME


def test_apply_colours_panel_and_textctrl(wx_app):
    frame = wx.Frame(None)
    panel = wx.Panel(frame)
    text = wx.TextCtrl(panel, style=wx.TE_READONLY)
    theme.apply(frame, "high_contrast")

    bg = text.GetBackgroundColour()
    fg = text.GetForegroundColour()
    assert (bg.Red(), bg.Green(), bg.Blue()) == (0, 0, 0)
    assert (fg.Red(), fg.Green(), fg.Blue()) == (255, 255, 0)

    panel_bg = panel.GetBackgroundColour()
    assert (panel_bg.Red(), panel_bg.Green(), panel_bg.Blue()) == (0, 0, 0)

    frame.Destroy()


def test_apply_light_theme_whites_the_background(wx_app):
    frame = wx.Frame(None)
    text = wx.TextCtrl(frame, style=wx.TE_READONLY)
    theme.apply(frame, "light")
    bg = text.GetBackgroundColour()
    assert (bg.Red(), bg.Green(), bg.Blue()) == (255, 255, 255)
    frame.Destroy()


def test_apply_dark_theme_uses_dark_background(wx_app):
    frame = wx.Frame(None)
    text = wx.TextCtrl(frame, style=wx.TE_READONLY)
    theme.apply(frame, "dark")
    bg = text.GetBackgroundColour()
    # "dark" is (35, 35, 38) — compare exactly so a colour change gets noticed.
    assert (bg.Red(), bg.Green(), bg.Blue()) == (35, 35, 38)
    frame.Destroy()


def test_valid_themes_constant():
    assert theme.VALID_THEMES == ("auto", "light", "dark", "high_contrast")
