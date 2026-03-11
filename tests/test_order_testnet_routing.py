import unittest

from hl_cli.commands.order import _wallet_perp_dexs_for_coin

class OrderTestnetRoutingTests(unittest.TestCase):
    def test_wallet_perp_dexs_for_coin(self):
        self.assertIsNone(_wallet_perp_dexs_for_coin("BTC"))
        self.assertEqual(_wallet_perp_dexs_for_coin("flx:NVDA"), ["flx"])

if __name__ == "__main__":
    unittest.main()
