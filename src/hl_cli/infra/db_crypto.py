import base64
import hashlib
import sqlite3
import secrets
from typing import Optional

from Crypto.Cipher import ChaCha20

from . import db as db_module

_ENC_PREFIX = "enc_v1:"

def chacha20_key() -> bytes:
    # Derive the encryption key from the current command path so only the same
    # installed command path can transparently decrypt the stored account data.
    return hashlib.sha256(db_module._command_path_for_key().encode("utf-8")).digest()

def encrypt_value(value: str) -> str:
    nonce = secrets.token_bytes(12)
    cipher = ChaCha20.new(key=chacha20_key(), nonce=nonce)
    encrypted = cipher.encrypt(value.encode("utf-8"))
    return f"{_ENC_PREFIX}{base64.urlsafe_b64encode(nonce).decode()}:{base64.urlsafe_b64encode(encrypted).decode()}"

def decrypt_value(value: str) -> str:
    if not value.startswith(_ENC_PREFIX):
        return value
    payload = value[len(_ENC_PREFIX):]
    nonce_b64, encrypted_b64 = payload.split(":", 1)
    nonce = base64.urlsafe_b64decode(nonce_b64.encode())
    encrypted = base64.urlsafe_b64decode(encrypted_b64.encode())
    cipher = ChaCha20.new(key=chacha20_key(), nonce=nonce)
    return cipher.decrypt(encrypted).decode("utf-8")

def encrypt_optional_value(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    return encrypt_value(value)

def decrypt_optional_value(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    return decrypt_value(value)

def migrate_encrypted_account_fields(conn: sqlite3.Connection) -> None:
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
                encrypt_value(user_address),
                encrypt_optional_value(api_wallet_private_key),
                encrypt_optional_value(api_wallet_public_key),
                row["id"],
            ),
        )
    conn.commit()
