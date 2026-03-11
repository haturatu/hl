import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hl_cli.infra import twap_registry


class TwapRegistryTests(unittest.TestCase):
    def test_register_list_find_and_cancel(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "twap_orders.json"
            with patch.object(twap_registry, "TWAP_REGISTRY_PATH", path):
                twap_registry.register_twap_order(
                    network="testnet",
                    user="0xabc",
                    coin="BTC",
                    resolved_coin="BTC",
                    twap_id=123,
                    side="buy",
                    total_size=1.25,
                    duration_minutes=10,
                    randomize=False,
                    reduce_only=False,
                )

                active = twap_registry.list_twap_orders(network="testnet", user="0xabc")
                self.assertEqual(len(active), 1)
                self.assertEqual(active[0].twap_id, 123)

                record = twap_registry.find_twap_order(network="testnet", user="0xabc", twap_id=123)
                self.assertIsNotNone(record)
                self.assertEqual(record.resolved_coin, "BTC")

                twap_registry.mark_twap_cancelled(network="testnet", user="0xabc", twap_id=123)
                self.assertEqual(twap_registry.list_twap_orders(network="testnet", user="0xabc"), [])

                raw = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(raw[0]["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
