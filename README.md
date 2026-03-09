# Hyperliquid CLI (Python)

## Installation

```bash
cd hl
make install
```

After installation, the `hl` command is available:

```bash
hl --help
```

Manual install is still available:

```bash
pip install .
```

## Bash Completion

`hl` can print a Bash completion script for top-level commands and subcommands.

`make install` installs the package and appends a managed completion line to
`~/.bashrc` if it is not already present.

Enable it for the current shell:

```bash
eval "$(hl completion bash)"
```

Persist it in `~/.bashrc`:

```bash
echo 'eval "$(hl completion bash)"' >> ~/.bashrc
```

`make uninstall` removes the managed completion line from `~/.bashrc`.

`pip install .` alone still does not edit shell startup files automatically.

To remove both the package and the managed `~/.bashrc` completion line:

```bash
make uninstall
```

## Global Options

- `--json` Output JSON
- `--testnet` Use testnet

## Supported Commands

- `hl account add|ls|set-default|remove`
- `hl account positions|orders|balances|portfolio`
- `hl order ls|limit|market|tpsl|twap|twap-cancel|cancel|cancel-all|set-leverage|configure`
- `hl asset price|book|leverage`
- `hl markets ls|search`
- `hl referral set|status`

## Configuration

- DB: `~/.hl/hl.db`
- Order config: `~/.hl/order-config.json`

Environment variable fallback (when DB account is not configured):

- `HYPERLIQUID_PRIVATE_KEY`
- `HYPERLIQUID_WALLET_ADDRESS`

## Security Notes

In this repository, API keys and private keys are read from the `HYPERLIQUID_PRIVATE_KEY`
environment variable or stored in `~/.hl/hl.db`.

I considered encrypting this data, but concluded that it is difficult to do so
effectively without hurting the user experience. A Unix-style approach like
WireGuard, which relies on root privileges for secret key handling, does not fit
well here: requiring `sudo` for every `hl` invocation is not practical, and using
root privileges just to run `hl` is not a good tradeoff.

Another possible approach would be to keep the secret key in memory, but that would
effectively require turning this application into a daemon, which seems excessive.
If the `hl` command must be able to decrypt the key at execution time, then that
decryption step can usually be bypassed in practice anyway, so the actual security
benefit is limited.

Because of that, the practical security guidance I can give is:

- Use wallets or API keys that would not be catastrophic if leaked
- Restrict which OS user can run this tool
- If you need stronger protection, use disk encryption as the higher-level control

I also considered using `age` for encryption, but invoking it via `subprocess` does
not seem like a fundamental improvement, even if making `age` a required dependency
would be acceptable.

## Run for Development

```bash
cd hl
PYTHONPATH=src python -m hl_cli.cli.argparse_main --help
```

## JSON Pattern Tests

`tests/` validates that every subcommand pattern produces parseable raw JSON output in `--json` mode.

```bash
cd hl
PYTHONPATH=src python -m unittest -v tests.test_json_patterns
```

## TWAP Orders

`hyperliquid-python-sdk` does not provide a high-level TWAP method, so this CLI signs and submits the official
`exchange` actions `twapOrder` / `twapCancel`.

```bash
# 30-minute native TWAP
hl order twap buy 1.0 BTC 30

# Derive total TWAP size from USD margin (stake * leverage)
hl order twap buy 0 BTC 30 --stake 5

# Compatibility format: 5,10 is sent as total 50 minutes
hl order twap sell 2.0 ETH 5,10 --randomize

# Cancel TWAP
hl order twap-cancel BTC 12345
```

## Stake-Based Orders

`--stake` is treated as USD margin. Position value is calculated as:
`position_notional = stake * leverage` (or `stake * 1` if `--leverage` is omitted).

```bash
# $50 margin, 20x leverage => about $1,000 BTC position notional
hl order limit buy BTC 65000 --stake 50 --leverage 20 --cross

# $50 margin, 20x leverage market long
hl order market buy BTC --stake 50 --leverage 20 --isolated

# Set leverage and margin mode at order time
hl order limit buy BTC 65000 --stake 50 --leverage 20 --cross
hl order market buy BTC --stake 50 --leverage 20 --isolated

# Set leverage directly
hl order set-leverage BTC 20 --cross

# If leverage is invalid, show warning and retry with coin maxLeverage from /info type=meta
hl order set-leverage BTC 60

# Close full position by coin
hl order market close ETH
hl order market close xyz:TSLA

# Close 50% of a position
hl order market close ETH --ratio 0.5

# Set TP/SL trigger orders for an open position
hl order tpsl ETH --tp 1900 --sl 1800
hl order tpsl ETH --sl 1800 --ratio 0.5
```

## Acknowledgments

- https://app.hyperliquid.xyz/
- https://github.com/chrisling-dev/hyperliquid-cli
- https://github.com/ehfuzzz/hyperliquid-CLI

This project is primarily a Python implementation of
https://github.com/chrisling-dev/hyperliquid-cli.
Some features, including the TWAP order implementation, are also based on ideas from
https://github.com/ehfuzzz/hyperliquid-CLI.

This repository also includes changes such as expanded `order` subcommands, the
`--stake` option, and additional `market` subcommand functionality.

At the moment, I am not fully sure how this should be handled from a licensing and
attribution perspective, so this repository is being published under my BSD 3-Clause
License as a temporary choice. If you have a better idea for the appropriate license
notice or attribution, please open an issue.
