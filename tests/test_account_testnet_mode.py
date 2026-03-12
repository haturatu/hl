import asyncio
import unittest
from unittest.mock import patch

from hl_cli.commands.account import (
    _account_perp_dexs,
    _fetch_portfolio_async,
    _fetch_positions_async,
)


class _FakeInfo:
    def __init__(self):
        self.user_state_calls = []

    def user_state(self, user, dex=""):
        self.user_state_calls.append((user, dex))
        return {
            "assetPositions": [],
            "marginSummary": {"accountValue": "1.0", "totalMarginUsed": "0.0"},
        }

    def spot_user_state(self, _user):
        return {"balances": []}


class _FakeConfig:
    def __init__(self, testnet):
        self.testnet = testnet


class _FakeContext:
    def __init__(self, testnet):
        self.config = _FakeConfig(testnet)
        self.info = _FakeInfo()

    def get_public_client(self):
        return self.info

    def get_perp_dexs(self):
        return ["", "flx", "test"]


class AccountTestnetModeTests(unittest.TestCase):
    def test_account_perp_dexs_uses_main_perp_only_on_testnet(self):
        self.assertEqual(_account_perp_dexs(_FakeContext(True)), [""])
        self.assertEqual(_account_perp_dexs(_FakeContext(False)), ["", "flx", "test"])

    def test_fetch_positions_uses_main_perp_only_on_testnet(self):
        context = _FakeContext(True)

        async def fake_to_thread(func, /, *args, **kwargs):
            return func(*args, **kwargs)

        with patch(
            "hl_cli.services.account_fetch.asyncio.to_thread",
            side_effect=fake_to_thread,
        ):
            asyncio.run(_fetch_positions_async(context, "0xabc"))

        self.assertEqual(context.info.user_state_calls, [("0xabc", "")])

    def test_fetch_portfolio_uses_main_perp_only_on_testnet(self):
        context = _FakeContext(True)

        async def fake_to_thread(func, /, *args, **kwargs):
            return func(*args, **kwargs)

        with patch(
            "hl_cli.services.account_fetch.asyncio.to_thread",
            side_effect=fake_to_thread,
        ):
            asyncio.run(_fetch_portfolio_async(context, "0xabc"))

        self.assertEqual(context.info.user_state_calls, [("0xabc", "")])


if __name__ == "__main__":
    unittest.main()
