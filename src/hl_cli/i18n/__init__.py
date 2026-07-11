"""Internationalization (i18n) support for the hl CLI.

This module follows the Python :mod:`gettext` best practices. Translations
live under ``hl_cli/locale/<lang>/LC_MESSAGES/hl_cli.mo`` and are selected at
startup from, in order of precedence:

1. an explicit ``--lang`` CLI option,
2. the ``HL_LANG`` environment variable (project specific),
3. the standard POSIX variables ``LANGUAGE``, ``LC_ALL``, ``LC_MESSAGES``,
   ``LANG``,
4. ``en`` as a fallback.

Call :func:`install_language` once during startup (before any translatable
string is rendered). After that, import ``_`` from this module and wrap
user-facing literals::

    from ..i18n import _

    console.print(_("Order placed"))
    console.print(_("Asset: {coin}").format(coin=coin))
"""
from __future__ import annotations

import gettext as _gettext
import os

LOCALE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "locale"
)
DOMAIN = "hl_cli"
DEFAULT_LANGUAGE = "en"

_translator: _gettext.NullTranslations = _gettext.NullTranslations()
_current_language: str = DEFAULT_LANGUAGE


def gettext(message: str) -> str:
    """Translate ``message`` using the active catalog (identity fallback)."""
    return _translator.gettext(message)


def ngettext(singular: str, plural: str, n: int) -> str:
    """Translate ``singular``/``plural`` choosing by ``n`` (identity fallback)."""
    return _translator.ngettext(singular, plural, n)


# ``_`` is the conventional alias used throughout the codebase.
_ = gettext


def available_languages() -> list[str]:
    """Return the list of languages that ship a compiled catalog."""
    langs = {DEFAULT_LANGUAGE}
    if os.path.isdir(LOCALE_DIR):
        for name in os.listdir(LOCALE_DIR):
            mo_path = os.path.join(LOCALE_DIR, name, "LC_MESSAGES", f"{DOMAIN}.mo")
            if os.path.isfile(mo_path):
                langs.add(name)
    return sorted(langs)


def current_language() -> str:
    """Return the language active for the running process."""
    return _current_language


def _env_language() -> str | None:
    for var in ("HL_LANG", "LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(var)
        if not value:
            continue
        # LANGUAGE may be a colon separated list; values may carry an
        # encoding (.UTF-8) or a territory (_KR) suffix.
        candidate = value.split(":")[0].split(".")[0].split("_")[0].strip().lower()
        if candidate:
            return candidate
    return None


def _argv_language(argv: list[str] | None) -> str | None:
    if not argv:
        return None
    tokens = list(argv)
    for index, token in enumerate(tokens):
        if token == "--lang" and index + 1 < len(tokens):
            return tokens[index + 1]
        if token.startswith("--lang="):
            return token.split("=", 1)[1]
    return None


def resolve_language(
    explicit: str | None = None, argv: list[str] | None = None
) -> str:
    """Resolve the language to use given an optional explicit override."""
    return (
        explicit
        or _argv_language(argv)
        or _env_language()
        or DEFAULT_LANGUAGE
    )


def install_language(
    lang: str | None = None, *, argv: list[str] | None = None
) -> str:
    """Activate the translation catalog for ``lang`` (or auto-detected).

    Returns the language that was selected. Safe to call multiple times.
    """
    global _translator, _current_language
    chosen = resolve_language(lang, argv)
    if chosen == DEFAULT_LANGUAGE:
        # The source language is English: use the identity translator so that
        # strings are returned unchanged even if an (empty) en catalog exists.
        _translator = _gettext.NullTranslations()
    else:
        try:
            _translator = _gettext.translation(
                DOMAIN, LOCALE_DIR, languages=[chosen], fallback=True
            )
        except (OSError, FileNotFoundError):
            _translator = _gettext.NullTranslations()
    _current_language = chosen
    return chosen
