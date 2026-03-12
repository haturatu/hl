import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Optional

from .paths import HL_DIR

TWAP_REGISTRY_PATH = HL_DIR / "twap_orders.json"


@dataclass
class TwapRecord:
    network: str
    user: str
    coin: str
    resolved_coin: str
    twap_id: int
    side: str
    total_size: float
    duration_minutes: int
    randomize: bool
    reduce_only: bool
    submitted_at: str
    status: str = "active"
    cancelled_at: Optional[str] = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_all() -> list[TwapRecord]:
    if not TWAP_REGISTRY_PATH.exists():
        return []
    try:
        raw = json.loads(TWAP_REGISTRY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(raw, list):
        return []
    records: list[TwapRecord] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            records.append(TwapRecord(**item))
        except TypeError:
            continue
    return records


def _save_all(records: list[TwapRecord]) -> None:
    HL_DIR.mkdir(parents=True, exist_ok=True)
    TWAP_REGISTRY_PATH.write_text(
        json.dumps(
            [asdict(record) for record in records], ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
    )


def register_twap_order(
    *,
    network: str,
    user: str,
    coin: str,
    resolved_coin: str,
    twap_id: int,
    side: str,
    total_size: float,
    duration_minutes: int,
    randomize: bool,
    reduce_only: bool,
) -> None:
    records = _load_all()
    records = [
        record
        for record in records
        if not (
            record.network == network
            and record.user.lower() == user.lower()
            and record.twap_id == twap_id
            and record.resolved_coin == resolved_coin
        )
    ]
    records.append(
        TwapRecord(
            network=network,
            user=user,
            coin=coin,
            resolved_coin=resolved_coin,
            twap_id=twap_id,
            side=side,
            total_size=total_size,
            duration_minutes=duration_minutes,
            randomize=randomize,
            reduce_only=reduce_only,
            submitted_at=_utc_now(),
        )
    )
    _save_all(records)


def list_twap_orders(
    *, network: str, user: str, active_only: bool = True
) -> list[TwapRecord]:
    records = [
        record
        for record in _load_all()
        if record.network == network and record.user.lower() == user.lower()
    ]
    if active_only:
        records = [record for record in records if record.status == "active"]
    records.sort(key=lambda record: record.submitted_at, reverse=True)
    return records


def find_twap_order(
    *,
    network: str,
    user: str,
    twap_id: int,
    coin: Optional[str] = None,
) -> Optional[TwapRecord]:
    for record in list_twap_orders(network=network, user=user, active_only=False):
        if record.twap_id != twap_id:
            continue
        if coin and coin not in {record.coin, record.resolved_coin}:
            continue
        return record
    return None


def mark_twap_cancelled(
    *, network: str, user: str, twap_id: int, coin: Optional[str] = None
) -> None:
    records = _load_all()
    updated = False
    for record in records:
        if record.network != network or record.user.lower() != user.lower():
            continue
        if record.twap_id != twap_id:
            continue
        if coin and coin not in {record.coin, record.resolved_coin}:
            continue
        record.status = "cancelled"
        record.cancelled_at = _utc_now()
        updated = True
    if updated:
        _save_all(records)
