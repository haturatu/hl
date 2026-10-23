# Public-read performance investigation

## Investigation and plan

Investigated on 2026-10-24 against `e414335`, using CPython 3.14.8 and
hyperliquid-python-sdk 0.23.0. The original 35 unit tests pass.

Evidence:

- `python -X importtime -m hl_cli.cli.argparse_main --help` attributes about
  316 ms to importing `core.context` and its SDK/signing dependencies. Deferring
  imports is a separate candidate; this change focuses on public API reads.
- `pdb` stopped in `CLIContext.get_perp_dexs` with an already initialized
  `self._info`. `temp is self._info` was false, although both clients had the
  same asset maps. SDK initialization fetches `spotMeta` and `meta` again.
- A deterministic simulated API confirms those two extra requests. Market reads
  additionally wait for spot/categories, then main perps, then DEX discovery,
  then builder perps. DEX discovery blocks the event loop. Spot-only reads also
  fetch unused perp categories.
- `cProfile` identifies client construction, simulated network waits, and event
  loop waits as the dominant costs for these paths. Small row transformations
  are not the optimization target.

Implementation plan, in order:

1. Add a reproducible offline benchmark using real SDK clients with `Info.post`
   replaced by a fixed-response API; record request counts and output hashes.
2. Reuse the context's public client when discovering perp DEXs, retaining
   per-context caching, network selection, and the existing fallback.
3. Fetch independent market snapshots concurrently, move DEX discovery off the
   event loop, and skip perp requests for spot-only output. Preserve spot
   metadata for perp collateral names and skip builder DEXs on testnet.
4. Add regression tests for request counts, cache/fallback behavior, output,
   mainnet/testnet routing, and concurrency using synchronization rather than
   elapsed-time assertions. Run the complete test suite and Ruff.
5. Repeat the benchmark, compare output hashes, and create a PR with measured
   results and the limits of the measurement.

## Reproduction

```bash
PYTHONPATH=src .venv/bin/python tools/benchmark_public_reads.py
PYTHONPATH=src .venv/bin/python tools/benchmark_public_reads.py --profile /tmp/hl-public.prof
.venv/bin/python -c 'import pstats; pstats.Stats("/tmp/hl-public.prof").strip_dirs().sort_stats("cumulative").print_stats(20)'
```

The benchmark uses seven repetitions, 20 ms per simulated request, two builder
DEXs, and a fresh context for each sample. `dexs_warm` initializes the public
client before timing and clears its request log. No real API, local account
database, wallet, or private key is accessed. Timings must be collected without
`pdb`/`cProfile`; those tools add overhead. Output SHA-256 values permit a direct
comparison of the full returned data, including row order.

The fixture is deliberately small to isolate request latency. These results
are not measurements of production API latency, large-universe CPU cost, or
Nuitka executable startup. Existing SDK initialization requests remain included
in the market scenarios.
