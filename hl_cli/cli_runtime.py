import time
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from .context import CLIContext

console = Console()


def cli_context(ctx: typer.Context) -> CLIContext:
    return ctx.obj["context"]


def json_output_enabled(ctx: typer.Context) -> bool:
    return bool(ctx.obj["json"])


def finish_command(ctx: typer.Context) -> None:
    if not json_output_enabled(ctx):
        elapsed = time.perf_counter() - float(ctx.obj["start"])
        print(f"\nExecution time: {elapsed:.2f}s")


def confirm(message: str, default: bool = False) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    answer = input(f"{message} {suffix}: ").strip().lower()
    if not answer:
        return default
    return answer in {"y", "yes"}


def render_table(title: str, columns: list[str], rows: list[list[Any]]) -> None:
    table = Table(title=title)
    for c in columns:
        table.add_column(c)
    for row in rows:
        table.add_row(*[str(v) for v in row])
    console.print(table)
