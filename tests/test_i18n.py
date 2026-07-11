"""Tests for the hl_cli i18n (localization) module."""

import importlib

import pytest

from hl_cli import i18n
from hl_cli.i18n import _


@pytest.fixture(autouse=True)
def _reset_language():
    yield
    # Avoid leaking the active catalog into other test modules.
    i18n.install_language("en")


def test_default_language_is_identity():
    i18n.install_language("en")
    assert i18n.current_language() == "en"
    assert _("Add account") == "Add account"


def test_japanese_translation_is_applied():
    i18n.install_language("ja")
    assert i18n.current_language() == "ja"
    assert _("Add account") == "アカウントを追加"


def test_chinese_translation_is_applied():
    i18n.install_language("zh")
    assert i18n.current_language() == "zh"
    assert _("Add account") == "添加账户"


def test_korean_translation_is_applied():
    i18n.install_language("ko")
    assert i18n.current_language() == "ko"
    assert _("Add account") == "계정 추가"


def test_argv_language_override():
    chosen = i18n.resolve_language(argv=["--lang", "ja", "account", "ls"])
    assert chosen == "ja"
    chosen = i18n.resolve_language(argv=["account", "ls", "--lang=zh"])
    assert chosen == "zh"


def test_env_language_override(monkeypatch):
    monkeypatch.setenv("HL_LANG", "ja")
    assert i18n.resolve_language() == "ja"


def test_posix_lang_env_is_parsed(monkeypatch):
    for var in ("HL_LANG", "LANGUAGE", "LC_ALL", "LC_MESSAGES"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LANG", "ko_KR.UTF-8")
    assert i18n.resolve_language() == "ko"


def test_explicit_language_beats_env(monkeypatch):
    monkeypatch.setenv("HL_LANG", "ja")
    assert i18n.resolve_language("zh") == "zh"


def test_unknown_language_falls_back_to_identity():
    i18n.install_language("xx")
    assert i18n.current_language() == "xx"
    assert _("Order placed") == "Order placed"


def test_available_languages_includes_shipped_catalogs():
    langs = i18n.available_languages()
    assert "en" in langs
    for lang in ("ja", "zh", "ko"):
        assert lang in langs


def test_env_var_reload_after_module_import():
    importlib.reload(i18n)
    assert hasattr(i18n, "_")
