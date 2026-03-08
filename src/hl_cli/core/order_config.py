import json

from ..infra.paths import HL_DIR, ORDER_CONFIG_PATH

DEFAULT_CONFIG = {"slippage": 1.0}


def get_order_config() -> dict:
    if not ORDER_CONFIG_PATH.exists():
        return dict(DEFAULT_CONFIG)
    try:
        data = json.loads(ORDER_CONFIG_PATH.read_text())
        return {**DEFAULT_CONFIG, **data}
    except Exception:
        return dict(DEFAULT_CONFIG)


def update_order_config(**updates: object) -> dict:
    HL_DIR.mkdir(parents=True, exist_ok=True)
    cfg = get_order_config()
    cfg.update(updates)
    ORDER_CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
    return cfg
