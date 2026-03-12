import shutil
import sys
from pathlib import Path

from .paths import DB_PATH, HL_DIR


def _command_path_for_key() -> str:
    argv0 = sys.argv[0]
    resolved = shutil.which(argv0) if argv0 and "/" not in argv0 else argv0
    if not resolved:
        resolved = argv0
    return str(Path(resolved or "hl").resolve())


from .account_repo import (  # noqa: E402
    Account,
    _conn,
    create_account,
    delete_account,
    get_account_by_alias,
    get_account_count,
    get_all_accounts,
    get_default_account,
    is_alias_taken,
    set_default_account,
)

__all__ = [
    "Account",
    "DB_PATH",
    "HL_DIR",
    "_command_path_for_key",
    "_conn",
    "create_account",
    "delete_account",
    "get_account_by_alias",
    "get_account_count",
    "get_all_accounts",
    "get_default_account",
    "is_alias_taken",
    "set_default_account",
]
