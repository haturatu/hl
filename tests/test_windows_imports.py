import builtins
import importlib
import sys
import unittest
from unittest.mock import patch


class WindowsImportTests(unittest.TestCase):
    def test_markets_tui_import_does_not_require_posix_tty_modules(self):
        original_import = builtins.__import__

        def import_without_posix_tty(name, *args, **kwargs):
            if name in {"termios", "tty"}:
                raise ModuleNotFoundError(f"No module named {name!r}")
            return original_import(name, *args, **kwargs)

        sys.modules.pop("hl_cli.cli.markets_tui", None)
        with patch.object(builtins, "__import__", side_effect=import_without_posix_tty):
            module = importlib.import_module("hl_cli.cli.markets_tui")

        self.assertTrue(hasattr(module, "run_markets_tui"))


if __name__ == "__main__":
    unittest.main()
