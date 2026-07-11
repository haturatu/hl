"""Compile ``.po`` catalogs into binary ``.mo`` files (pure Python).

This mirrors the behaviour of GNU ``msgfmt`` for the subset of features the
hl CLI needs: metadata headers, singular entries and plural entries with
``Plural-Forms`` support. No third-party dependencies are required so it works
in CI and in the Nuitka build environment.
"""
from __future__ import annotations

import os
import struct
import sys
from typing import Iterator


def _decode_po_string(value: str) -> str:
    """Decode a single quoted, escape-encoded ``.po`` string literal."""
    out: list[str] = []
    i = 0
    while i < len(value):
        ch = value[i]
        if ch == "\\":
            nxt = value[i + 1] if i + 1 < len(value) else ""
            mapping = {
                "n": "\n",
                "t": "\t",
                "r": "\r",
                '"': '"',
                "\\": "\\",
                "a": "\a",
                "b": "\b",
                "f": "\f",
                "v": "\v",
            }
            out.append(mapping.get(nxt, nxt))
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _split_quoted(line: str) -> list[str]:
    parts: list[str] = []
    for token in line.split('""'):
        token = token.strip()
        if not token:
            continue
        if token.startswith('"') and token.endswith('"'):
            parts.append(token[1:-1])
        else:
            parts.append(token)
    return parts


class _PoEntry:
    __slots__ = ("msgid", "msgid_plural", "msgstrs")

    def __init__(self) -> None:
        self.msgid: str = ""
        self.msgid_plural: str = ""
        self.msgstrs: list[str] = [""]


def parse_po(path: str) -> tuple[dict[str, str], list[_PoEntry]]:
    """Parse a ``.po`` file.

    Returns ``(metadata, entries)`` where ``metadata`` is the key/value header
    (from the empty msgid) and ``entries`` is the list of translatable entries
    in file order.
    """
    metadata: dict[str, str] = {}
    entries: list[_PoEntry] = []
    current: _PoEntry | None = None

    with open(path, "r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("msgctxt"):
                continue
            if line.startswith("msgid"):
                if current is not None:
                    entries.append(current)
                current = _PoEntry()
                text = line[len("msgid") :].strip()
                current.msgid = _decode_po_string("".join(_split_quoted(text)))
            elif line.startswith("msgid_plural"):
                text = line[len("msgid_plural") :].strip()
                current.msgid_plural = _decode_po_string("".join(_split_quoted(text)))
            elif line.startswith("msgstr["):
                # plural form: msgstr[0], msgstr[1], ...
                index = int(line[len("msgstr[") : line.index("]")])
                text = line[line.index("]") + 1 :].strip()
                decoded = _decode_po_string("".join(_split_quoted(text)))
                while len(current.msgstrs) <= index:
                    current.msgstrs.append("")
                current.msgstrs[index] = decoded
            elif line.startswith("msgstr"):
                text = line[len("msgstr") :].strip()
                current.msgstrs = [_decode_po_string("".join(_split_quoted(text)))]

    if current is not None:
        entries.append(current)

    # The first entry with an empty msgid carries the metadata header.
    if entries and entries[0].msgid == "":
        header = entries.pop(0)
        for block in header.msgstrs[0].split("\n"):
            if ":" in block:
                key, _, value = block.partition(":")
                metadata[key.strip()] = value.strip()

    return metadata, entries


def _build_mo(metadata: dict[str, str], entries: list[_PoEntry]) -> bytes:
    # Catalog key -> translation. Plural entries store all forms NUL-joined.
    catalog: dict[str, bytes] = {}
    order: list[str] = []
    for entry in entries:
        if entry.msgid == "":
            continue
        if entry.msgid_plural:
            joined = "\0".join(entry.msgstrs) if entry.msgstrs else ""
            key = entry.msgid
        else:
            joined = entry.msgstrs[0] if entry.msgstrs else ""
            key = entry.msgid
        if key not in catalog:
            catalog[key] = joined.encode("utf-8")
            order.append(key)

    # Metadata header with sane defaults.
    header_lines = [
        "Project-Id-Version: hl_cli",
        "MIME-Version: 1.0",
        "Content-Type: text/plain; charset=UTF-8",
        "Content-Transfer-Encoding: 8bit",
    ]
    for reserved in ("MIME-Version", "Content-Type", "Content-Transfer-Encoding"):
        metadata.pop(reserved, None)
    for key, value in metadata.items():
        header_lines.append(f"{key}: {value}")
    header = ("\n".join(header_lines) + "\n").encode("utf-8")

    keys = [b""]
    values = [header]
    for key in order:
        keys.append(key.encode("utf-8"))
        values.append(catalog[key])

    # Little-endian MO layout without a hash table (Python ignores it).
    magic = 0x950412DE
    version = 0
    num_strings = len(keys)
    offset_originals = 28
    offset_translations = offset_originals + num_strings * 8
    pool_start = offset_translations + num_strings * 8
    offset_hash = 0
    hash_size = 0

    # Build the string data blob and the offset/length tables.
    originals_table: list[bytes] = []
    translations_table: list[bytes] = []
    blob = b""
    orig_offsets: list[tuple[int, int]] = []
    trans_offsets: list[tuple[int, int]] = []

    for key_bytes in keys:
        orig_offsets.append((len(key_bytes), pool_start + len(blob)))
        blob += key_bytes + b"\x00"
    for value_bytes in values:
        trans_offsets.append((len(value_bytes), pool_start + len(blob)))
        blob += value_bytes + b"\x00"

    def table_bytes(offsets: list[tuple[int, int]]) -> bytes:
        out = b""
        for length, offset in offsets:
            out += struct.pack("<II", length, offset)
        return out

    original_table_bytes = table_bytes(orig_offsets)
    translation_table_bytes = table_bytes(trans_offsets)

    header_struct = struct.pack(
        "<IIIIIII",
        magic,
        version,
        num_strings,
        offset_originals,
        offset_translations,
        hash_size,
        offset_hash,
    )
    return (
        header_struct
        + original_table_bytes
        + translation_table_bytes
        + blob
    )


def compile_po(po_path: str, mo_path: str) -> None:
    metadata, entries = parse_po(po_path)
    data = _build_mo(metadata, entries)
    os.makedirs(os.path.dirname(mo_path), exist_ok=True)
    with open(mo_path, "wb") as handle:
        handle.write(data)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 2:
        print("usage: build_locales.py <input.po> <output.mo>", file=sys.stderr)
        return 2
    compile_po(argv[0], argv[1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
