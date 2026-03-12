import pathlib
import os
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class CompletionTests(unittest.TestCase):
    def test_bash_completion_command_prints_script(self):
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "src")
        result = subprocess.run(
            [sys.executable, "-m", "hl_cli.cli.argparse_main", "completion", "bash"],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )

        self.assertIn("_hl_completion()", result.stdout)
        self.assertIn("complete -F _hl_completion hl", result.stdout)
        self.assertIn('"order")', result.stdout)
        self.assertIn('"order market")', result.stdout)
        self.assertIn("buy sell long short close", result.stdout)
        self.assertIn("--json --testnet -h --help --reduce-only --slippage", result.stdout)
        self.assertIn("--spot-only --perp-only --category --sort-by -w --watch", result.stdout)


if __name__ == "__main__":
    unittest.main()
