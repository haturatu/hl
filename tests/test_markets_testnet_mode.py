import asyncio
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from hl_cli.commands.markets import _build_market_rows_async


class _FakeInfo:
    def spot_meta_and_asset_ctxs(self):
        return (
            {
                "tokens": [{"name": "USDC"}, {"name": "BTC"}],
                "universe": [{"name": "BTC/USDC", "tokens": [1, 0]}],
            },
            [
                {
                    "coin": "BTC/USDC",
                    "markPx": "100",
                    "prevDayPx": "90",
                    "dayNtlVlm": "1",
                }
            ],
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
            [
                {
                    "markPx": "100",
                    "prevDayPx": "90",
                    "dayNtlVlm": "10",
                    "funding": "0.1",
                    "openInterest": "2",
                }
            ],
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
    def test_spot_only_skips_all_perp_requests_on_both_networks(self):
        for testnet in (False, True):
            with self.subTest(testnet=testnet):
                info = Mock(wraps=_FakeInfo())
                info.post.side_effect = AssertionError("unused perp request")
                info.meta_and_asset_ctxs.side_effect = AssertionError(
                    "unused perp request"
                )
                context = SimpleNamespace(
                    config=SimpleNamespace(testnet=testnet),
                    get_public_client=lambda info=info: info,
                    get_perp_dexs=Mock(side_effect=AssertionError("unused discovery")),
                )
                rows = asyncio.run(_build_market_rows_async(context, True, False))
                self.assertEqual(rows["perpMarkets"], [])
                self.assertEqual(rows["spotMarkets"][0]["pairName"], "[Spot] BTC/USDC")
                info.spot_meta_and_asset_ctxs.assert_called_once_with()
                info.post.assert_not_called()
                info.meta_and_asset_ctxs.assert_not_called()
                context.get_perp_dexs.assert_not_called()

    def test_independent_fetches_overlap_and_builder_order_is_preserved(self):
        initial_fetches = threading.Barrier(4)
        builder_fetches = threading.Barrier(2)
        beta_finished = threading.Event()
        event_loop_thread = threading.get_ident()
        discovery_threads = []

        class ConcurrentInfo(_FakeInfo):
            def spot_meta_and_asset_ctxs(self):
                initial_fetches.wait(timeout=5)
                return super().spot_meta_and_asset_ctxs()

            def meta_and_asset_ctxs(self):
                initial_fetches.wait(timeout=5)
                return super().meta_and_asset_ctxs()

            def post(self, _path, payload):
                if payload == {"type": "perpCategories"}:
                    initial_fetches.wait(timeout=5)
                    return [["BTC", "crypto"], ["alpha:XYZ", "stocks"]]
                if payload.get("type") == "metaAndAssetCtxs":
                    builder_fetches.wait(timeout=5)
                    dex = payload["dex"]
                    if dex == "alpha":
                        if not beta_finished.wait(timeout=5):
                            raise AssertionError("builder fetches did not overlap")
                    else:
                        beta_finished.set()
                    meta, ctxs = super().meta_and_asset_ctxs()
                    meta["collateralToken"] = 1
                    meta["universe"][0]["name"] = f"{dex}:XYZ"
                    return meta, ctxs
                raise AssertionError(payload)

        def discover_dexs():
            discovery_threads.append(threading.get_ident())
            initial_fetches.wait(timeout=5)
            return ["", "alpha", "beta"]

        context = SimpleNamespace(
            config=SimpleNamespace(testnet=False),
            get_public_client=lambda: ConcurrentInfo(),
            get_perp_dexs=Mock(side_effect=discover_dexs),
        )
        rows = asyncio.run(_build_market_rows_async(context, False, False))
        self.assertEqual(len(discovery_threads), 1)
        self.assertNotEqual(discovery_threads[0], event_loop_thread)
        context.get_perp_dexs.assert_called_once_with()
        self.assertEqual(
            [r["coin"] for r in rows["perpMarkets"]], ["BTC", "alpha:XYZ", "beta:XYZ"]
        )
        self.assertEqual(
            [r["category"] for r in rows["perpMarkets"]], ["crypto", "stocks", None]
        )
        self.assertEqual(
            [r["pairName"] for r in rows["perpMarkets"]],
            ["BTC/USDC 40x", "alpha:XYZ/BTC 40x", "beta:XYZ/BTC 40x"],
        )
        for row in rows["perpMarkets"]:
            self.assertEqual(row["price"], "100")
            self.assertAlmostEqual(row["priceChange"], 100 / 9)
            self.assertEqual(row["openInterestUsd"], 200)
        self.assertEqual([r["coin"] for r in rows["spotMarkets"]], ["BTC/USDC"])

    def test_perp_only_keeps_spot_metadata_for_collateral_name(self):
        info = Mock(wraps=_FakeInfo())
        context = SimpleNamespace(
            config=SimpleNamespace(testnet=True),
            get_public_client=lambda: info,
            get_perp_dexs=Mock(side_effect=AssertionError("testnet discovery")),
        )
        rows = asyncio.run(_build_market_rows_async(context, False, True))
        self.assertEqual(rows["spotMarkets"], [])
        self.assertEqual(rows["perpMarkets"][0]["pairName"], "BTC/USDC 40x")
        info.spot_meta_and_asset_ctxs.assert_called_once_with()
        context.get_perp_dexs.assert_not_called()

    def test_snapshot_errors_propagate(self):
        info = Mock(wraps=_FakeInfo())
        info.meta_and_asset_ctxs.side_effect = RuntimeError("snapshot unavailable")
        context = SimpleNamespace(
            config=SimpleNamespace(testnet=True),
            get_public_client=lambda: info,
        )
        with self.assertRaisesRegex(RuntimeError, "snapshot unavailable"):
            asyncio.run(_build_market_rows_async(context, False, False))

    def test_testnet_skips_builder_perp_fetches(self):
        async def fake_to_thread(func, /, *args, **kwargs):
            return func(*args, **kwargs)

        with patch(
            "hl_cli.commands.markets.asyncio.to_thread", side_effect=fake_to_thread
        ):
            rows = asyncio.run(_build_market_rows_async(_FakeContext(), False, False))

        self.assertEqual([x["coin"] for x in rows["perpMarkets"]], ["BTC"])
        self.assertEqual([x["coin"] for x in rows["spotMarkets"]], ["BTC/USDC"])


if __name__ == "__main__":
    unittest.main()
