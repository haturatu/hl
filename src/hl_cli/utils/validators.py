import re

from ..i18n import _


def validate_address(value: str) -> str:
    if not re.fullmatch(r"0x[a-fA-F0-9]{40}", value):
        raise ValueError(_("Invalid address: {value}").format(value=value))
    return value


def normalize_private_key(value: str) -> str:
    key = value if value.startswith("0x") else f"0x{value}"
    if not re.fullmatch(r"0x[a-fA-F0-9]{64}", key):
        raise ValueError(_("Invalid private key format"))
    return key


def validate_positive_number(value: str, name: str) -> float:
    num = float(value)
    if num <= 0:
        raise ValueError(_("{name} must be a positive number").format(name=name))
    return num


def validate_positive_integer(value: str, name: str) -> int:
    num = int(value)
    if num <= 0:
        raise ValueError(_("{name} must be a positive integer").format(name=name))
    return num


def normalize_side(value: str) -> str:
    lower = value.lower()
    if lower in {"buy", "sell", "long", "short"}:
        return lower
    raise ValueError(_('Side must be "buy", "sell", "long", or "short"'))


def side_is_buy(value: str) -> bool:
    return normalize_side(value) in {"buy", "long"}


def side_uses_spot(value: str) -> bool:
    return normalize_side(value) in {"buy", "sell"}


def normalize_tif(value: str) -> str:
    mapping = {"gtc": "Gtc", "ioc": "Ioc", "alo": "Alo"}
    try:
        return mapping[value.lower()]
    except KeyError as exc:
        raise ValueError(_('Time-in-force must be "Gtc", "Ioc", or "Alo"')) from exc
