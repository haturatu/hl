import asyncio
import unittest
from unittest.mock import patch

from hl_cli.commands.markets import _build_market_rows_async


class _FakeInfo:
    def spot_meta_and_asset_ctxs(self):
        return (
            {
                "tokens": [{"name": "USDC"}, {"name": "BTC"}],
                "universe": [{"name": "BTC/USDC", "tokens": [1, 0]}],
            },
            [{"coin": "BTC/USDC", "markPx": "100", "prevDayPx": "90", "dayNtlVlm": "1"}],
        )

    def post(self, _path, payload):
        if payload == {"type": "perpCategories"}:
            return []
        raise AssertionError(payload)

    def meta_and_asset_ctxs(self):
        return (
            {
                "collateralToken": 0,
                "universe": [{"name": "BTC", "maxLeverage": 40}],
            },
            [{"markPx": "100", "prevDayPx": "90", "dayNtlVlm": "10", "funding": "0.1", "openInterest": "2"}],
        )


class _FakeConfig:
    testnet = True


class _FakeContext:
    config = _FakeConfig()

    def get_public_client(self):
        return _FakeInfo()

    def get_perp_dexs(self):
        raise AssertionError("builder perps should be skipped on testnet")


class MarketsTestnetModeTests(unittest.TestCase):
    def test_testnet_skips_builder_perp_fetches(self):
        async def fake_to_thread(func, /, *args, **kwargs):
            return func(*args, **kwargs)

        with patch("hl_cli.commands.markets.asyncio.to_thread", side_effect=fake_to_thread):
            rows = asyncio.run(_build_market_rows_async(_FakeContext(), False, False))

        self.assertEqual([x["coin"] for x in rows["perpMarkets"]], ["BTC"])
        self.assertEqual([x["coin"] for x in rows["spotMarkets"]], ["BTC/USDC"])


if __name__ == "__main__":
    unittest.main()
