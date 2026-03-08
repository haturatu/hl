# Hyperliquid CLI (Python)

## Installation

```bash
cd hl
pip install .
```

After installation, the `hl` command is available:

```bash
hl --help
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
- `hl server start|stop|status`

## Configuration

- DB: `~/.hl/hl.db`
- Order config: `~/.hl/order-config.json`
- Server files: `~/.hl/server.pid`, `~/.hl/server.log`, `~/.hl/server-cache.json`

Environment variable fallback (when DB account is not configured):

- `HYPERLIQUID_PRIVATE_KEY`
- `HYPERLIQUID_WALLET_ADDRESS`

## Run for Development

```bash
cd hl
python -m hl_cli.main --help
```

## JSON Pattern Tests

`tests/` validates that every subcommand pattern produces parseable raw JSON output in `--json` mode.

```bash
cd hl
PYTHONPATH=. python -m unittest -v tests.test_json_patterns
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
