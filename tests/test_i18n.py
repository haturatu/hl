"""Tests for the hl_cli i18n (localization) module.

These tests avoid pytest-only features so they also run under the stdlib
``unittest discover`` command used in CI.
"""

import importlib
import os
import unittest

from hl_cli import i18n
from hl_cli.i18n import _


_LANG_VARS = ("HL_LANG", "LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG")


class I18nTests(unittest.TestCase):
    def setUp(self):
        self._saved_env = {var: os.environ.get(var) for var in _LANG_VARS}

    def tearDown(self):
        # Restore the environment so other test modules are unaffected.
        for var, value in self._saved_env.items():
            if value is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = value
        # Reset the active catalog to avoid leaking it into other modules.
        i18n.install_language("en")

    def _set_env(self, **kwargs):
        for var, value in kwargs.items():
            if value is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = value

    def test_default_language_is_identity(self):
        i18n.install_language("en")
        self.assertEqual(i18n.current_language(), "en")
        self.assertEqual(_("Add account"), "Add account")

    def test_japanese_translation_is_applied(self):
        i18n.install_language("ja")
        self.assertEqual(i18n.current_language(), "ja")
        self.assertEqual(_("Add account"), "アカウントを追加")

    def test_chinese_translation_is_applied(self):
        i18n.install_language("zh")
        self.assertEqual(i18n.current_language(), "zh")
        self.assertEqual(_("Add account"), "添加账户")

    def test_korean_translation_is_applied(self):
        i18n.install_language("ko")
        self.assertEqual(i18n.current_language(), "ko")
        self.assertEqual(_("Add account"), "계정 추가")

    def test_argv_language_override(self):
        self.assertEqual(
            i18n.resolve_language(argv=["--lang", "ja", "account", "ls"]), "ja"
        )
        self.assertEqual(
            i18n.resolve_language(argv=["account", "ls", "--lang=zh"]), "zh"
        )

    def test_env_language_override(self):
        self._set_env(HL_LANG="ja")
        self.assertEqual(i18n.resolve_language(), "ja")

    def test_posix_lang_env_is_parsed(self):
        self._set_env(HL_LANG=None, LANGUAGE=None, LC_ALL=None, LC_MESSAGES=None)
        self._set_env(LANG="ko_KR.UTF-8")
        self.assertEqual(i18n.resolve_language(), "ko")

    def test_explicit_language_beats_env(self):
        self._set_env(HL_LANG="ja")
        self.assertEqual(i18n.resolve_language("zh"), "zh")

    def test_unknown_language_falls_back_to_identity(self):
        i18n.install_language("xx")
        self.assertEqual(i18n.current_language(), "xx")
        self.assertEqual(_("Add account"), "Add account")

    def test_available_languages_includes_shipped_catalogs(self):
        langs = i18n.available_languages()
        self.assertIn("en", langs)
        for lang in ("ja", "zh", "ko"):
            self.assertIn(lang, langs)

    def test_env_var_reload_after_module_import(self):
        importlib.reload(i18n)
        self.assertTrue(hasattr(i18n, "_"))


if __name__ == "__main__":
    unittest.main()
