import unittest
from unittest.mock import Mock, patch

from hl_cli.commands.markets import _safe_token_name
from hl_cli.core.context import CLIContext, Config, _build_info_client, _load_safe_spot_meta

class TestnetContextTests(unittest.TestCase):
    def test_safe_token_name_handles_invalid_indexes(self):
        tokens = [{"name": "USDC"}, {"name": "BTC"}]

        self.assertEqual(_safe_token_name(tokens, 1), "BTC")
        self.assertEqual(_safe_token_name(tokens, 2, "USD"), "USD")
        self.assertEqual(_safe_token_name(tokens, -1, "USD"), "USD")

    def test_load_safe_spot_meta_filters_invalid_token_refs(self):
        response = Mock()
        response.json.return_value = {
            "tokens": [{"name": "USDC"}, {"name": "BTC"}],
            "universe": [
                {"name": "BTC/USDC", "tokens": [1, 0]},
                {"name": "BROKEN/USDC", "tokens": [2, 0]},
                {"name": "SHORT", "tokens": [1]},
            ],
        }
        response.raise_for_status.return_value = None

        with patch("hl_cli.core.context.requests.post", return_value=response) as post:
            spot_meta = _load_safe_spot_meta("https://api.hyperliquid-testnet.xyz")

        self.assertEqual([x["name"] for x in spot_meta["universe"]], ["BTC/USDC"])
        post.assert_called_once()

    def test_get_public_client_falls_back_to_sanitized_spot_meta(self):
        config = Config(private_key=None, wallet_address=None, testnet=True, account=None)
        context = CLIContext(config)
        safe_spot_meta = {
            "tokens": [{"name": "USDC"}, {"name": "BTC"}],
            "universe": [{"name": "BTC/USDC", "tokens": [1, 0]}],
        }
        created = object()

        with patch("hl_cli.core.context.Info", side_effect=[IndexError("bad"), created]) as info_cls:
            with patch("hl_cli.core.context._load_safe_spot_meta", return_value=safe_spot_meta) as loader:
                client = context.get_public_client()

        self.assertIs(client, created)
        loader.assert_called_once_with(context.base_url)
        self.assertEqual(info_cls.call_args_list[0].kwargs, {"skip_ws": True})
        self.assertEqual(
            info_cls.call_args_list[1].kwargs,
            {"skip_ws": True, "spot_meta": safe_spot_meta},
        )

    def test_build_info_client_falls_back_to_sanitized_spot_meta(self):
        safe_spot_meta = {
            "tokens": [{"name": "USDC"}, {"name": "BTC"}],
            "universe": [{"name": "BTC/USDC", "tokens": [1, 0]}],
        }
        created = object()

        with patch("hl_cli.core.context.Info", side_effect=[IndexError("bad"), created]) as info_cls:
            with patch("hl_cli.core.context._load_safe_spot_meta", return_value=safe_spot_meta) as loader:
                client = _build_info_client("https://api.hyperliquid-testnet.xyz", skip_ws=True)

        self.assertIs(client, created)
        loader.assert_called_once_with("https://api.hyperliquid-testnet.xyz")
        self.assertEqual(info_cls.call_args_list[1].kwargs, {"skip_ws": True, "spot_meta": safe_spot_meta})

if __name__ == "__main__":
    unittest.main()
