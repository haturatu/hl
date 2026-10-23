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

## Results

Unprofiled median times on the same machine, before (`3ec664b`, benchmark only)
and after the two optimizations (`9827276`):

| Scenario | Before (ms) | After (ms) | Requests before → after |
| --- | ---: | ---: | ---: |
| DEX discovery, cold client | 60.608 | 60.454 | 3 → 3 |
| DEX discovery, existing client | 60.571 | 20.145 | 3 → 1 |
| Testnet, all markets | 82.949 | 63.137 | 5 → 5 |
| Mainnet, all markets | 166.639 | 84.324 | 10 → 8 |
| Mainnet, spot only | 62.478 | 62.550 | 4 → 3 |
| Mainnet, perp only | 164.785 | 84.783 | 10 → 8 |

All six output hashes match before and after. Mainnet market latency decreased
by about 49%, and warm DEX discovery latency by about 67% in this simulation.
Spot-only latency is effectively unchanged because the removed categories
request previously overlapped the spot request; its API request count improves.
Cold DEX discovery still needs SDK initialization, but now retains that client
for subsequent reads.

The mainnet savings remove one `spotMeta` and one `meta` initialization request
per context. Spot-only savings remove `perpCategories`. Parallel snapshot and
DEX discovery requests reduce the critical path without changing builder
request count or output ordering.

Validation:

- All 43 unit tests pass on Python 3.14.8 with SDK 0.23.0 and on Python 3.12.3
  with freshly resolved dependencies, including SDK 0.24.0.
- The Python 3.12 / SDK 0.24 offline benchmark produces the same request counts
  and output hashes for all six scenarios.
- Ruff passes for `src` and the changed benchmark/tests; `git diff --check`
  passes. Concurrency tests use barriers/events to verify overlapping requests,
  DEX discovery outside the event loop thread, and stable builder row order.
