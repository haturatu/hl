import io
import json
import pathlib
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from hl_cli.cli import argparse_main


async def _call_stub(fn, *_args, **_kwargs):
    print(json.dumps({"ok": fn.__name__}))


class JsonPatternTests(unittest.TestCase):
    def _run(self, argv):
        buf = io.StringIO()
        with patch.object(argparse_main, "_call", _call_stub):
            with redirect_stdout(buf):
                argparse_main.main(argv)
        lines = [x.strip() for x in buf.getvalue().splitlines() if x.strip()]
        self.assertTrue(lines, f"no output for argv={argv}")
        return json.loads(lines[-1])

    def test_all_leaf_commands_emit_json(self):
        patterns = [
            (["--json", "account", "add"], "account_add"),
            (["--json", "account", "ls"], "account_ls"),
            (["--json", "account", "set-default", "main"], "account_set_default"),
            (["--json", "account", "remove", "main", "--force"], "account_remove"),
            (["--json", "account", "positions"], "account_positions"),
            (["--json", "account", "orders"], "account_orders"),
            (["--json", "account", "balances"], "_account_balances_async"),
            (["--json", "account", "portfolio"], "_account_portfolio_async"),
            (["--json", "order", "ls"], "order_ls"),
            (["--json", "order", "limit", "buy", "0.01", "BTC", "65000"], "order_limit"),
            (["--json", "order", "limit", "buy", "BTC", "65000", "--stake", "50"], "order_limit"),
            (["--json", "order", "market", "buy", "0.01", "BTC"], "order_market"),
            (["--json", "order", "market", "buy", "BTC", "--stake", "50"], "order_market"),
            (["--json", "order", "market", "close", "ETH"], "order_market_close"),
            (["--json", "order", "market", "close", "ETH", "--ratio", "0.5"], "order_market_close"),
            (["--json", "order", "twap", "buy", "1", "BTC", "30"], "order_twap"),
            (["--json", "order", "twap-cancel", "BTC", "12345"], "order_twap_cancel"),
            (["--json", "order", "cancel", "123"], "order_cancel"),
            (["--json", "order", "cancel-all", "--yes"], "order_cancel_all"),
            (["--json", "order", "set-leverage", "BTC", "10", "--cross"], "order_set_leverage"),
            (["--json", "order", "tpsl", "ETH", "--tp", "1900", "--sl", "1800"], "order_tpsl"),
            (["--json", "order", "configure", "--slippage", "0.8"], "order_configure"),
            (["--json", "asset", "price", "BTC"], "asset_price"),
            (["--json", "asset", "book", "BTC"], "asset_book"),
            (["--json", "asset", "leverage", "BTC"], "asset_leverage"),
            (["--json", "markets", "ls"], "markets_ls"),
            (["--json", "markets", "search", "ORCL"], "markets_search"),
            (["--json", "referral", "set", "MYCODE"], "referral_set"),
            (["--json", "referral", "status"], "referral_status"),
            (["--json", "server", "start"], "server_start"),
            (["--json", "server", "stop"], "server_stop"),
            (["--json", "server", "status"], "server_status"),
        ]

        for argv, expected in patterns:
            with self.subTest(argv=argv):
                payload = self._run(argv)
                self.assertEqual(payload.get("ok"), expected)


if __name__ == "__main__":
    unittest.main()
