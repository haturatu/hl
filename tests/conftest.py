"""Ensure tests run with a deterministic display language.

The CLI resolves its display language from HL_LANG / LANGUAGE / LC_ALL /
LC_MESSAGES / LANG at startup. On developer machines where one of those points
to a non-English locale, user-facing error/status strings become translated and
assertions that match English text would break. Pin the language to English for
the whole suite; tests that exercise i18n still override it via monkeypatch.
"""

import os

os.environ["HL_LANG"] = "en"
for _var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
    os.environ.pop(_var, None)
