import re

def validate_address(value: str) -> str:
    if not re.fullmatch(r"0x[a-fA-F0-9]{40}", value):
        raise ValueError(f"Invalid address: {value}")
    return value

def normalize_private_key(value: str) -> str:
    key = value if value.startswith("0x") else f"0x{value}"
    if not re.fullmatch(r"0x[a-fA-F0-9]{64}", key):
        raise ValueError("Invalid private key format")
    return key

def validate_positive_number(value: str, name: str) -> float:
    num = float(value)
    if num <= 0:
        raise ValueError(f"{name} must be a positive number")
    return num

def validate_positive_integer(value: str, name: str) -> int:
    num = int(value)
    if num <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return num

def normalize_side(value: str) -> str:
    lower = value.lower()
    if lower in {"buy", "long"}:
        return "buy"
    if lower in {"sell", "short"}:
        return "sell"
    raise ValueError('Side must be "buy", "sell", "long", or "short"')

def normalize_tif(value: str) -> str:
    mapping = {"gtc": "Gtc", "ioc": "Ioc", "alo": "Alo"}
    try:
        return mapping[value.lower()]
    except KeyError as exc:
        raise ValueError('Time-in-force must be "Gtc", "Ioc", or "Alo"') from exc
