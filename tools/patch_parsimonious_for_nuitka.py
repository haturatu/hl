from __future__ import annotations

import pathlib

import parsimonious


def main() -> None:
    path = pathlib.Path(parsimonious.__file__).parent / "expressions.py"
    text = path.read_text(encoding="utf-8")
    patched = text.replace("import regex as re", "import re")
    if patched == text:
        return
    path.write_text(patched, encoding="utf-8")
    print(f"Patched {path} to use stdlib re for Nuitka builds")


if __name__ == "__main__":
    main()
