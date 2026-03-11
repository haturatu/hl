# Hyperliquid CLI (Python)

# Requirements

- Python 3.10+
- `hyperliquid-python-sdk` for API interactions
- `rich` for pretty console output
- `eth-account` for key management
- Optional: `make` for installation and uninstallation scripts

## Installation

Install from git and also set up Bash completion with `make install`:

```bash
git clone https://github.com/haturatu/hl.git
cd hl
make install
```

Install from source for local development:

```bash
cd hl
pip install -e .
```

Install from PyPI:

```bash
pip install hyperliquid-cli-python
```

After installation, the `hl` command is available:

```bash
hl --help
```

## Bash Completion

`hl` can print a Bash completion script for top-level commands and subcommands.

Note: `pip install -e .` and `pip install hyperliquid-cli-python` install the
`hl` command, but they do not automatically enable Bash completion.

Enable it for the current shell:

```bash
eval "$(hl completion bash)"
```

Persist it in `~/.bashrc`:

```bash
echo 'eval "$(hl completion bash)"' >> ~/.bashrc
```

If you want the old helper flow that also edits `~/.bashrc`, `make install`
still exists.

To remove the package and any managed `~/.bashrc` completion line:

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

Account data stored in `~/.hl/hl.db` is encrypted at rest for these fields:

- `user_address`
- `api_wallet_public_key`
- `api_wallet_private_key`

The current implementation derives a 32-byte key as follows:

1. Resolve the command path used to run `hl`
2. Hash that path with SHA-256
3. Use the resulting digest as the ChaCha20 key

Each stored value is encrypted independently with its own random nonce.

This means:

- the same installed command path can transparently decrypt the saved values
- changing the executable path can make existing saved account data undecryptable
- this is path-bound encryption, not password-based encryption

Example:

```bash
$ which hl
/home/haturatu/.local/bin/hl
```

If `hl` is installed at a user-local path like `/home/haturatu/.local/bin/hl`, then
the encryption key is effectively tied to that installed command path. In normal usage,
that often behaves like "the user who has this `hl` on their path can decrypt the DB".
So in practice it can look close to per-user decryption when each user has their own
home directory and their own local install path.

Important limitation:

- this mechanism does **not** prove OS user identity by itself
- if another OS user can both read `~/.hl/hl.db` and execute the same `hl` binary
  path, path-based derivation alone does not prevent that user from decrypting the data

So this mechanism is mainly useful as a coupling between the saved DB contents and the
specific installed command path. It helps prevent casual reuse of the DB from a different
binary location, but it is not a substitute for filesystem permissions or disk encryption.

Environment variable fallback still exists when DB account data is not configured:

- `HYPERLIQUID_PRIVATE_KEY`
- `HYPERLIQUID_WALLET_ADDRESS`

These environment variables are not stored in the encrypted DB. They are read as
plain process environment values at runtime.

Practical guidance:

- Use wallets or API keys that would not be catastrophic if leaked
- Restrict which OS user can run this tool
- If you need stronger protection, use disk encryption as the higher-level control

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

`--stake` is used by the CLI to derive order size.

- If you pass `--stake 50 --leverage 20`, the CLI derives size from about `$1000`
  of notional (`50 * 20`).
- If you pass `--stake 50` without `--leverage`, the CLI derives size from about
  `$50` of notional.

Important: omitting `--leverage` does **not** mean your account or position is forced
to `1x`. It only means the CLI does not multiply `--stake` by leverage when calculating
the order size. If the exchange/account already has leverage set for that asset, the
resulting position can still show that existing leverage in `hl account positions`.

This means:

- `--stake 50` means the CLI sizes the order from about `$50` of notional
- `--stake 50 --leverage 20` means about `$1000` of position notional

So:

- `--leverage` changes how `--stake` is converted into order size
- existing leverage on the exchange can still affect margin usage and the leverage
  shown later in `hl account positions`

```bash
# No --leverage: CLI sizes the order from about $50 of BTC notional
hl order market buy BTC --stake 50

# No --leverage: CLI sizes the order from about $50 of ETH notional
hl order market buy ETH --stake 50

# With --leverage 20: CLI sizes the order from about $1,000 of BTC notional
hl order limit buy BTC 65000 --stake 50 --leverage 20 --cross

# With --leverage 20: CLI sizes the order from about $1,000 of BTC notional
hl order market buy BTC --stake 50 --leverage 20 --isolated

# Example:
# BTC at 69,000
# - --stake 50                 => about 0.000724 BTC of order size
# - --stake 50 --leverage 20   => about 0.01449 BTC
#
# ETH at 2,020
# - --stake 50                 => about 0.02475 ETH of order size
# - --stake 50 --leverage 20   => about 0.2475 ETH

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
attribution perspective, so this repository is being published under my BSD 2-Clause
License as a temporary choice. If you have a better idea for the appropriate license
notice or attribution, please open an issue.
