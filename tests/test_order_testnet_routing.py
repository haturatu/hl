import unittest

from hl_cli.commands.order import _close_position_perp_dexs, _wallet_perp_dexs_for_coin


class _FakeConfig:
    def __init__(self, testnet):
        self.testnet = testnet


class _FakeContext:
    def __init__(self, testnet):
        self.config = _FakeConfig(testnet)

    def get_perp_dexs(self):
        return ["", "flx", "test"]


class OrderTestnetRoutingTests(unittest.TestCase):
    def test_wallet_perp_dexs_for_coin(self):
        self.assertIsNone(_wallet_perp_dexs_for_coin("BTC"))
        self.assertEqual(_wallet_perp_dexs_for_coin("flx:NVDA"), ["flx"])

    def test_close_position_perp_dexs_uses_main_perp_only_on_testnet(self):
        self.assertEqual(_close_position_perp_dexs(_FakeContext(True)), [""])
        self.assertEqual(_close_position_perp_dexs(_FakeContext(False)), ["", "flx", "test"])

if __name__ == "__main__":
    unittest.main()
