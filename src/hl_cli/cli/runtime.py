import asyncio
from concurrent.futures import Future
from functools import wraps
import threading
import time
from typing import Awaitable, Callable, ParamSpec, Protocol, TypeVar, TypedDict

from rich.console import Console
from rich.table import Table

from ..core.context import CLIContext
from ..types import TableCell
from ..utils.output import out_error

console = Console()
P = ParamSpec("P")
R = TypeVar("R")

class RuntimeObj(TypedDict):
    context: CLIContext
    json: bool
    start: float


class CommandContext(Protocol):
    obj: RuntimeObj


def cli_context(ctx: CommandContext) -> CLIContext:
    return ctx.obj["context"]

def json_output_enabled(ctx: CommandContext) -> bool:
    return bool(ctx.obj["json"])

def finish_command(ctx: CommandContext) -> None:
    if not json_output_enabled(ctx):
        elapsed = time.perf_counter() - float(ctx.obj["start"])
        print(f"\nExecution time: {elapsed:.2f}s")

def confirm(message: str, default: bool = False) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    answer = input(f"{message} {suffix}: ").strip().lower()
    if not answer:
        return default
    return answer in {"y", "yes"}

def render_table(title: str, columns: list[str], rows: list[list[TableCell]]) -> None:
    table = Table(title=title)
    for c in columns:
        table.add_column(c)
    for row in rows:
        table.add_row(*[str(v) for v in row])
    console.print(table)

def run_blocking(coro: Awaitable[R]) -> R:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result: Future[R] = Future()

    def runner() -> None:
        try:
            value = asyncio.run(coro)
        except BaseException as exc:  # noqa: BLE001
            result.set_exception(exc)
            return
        result.set_result(value)

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    return result.result()

def cli_command(fn: Callable[P, R]) -> Callable[P, R]:
    @wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return fn(*args, **kwargs)
        except SystemExit:
            raise
        except Exception as exc:  # noqa: BLE001
            out_error(str(exc))
            raise SystemExit(1) from exc

    return wrapper
