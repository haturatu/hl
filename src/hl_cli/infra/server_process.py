import argparse
import json
import os
import signal
import sys
import time
from datetime import datetime, timezone

from hyperliquid.info import Info
from hyperliquid.utils.constants import MAINNET_API_URL, TESTNET_API_URL

from .paths import HL_DIR, SERVER_CACHE_PATH, SERVER_LOG_PATH, SERVER_PID_PATH, SERVER_STATE_PATH


_running = True


def _log(message: str) -> None:
    HL_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat()
    with SERVER_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(f"[{ts}] {message}\n")


def _shutdown(signum: int, _frame) -> None:  # type: ignore[no-untyped-def]
    global _running
    _running = False
    _log(f"received signal={signum}; shutting down")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--testnet", action="store_true")
    args = parser.parse_args()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    HL_DIR.mkdir(parents=True, exist_ok=True)
    SERVER_PID_PATH.write_text(str(os.getpid()), encoding="utf-8")

    base_url = TESTNET_API_URL if args.testnet else MAINNET_API_URL
    started_at = int(time.time() * 1000)
    SERVER_STATE_PATH.write_text(
        json.dumps({"testnet": args.testnet, "startedAt": started_at, "connected": True}, indent=2),
        encoding="utf-8",
    )

    _log(f"server started pid={os.getpid()} testnet={args.testnet}")
    info = Info(base_url, skip_ws=True)

    while _running:
        try:
            mids = info.all_mids()
            perp_meta, perp_ctxs = info.meta_and_asset_ctxs()
            spot_meta, spot_ctxs = info.spot_meta_and_asset_ctxs()

            cache = {
                "updatedAt": int(time.time() * 1000),
                "allMids": mids,
                "allPerpMetas": [perp_meta],
                "allDexsAssetCtxs": {"ctxs": [["", perp_ctxs]]},
                "spotMeta": spot_meta,
                "spotAssetCtxs": spot_ctxs,
            }
            SERVER_CACHE_PATH.write_text(json.dumps(cache), encoding="utf-8")

            state = {
                "running": True,
                "testnet": args.testnet,
                "connected": True,
                "startedAt": started_at,
                "uptime": int(time.time() * 1000) - started_at,
                "cache": {
                    "hasMids": True,
                    "hasAssetCtxs": True,
                    "hasPerpMetas": True,
                    "hasSpotMeta": True,
                    "hasSpotAssetCtxs": True,
                    "midsAge": 0,
                    "assetCtxsAge": 0,
                    "perpMetasAge": 0,
                    "spotMetaAge": 0,
                    "spotAssetCtxsAge": 0,
                },
            }
            SERVER_STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            _log(f"update error: {exc}")
            time.sleep(2.0)
            continue

        time.sleep(1.0)

    for path in (SERVER_PID_PATH,):
        try:
            if path.exists():
                path.unlink()
        except OSError:
            pass
    _log("server stopped")


if __name__ == "__main__":
    main()
