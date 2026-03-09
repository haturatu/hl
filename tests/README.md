# JSON Output Pattern Tests

These tests verify that all `hl` subcommand patterns return parseable raw JSON when run with `--json`.

## Run

```bash
cd python-cli
PYTHONPATH=. python -m unittest -v tests.test_json_patterns
```

## Scope

- account/*
- order/*
- asset/*
- markets/*
- referral/*

To avoid network dependency, command execution internals are mocked in these tests.
