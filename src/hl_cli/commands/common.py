from typing import Any

from ..cli.runtime import cli_context, confirm, finish_command, json_output_enabled, render_table
from ..core.context import CLIContext

def _ctx(ctx: Any) -> CLIContext:
    return cli_context(ctx)

def _json(ctx: Any) -> bool:
    return json_output_enabled(ctx)

def _done(ctx: Any) -> None:
    finish_command(ctx)

def _confirm(message: str, default: bool = False) -> bool:
    return confirm(message, default)

def _format_address(addr: str) -> str:
    return f"{addr[:6]}...{addr[-4:]}"

def _render_table(title: str, columns: list[str], rows: list[list[Any]]) -> None:
    render_table(title, columns, rows)

def _format_usd(value: str | float | int | None) -> str:
    try:
        n = float(value)  # type: ignore[arg-type]
        return f"${n:,.2f}"
    except Exception:
        return f"${value}" if value is not None else "-"

def _format_price(value: str | float | int | None) -> str:
    try:
        n = float(value)  # type: ignore[arg-type]
    except Exception:
        return f"${value}" if value is not None else "-"

    abs_n = abs(n)
    if abs_n >= 1000:
        s = f"{n:,.2f}"
    elif abs_n >= 1:
        s = f"{n:,.4f}"
    elif abs_n >= 0.01:
        s = f"{n:,.4f}"
    elif abs_n >= 0.0001:
        s = f"{n:,.6f}"
    else:
        s = f"{n:,.8f}"

    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return f"${s}"

def _format_rate_pct(value: str | float | int | None) -> str:
    try:
        n = float(value)  # type: ignore[arg-type]
    except Exception:
        return str(value) if value is not None else "-"

    abs_n = abs(n)
    if abs_n >= 1:
        s = f"{n:+.2f}"
    elif abs_n >= 0.01:
        s = f"{n:+.4f}"
    else:
        s = f"{n:+.6f}"

    if "." in s:
        sign = s[0] if s[0] in "+-" else ""
        digits = s[1:] if sign else s
        digits = digits.rstrip("0").rstrip(".")
        s = f"{sign}{digits}"
    return f"{s}%"

def _network_name(context: CLIContext) -> str:
    return "testnet" if context.config.testnet else "mainnet"
