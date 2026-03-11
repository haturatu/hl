import unittest
from unittest.mock import patch

from hl_cli.commands.order import (
    _close_position_perp_dexs,
    _resolve_coin_for_side,
    _resolve_spot_coin,
    _validate_side_mode_args,
    _wallet_perp_dexs_for_coin,
)


class _FakeConfig:
    def __init__(self, testnet):
        self.testnet = testnet


class _FakeContext:
    def __init__(self, testnet):
        self.config = _FakeConfig(testnet)
        self.info = None

    def get_perp_dexs(self):
        return ["", "flx", "test"]

    def get_public_client(self):
        return self.info


class _FakeInfo:
    def all_mids(self, dex=""):
        return {"@1035": "88.0", "BTC/USDC": "70000.0"}

    def spot_meta(self):
        return {
            "tokens": [
                {"index": 0, "name": "USDC"},
                {"index": 1035, "name": "HYPE", "fullName": "HYPE"},
            ],
            "universe": [
                {"name": "HYPE/USDC", "tokens": [1035, 0]},
            ],
        }


class OrderTestnetRoutingTests(unittest.TestCase):
    def test_wallet_perp_dexs_for_coin(self):
        self.assertIsNone(_wallet_perp_dexs_for_coin("BTC"))
        self.assertEqual(_wallet_perp_dexs_for_coin("flx:NVDA"), ["flx"])

    def test_close_position_perp_dexs_uses_main_perp_only_on_testnet(self):
        self.assertEqual(_close_position_perp_dexs(_FakeContext(True)), [""])
        self.assertEqual(_close_position_perp_dexs(_FakeContext(False)), ["", "flx", "test"])

    def test_buy_sell_resolve_spot_and_long_short_resolve_perp(self):
        context = _FakeContext(False)
        with (
            patch("hl_cli.commands.order._resolve_spot_coin", return_value="BTC/USDC") as resolve_spot,
            patch("hl_cli.commands.order._resolve_perp_coin", return_value="BTC") as resolve_perp,
        ):
            self.assertEqual(_resolve_coin_for_side(context, "buy", "BTC"), "BTC/USDC")
            self.assertEqual(_resolve_coin_for_side(context, "sell", "BTC"), "BTC/USDC")
            self.assertEqual(_resolve_coin_for_side(context, "long", "BTC"), "BTC")
            self.assertEqual(_resolve_coin_for_side(context, "short", "BTC"), "BTC")
        self.assertEqual(resolve_spot.call_count, 2)
        self.assertEqual(resolve_perp.call_count, 2)

    def test_spot_sides_reject_perp_only_flags(self):
        with self.assertRaisesRegex(RuntimeError, "only supported with long/short"):
            _validate_side_mode_args(side="buy", leverage=5, cross=False, isolated=False)
        with self.assertRaisesRegex(RuntimeError, "only supported with long/short"):
            _validate_side_mode_args(side="sell", leverage=None, cross=True, isolated=False)
        with self.assertRaisesRegex(RuntimeError, "only supported with long/short"):
            _validate_side_mode_args(side="buy", leverage=None, cross=False, isolated=False, reduce_only=True)

    def test_resolve_spot_coin_accepts_index_symbol(self):
        context = _FakeContext(False)
        context.info = _FakeInfo()
        self.assertEqual(_resolve_spot_coin(context, "@1035"), "@1035")

if __name__ == "__main__":
    unittest.main()
