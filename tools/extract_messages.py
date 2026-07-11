"""Extract translatable strings from the hl CLI source.

Scans ``src/hl_cli`` for calls to ``_("...")`` and ``ngettext("...", "...")``
and regenerates ``locale/hl_cli.pot`` plus the per-language ``.po`` catalogs,
preserving any existing translations.

Usage::

    python tools/extract_messages.py
"""
from __future__ import annotations

import ast
import os
import sys
from typing import Iterable

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src", "hl_cli")
LOCALE_DIR = os.path.join(SRC, "locale")
DOMAIN = "hl_cli"
POT_PATH = os.path.join(LOCALE_DIR, f"{DOMAIN}.pot")

EXTRACT_FUNCS = {"_", "gettext", "ngettext"}


def _string_literal(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def collect_strings(path: str) -> list[tuple[str, str | None]]:
    """Return ``(msgid, msgid_plural_or_None)`` tuples found in ``path``."""
    with open(path, "r", encoding="utf-8") as handle:
        try:
            tree = ast.parse(handle.read(), filename=path)
        except SyntaxError:
            return []

    found: list[tuple[str, str | None]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = (
            func.attr
            if isinstance(func, ast.Attribute)
            else func.id
            if isinstance(func, ast.Name)
            else None
        )
        if name not in EXTRACT_FUNCS:
            continue
        if name == "ngettext":
            singular = _string_literal(node.args[0]) if len(node.args) > 0 else None
            plural = _string_literal(node.args[1]) if len(node.args) > 1 else None
            if singular is not None and plural is not None:
                found.append((singular, plural))
            continue
        literal = _string_literal(node.args[0]) if node.args else None
        if literal is not None:
            found.append((literal, None))
    return found


def _all_sources() -> Iterable[str]:
    for dirpath, _dirs, files in os.walk(SRC):
        if os.path.basename(dirpath) == "locale":
            continue
        for name in files:
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


def extract_catalog() -> dict[str, str | None]:
    """Build ``{msgid: msgid_plural_or_None}`` preserving first-seen order."""
    catalog: dict[str, str | None] = {}
    for path in sorted(_all_sources()):
        for msgid, plural in collect_strings(path):
            catalog.setdefault(msgid, plural)
    return catalog


def _write_pot(catalog: dict[str, str | None]) -> None:
    os.makedirs(LOCALE_DIR, exist_ok=True)
    lines = [
        'msgid ""',
        'msgstr ""',
        '"Project-Id-Version: hl_cli\\n"',
        '"MIME-Version: 1.0\\n"',
        '"Content-Type: text/plain; charset=UTF-8\\n"',
        '"Content-Transfer-Encoding: 8bit\\n"',
        "",
    ]
    for msgid, plural in catalog.items():
        lines.append(f'msgid "{_escape(msgid)}"')
        if plural:
            lines.append(f'msgid_plural "{_escape(plural)}"')
            lines.append('msgstr[0] ""')
            lines.append('msgstr[1] ""')
        else:
            lines.append('msgstr ""')
        lines.append("")
    with open(POT_PATH, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _read_po_translations(path: str) -> dict[str, list[str]]:
    """Return ``{msgid: [msgstr forms]}`` from an existing ``.po`` file."""
    if not os.path.isfile(path):
        return {}
    translations: dict[str, list[str]] = {}
    msgid = None
    plural = None
    forms: list[str] = []
    with open(path, "r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if line.startswith("#") or not line:
                continue
            if line.startswith("msgid "):
                if msgid is not None:
                    translations[msgid] = forms
                msgid = _unescape(_string_from(line[len("msgid ") :]))
                plural = None
                forms = [""]
            elif line.startswith("msgid_plural "):
                plural = _unescape(_string_from(line[len("msgid_plural ") :]))
            elif line.startswith("msgstr["):
                idx = int(line[len("msgstr[") : line.index("]")])
                while len(forms) <= idx:
                    forms.append("")
                forms[idx] = _unescape(_string_from(line[line.index("]") + 1 :]))
            elif line.startswith("msgstr "):
                forms = [_unescape(_string_from(line[len("msgstr ") :]))]
    if msgid is not None:
        translations[msgid] = forms
    return translations


def _string_from(token: str) -> str:
    token = token.strip()
    if token.startswith('"') and token.endswith('"'):
        return token[1:-1]
    return token


def _unescape(token: str) -> str:
    return (
        token.replace('\\"', '"')
        .replace("\\n", "\n")
        .replace("\\t", "\t")
        .replace("\\\\", "\\")
    )


def _write_po(path: str, catalog: dict[str, str | None], lang: str) -> None:
    existing = _read_po_translations(path)
    lines = [
        'msgid ""',
        'msgstr ""',
        f'"Language: {lang}\\n"',
        '"Project-Id-Version: hl_cli\\n"',
        '"MIME-Version: 1.0\\n"',
        '"Content-Type: text/plain; charset=UTF-8\\n"',
        '"Content-Transfer-Encoding: 8bit\\n"',
        f'"Plural-Forms: nplurals=2; plural=(n != 1);\\n"',
        "",
    ]
    for msgid, plural in catalog.items():
        lines.append(f'msgid "{_escape(msgid)}"')
        if plural:
            lines.append(f'msgid_plural "{_escape(plural)}"')
            prior = existing.get(msgid, ["", ""])
            forms = prior if len(prior) >= 2 else ["", ""]
            lines.append(f'msgstr[0] "{_escape(forms[0])}"')
            lines.append(f'msgstr[1] "{_escape(forms[1])}"')
        else:
            translation = existing.get(msgid, [""])[0]
            lines.append(f'msgstr "{_escape(translation)}"')
        lines.append("")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


def main(argv: list[str] | None = None) -> int:
    catalog = extract_catalog()
    _write_pot(catalog)
    if os.path.isdir(LOCALE_DIR):
        for entry in os.listdir(LOCALE_DIR):
            po_path = os.path.join(LOCALE_DIR, entry, "LC_MESSAGES", f"{DOMAIN}.po")
            if os.path.isfile(po_path):
                _write_po(po_path, catalog, entry)
                print(f"updated {os.path.relpath(po_path, ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
