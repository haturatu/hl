import base64
import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path
import secrets
import shutil
import sys
from typing import Optional

from Crypto.Cipher import ChaCha20

from .paths import DB_PATH, HL_DIR


@dataclass
class Account:
    id: int
    alias: str
    network: str
    user_address: str
    type: str
    source: str
    api_wallet_private_key: Optional[str]
    api_wallet_public_key: Optional[str]
    is_default: bool
    created_at: int
    updated_at: int


_ENC_PREFIX = "enc_v1:"


def _conn() -> sqlite3.Connection:
    HL_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    _migrate(conn)
    # TODO: Remove this compatibility migration after all existing plaintext
    # account rows have been rewritten to encrypted storage.
    _migrate_encrypted_account_fields(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS accounts (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          alias TEXT NOT NULL UNIQUE,
          network TEXT NOT NULL DEFAULT 'mainnet' CHECK (network IN ('mainnet', 'testnet')),
          user_address TEXT NOT NULL,
          type TEXT NOT NULL CHECK (type IN ('readonly', 'api_wallet')),
          source TEXT NOT NULL DEFAULT 'cli_import',
          api_wallet_private_key TEXT,
          api_wallet_public_key TEXT,
          is_default INTEGER NOT NULL DEFAULT 0,
          created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
          updated_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
        )
        """
    )
    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(accounts)").fetchall()
    }
    # TODO: Remove this compatibility migration after all existing databases have
    # been migrated to include the network column.
    if "network" not in columns:
        conn.execute("ALTER TABLE accounts ADD COLUMN network TEXT NOT NULL DEFAULT 'mainnet'")
    conn.commit()


def _to_account(row: sqlite3.Row) -> Account:
    return Account(
        id=row["id"],
        alias=row["alias"],
        network=row["network"],
        user_address=_decrypt_value(row["user_address"]),
        type=row["type"],
        source=row["source"],
        api_wallet_private_key=_decrypt_optional_value(row["api_wallet_private_key"]),
        api_wallet_public_key=_decrypt_optional_value(row["api_wallet_public_key"]),
        is_default=bool(row["is_default"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def get_all_accounts(network: str) -> list[Account]:
    conn = _conn()
    rows = conn.execute(
        "SELECT * FROM accounts WHERE network = ? ORDER BY is_default DESC, created_at ASC",
        (network,),
    ).fetchall()
    conn.close()
    return [_to_account(r) for r in rows]


def get_account_by_alias(alias: str, network: str) -> Optional[Account]:
    conn = _conn()
    row = conn.execute("SELECT * FROM accounts WHERE alias = ? AND network = ?", (alias, network)).fetchone()
    conn.close()
    return _to_account(row) if row else None


def get_default_account(network: str) -> Optional[Account]:
    conn = _conn()
    row = conn.execute("SELECT * FROM accounts WHERE network = ? AND is_default = 1 LIMIT 1", (network,)).fetchone()
    conn.close()
    return _to_account(row) if row else None


def get_account_count(network: str) -> int:
    conn = _conn()
    count = conn.execute("SELECT COUNT(*) AS c FROM accounts WHERE network = ?", (network,)).fetchone()["c"]
    conn.close()
    return int(count)


def is_alias_taken(alias: str, network: str) -> bool:
    return get_account_by_alias(alias, network) is not None


def create_account(
    *,
    alias: str,
    network: str,
    user_address: str,
    account_type: str,
    source: str = "cli_import",
    api_wallet_private_key: str | None = None,
    api_wallet_public_key: str | None = None,
    set_as_default: bool = False,
) -> Account:
    conn = _conn()
    count = conn.execute("SELECT COUNT(*) AS c FROM accounts WHERE network = ?", (network,)).fetchone()["c"]
    should_be_default = count == 0 or set_as_default
    if should_be_default:
        conn.execute("UPDATE accounts SET is_default = 0 WHERE network = ? AND is_default = 1", (network,))
    conn.execute(
        """
        INSERT INTO accounts (
            alias, network, user_address, type, source,
            api_wallet_private_key, api_wallet_public_key, is_default
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            alias,
            network,
            _encrypt_value(user_address),
            account_type,
            source,
            _encrypt_optional_value(api_wallet_private_key),
            _encrypt_optional_value(api_wallet_public_key),
            1 if should_be_default else 0,
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM accounts WHERE alias = ? AND network = ?", (alias, network)).fetchone()
    conn.close()
    return _to_account(row)


def set_default_account(alias: str, network: str) -> Account:
    conn = _conn()
    conn.execute("UPDATE accounts SET is_default = 0 WHERE network = ? AND is_default = 1", (network,))
    conn.execute(
        "UPDATE accounts SET is_default = 1, updated_at = strftime('%s', 'now') WHERE alias = ? AND network = ?",
        (alias, network),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM accounts WHERE alias = ? AND network = ?", (alias, network)).fetchone()
    conn.close()
    if not row:
        raise ValueError(f'Account with alias "{alias}" not found')
    return _to_account(row)


def delete_account(alias: str, network: str) -> bool:
    conn = _conn()
    row = conn.execute("SELECT * FROM accounts WHERE alias = ? AND network = ?", (alias, network)).fetchone()
    if not row:
        conn.close()
        return False
    was_default = bool(row["is_default"])
    conn.execute("DELETE FROM accounts WHERE alias = ? AND network = ?", (alias, network))
    if was_default:
        first = conn.execute(
            "SELECT id FROM accounts WHERE network = ? ORDER BY created_at ASC LIMIT 1",
            (network,),
        ).fetchone()
        if first:
            conn.execute("UPDATE accounts SET is_default = 1 WHERE id = ?", (first["id"],))
    conn.commit()
    conn.close()
    return True


def _command_path_for_key() -> str:
    argv0 = sys.argv[0]
    resolved = shutil.which(argv0) if argv0 and "/" not in argv0 else argv0
    if not resolved:
        resolved = argv0
    return str(Path(resolved or "hl").resolve())


def _chacha20_key() -> bytes:
    # Derive the encryption key from the current command path so only the same
    # installed command path can transparently decrypt the stored account data.
    return hashlib.sha256(_command_path_for_key().encode("utf-8")).digest()


def _encrypt_value(value: str) -> str:
    nonce = secrets.token_bytes(12)
    cipher = ChaCha20.new(key=_chacha20_key(), nonce=nonce)
    encrypted = cipher.encrypt(value.encode("utf-8"))
    return f"{_ENC_PREFIX}{base64.urlsafe_b64encode(nonce).decode()}:{base64.urlsafe_b64encode(encrypted).decode()}"


def _decrypt_value(value: str) -> str:
    if not value.startswith(_ENC_PREFIX):
        return value
    payload = value[len(_ENC_PREFIX):]
    nonce_b64, encrypted_b64 = payload.split(":", 1)
    nonce = base64.urlsafe_b64decode(nonce_b64.encode())
    encrypted = base64.urlsafe_b64decode(encrypted_b64.encode())
    cipher = ChaCha20.new(key=_chacha20_key(), nonce=nonce)
    return cipher.decrypt(encrypted).decode("utf-8")


def _encrypt_optional_value(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    return _encrypt_value(value)


def _decrypt_optional_value(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    return _decrypt_value(value)


def _migrate_encrypted_account_fields(conn: sqlite3.Connection) -> None:
    # TODO: Remove this whole function after all existing plaintext account rows
    # have been migrated to encrypted storage.
    rows = conn.execute(
        """
        SELECT id, user_address, api_wallet_private_key, api_wallet_public_key
        FROM accounts
        """
    ).fetchall()
    for row in rows:
        user_address = row["user_address"]
        api_wallet_private_key = row["api_wallet_private_key"]
        api_wallet_public_key = row["api_wallet_public_key"]
        if (
            isinstance(user_address, str)
            and user_address.startswith(_ENC_PREFIX)
            and (api_wallet_private_key is None or str(api_wallet_private_key).startswith(_ENC_PREFIX))
            and (api_wallet_public_key is None or str(api_wallet_public_key).startswith(_ENC_PREFIX))
        ):
            continue
        conn.execute(
            """
            UPDATE accounts
            SET user_address = ?, api_wallet_private_key = ?, api_wallet_public_key = ?
            WHERE id = ?
            """,
            (
                _encrypt_value(user_address),
                _encrypt_optional_value(api_wallet_private_key),
                _encrypt_optional_value(api_wallet_public_key),
                row["id"],
            ),
        )
    conn.commit()
