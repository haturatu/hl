import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hl_cli.infra import db


class AccountNetworkTests(unittest.TestCase):
    def test_accounts_are_scoped_by_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("hl_cli.infra.db.HL_DIR", root), patch("hl_cli.infra.db.DB_PATH", root / "hl.db"):
                main = db.create_account(
                    alias="main",
                    network="mainnet",
                    user_address="0x1111111111111111111111111111111111111111",
                    account_type="readonly",
                    set_as_default=True,
                )
                test = db.create_account(
                    alias="test",
                    network="testnet",
                    user_address="0x2222222222222222222222222222222222222222",
                    account_type="readonly",
                    set_as_default=True,
                )

                self.assertEqual(db.get_default_account("mainnet").id, main.id)
                self.assertEqual(db.get_default_account("testnet").id, test.id)
                self.assertEqual([x.alias for x in db.get_all_accounts("mainnet")], ["main"])
                self.assertEqual([x.alias for x in db.get_all_accounts("testnet")], ["test"])
                self.assertFalse(db.is_alias_taken("main", "testnet"))

    def test_set_default_and_delete_only_affect_target_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("hl_cli.infra.db.HL_DIR", root), patch("hl_cli.infra.db.DB_PATH", root / "hl.db"):
                db.create_account(
                    alias="m1",
                    network="mainnet",
                    user_address="0x1111111111111111111111111111111111111111",
                    account_type="readonly",
                    set_as_default=True,
                )
                db.create_account(
                    alias="m2",
                    network="mainnet",
                    user_address="0x2222222222222222222222222222222222222222",
                    account_type="readonly",
                )
                db.create_account(
                    alias="t1",
                    network="testnet",
                    user_address="0x3333333333333333333333333333333333333333",
                    account_type="readonly",
                    set_as_default=True,
                )

                db.set_default_account("m2", "mainnet")
                self.assertEqual(db.get_default_account("mainnet").alias, "m2")
                self.assertEqual(db.get_default_account("testnet").alias, "t1")

                db.delete_account("m2", "mainnet")
                self.assertEqual(db.get_default_account("mainnet").alias, "m1")
                self.assertEqual(db.get_default_account("testnet").alias, "t1")


if __name__ == "__main__":
    unittest.main()
