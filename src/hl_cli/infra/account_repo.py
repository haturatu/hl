import sqlite3
from dataclasses import dataclass
from typing import Optional

from . import db as db_module
from .db_crypto import (
    decrypt_optional_value,
    decrypt_value,
    encrypt_optional_value,
    encrypt_value,
    migrate_encrypted_account_fields,
)

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

def _conn() -> sqlite3.Connection:
    db_module.HL_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_module.DB_PATH)
    conn.row_factory = sqlite3.Row
    _migrate(conn)
    # TODO: Remove this compatibility migration after all existing plaintext
    # account rows have been rewritten to encrypted storage.
    migrate_encrypted_account_fields(conn)
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
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(accounts)").fetchall()}
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
        user_address=decrypt_value(row["user_address"]),
        type=row["type"],
        source=row["source"],
        api_wallet_private_key=decrypt_optional_value(row["api_wallet_private_key"]),
        api_wallet_public_key=decrypt_optional_value(row["api_wallet_public_key"]),
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
    return [_to_account(row) for row in rows]

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
            encrypt_value(user_address),
            account_type,
            source,
            encrypt_optional_value(api_wallet_private_key),
            encrypt_optional_value(api_wallet_public_key),
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
