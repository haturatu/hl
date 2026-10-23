"""Measure public-read paths with real SDK clients and simulated API latency.

Run with PYTHONPATH=src python tools/benchmark_public_reads.py. No API calls,
account configuration, private keys, or local account database are used.
"""

import argparse
import asyncio
from collections import Counter
import cProfile
import hashlib
import json
from statistics import median
import time
from unittest.mock import patch

from hyperliquid.info import Info

from hl_cli.commands.markets import _build_market_rows_async
from hl_cli.core.context import CLIContext, Config


class SimulatedAPI:
    def __init__(self, delay: float):
        self.delay = delay
        self.calls: list[str] = []

    def post(self, _info, _path, payload):
        kind = payload["type"]
        self.calls.append(kind)
        time.sleep(self.delay)
        if kind == "perpDexs":
            return [None, {"name": "alpha"}, {"name": "beta"}]
        if kind == "perpCategories":
            return [["BTC", "crypto"], ["alpha:XYZ", "stocks"]]
        spot_meta = {
            "tokens": [
                {"name": "USDC", "index": 0, "szDecimals": 6},
                {"name": "BTC", "index": 1, "szDecimals": 5},
            ],
            "universe": [{"name": "@1", "index": 1, "tokens": [1, 0]}],
        }
        coin = f"{payload['dex']}:XYZ" if payload.get("dex") else "BTC"
        perp_meta = {
            "collateralToken": 0,
            "universe": [{"name": coin, "szDecimals": 5, "maxLeverage": 20}],
        }
        asset_ctx = {
            "coin": "@1",
            "markPx": "100",
            "prevDayPx": "80",
            "dayNtlVlm": "1000",
            "funding": "0.0001",
            "openInterest": "5",
        }
        if kind == "spotMeta":
            return spot_meta
        if kind == "meta":
            return perp_meta
        if kind == "spotMetaAndAssetCtxs":
            return spot_meta, [asset_ctx]
        if kind == "metaAndAssetCtxs":
            return perp_meta, [asset_ctx]
        raise AssertionError(payload)


def measure(name: str, *, delay: float, repeat: int) -> dict:
    durations = []
    counts = None
    digest = None
    for _ in range(repeat):
        api = SimulatedAPI(delay)
        context = CLIContext(Config(None, None, name.startswith("testnet"), None))
        with patch.object(Info, "post", lambda *args: api.post(*args)):
            if name == "dexs_warm":
                context.get_public_client()
                api.calls.clear()
            start = time.perf_counter()
            if name.startswith("dexs_"):
                result = context.get_perp_dexs()
            else:
                result = asyncio.run(
                    _build_market_rows_async(
                        context, name.endswith("spot_only"), name.endswith("perp_only")
                    )
                )
            durations.append(time.perf_counter() - start)
        current_counts = dict(sorted(Counter(api.calls).items()))
        current_digest = hashlib.sha256(
            json.dumps(result, sort_keys=True).encode()
        ).hexdigest()
        if counts is not None:
            assert counts == current_counts
            assert digest == current_digest
        counts, digest = current_counts, current_digest
    return {
        "scenario": name,
        "median_ms": round(median(durations) * 1000, 3),
        "requests": counts,
        "output_sha256": digest,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delay", type=float, default=0.02)
    parser.add_argument("--repeat", type=int, default=7)
    parser.add_argument("--profile", help="Write a cProfile file")
    args = parser.parse_args()
    if args.delay < 0 or args.repeat < 1:
        parser.error("delay must be nonnegative and repeat must be positive")
    profiler = cProfile.Profile() if args.profile else None
    if profiler:
        profiler.enable()
    results = [
        measure(name, delay=args.delay, repeat=args.repeat)
        for name in (
            "dexs_cold",
            "dexs_warm",
            "testnet_all",
            "mainnet_all",
            "mainnet_spot_only",
            "mainnet_perp_only",
        )
    ]
    if profiler:
        profiler.disable()
        profiler.dump_stats(args.profile)
    print(
        json.dumps(
            {"delay_ms": args.delay * 1000, "repeat": args.repeat, "results": results},
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
