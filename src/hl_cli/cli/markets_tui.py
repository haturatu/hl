import contextlib
import json
import select
import sys
import termios
import time
import tty
from itertools import cycle
from typing import Any, Callable, Literal, Optional

from rich.console import Console
from rich.live import Live
from rich.panel import Panel

from ..utils.market_table import (
    build_market_table,
    market_table_columns,
    market_table_row_values,
    market_table_widths,
)


MarketsRows = dict[str, list[dict[str, Any]]]


def _market_row_dex(row: dict[str, Any]) -> str:
    if row.get("marketType") == "spot":
        return ""
    coin = str(row.get("coin", ""))
    if ":" in coin:
        return coin.split(":", 1)[0]
    return ""


def _market_row_kind(row: dict[str, Any]) -> Literal["perp", "spot"]:
    return "spot" if row.get("marketType") == "spot" else "perp"


class MarketsTuiState:
    def __init__(self) -> None:
        self.scope: Literal["all", "perp", "spot"] = "all"
        self.selected = 0
        self.scroll = 0
        self.mode: Literal["normal", "search"] = "normal"
        self.search_direction: Literal["forward", "backward"] = "forward"
        self.search_query = ""
        self.search_buffer = ""

    def rows(self, rows: MarketsRows) -> list[dict[str, Any]]:
        merged = [*rows["perpMarkets"], *rows["spotMarkets"]]
        if self.scope == "all":
            return merged
        return [row for row in merged if _market_row_kind(row) == self.scope]

    def clamp(self, total: int, window_size: int) -> None:
        if total <= 0:
            self.selected = 0
            self.scroll = 0
            return
        self.selected = max(0, min(self.selected, total - 1))
        max_scroll = max(0, total - window_size)
        if self.selected < self.scroll:
            self.scroll = self.selected
        elif self.selected >= self.scroll + window_size:
            self.scroll = self.selected - window_size + 1
        self.scroll = max(0, min(self.scroll, max_scroll))


@contextlib.contextmanager
def _raw_tty_mode() -> Any:
    if not sys.stdin.isatty():
        yield False
        return
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        yield True
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _read_key(timeout: float = 0.0) -> Optional[str]:
    if not sys.stdin.isatty():
        return None
    ready, _, _ = select.select([sys.stdin], [], [], timeout)
    if not ready:
        return None
    first = sys.stdin.read(1)
    if first != "\x1b":
        return first
    ready, _, _ = select.select([sys.stdin], [], [], 0.01)
    if not ready:
        return first
    second = sys.stdin.read(1)
    if second != "[":
        return first + second
    ready, _, _ = select.select([sys.stdin], [], [], 0.01)
    if not ready:
        return first + second
    third = sys.stdin.read(1)
    return first + second + third


def _matches_query(row: dict[str, Any], query: str) -> bool:
    needle = query.strip().lower()
    if not needle:
        return False
    haystacks = [
        str(row.get("coin", "")),
        str(row.get("pairName", "")),
        str(row.get("category", "")),
    ]
    return any(needle in value.lower() for value in haystacks)


def _find_match(rows: list[dict[str, Any]], start: int, query: str, *, forward: bool) -> Optional[int]:
    if not rows or not query.strip():
        return None
    total = len(rows)
    step = 1 if forward else -1
    index = start
    for _ in range(total):
        index = (index + step) % total
        if _matches_query(rows[index], query):
            return index
    return None


def _jump_to_match(
    state: MarketsTuiState,
    rows: MarketsRows,
    *,
    forward: bool,
    wrap_from_current: bool,
    window_size: int,
) -> None:
    current_rows = state.rows(rows)
    if not current_rows or not state.search_query.strip():
        return
    start = state.selected - 1 if forward and not wrap_from_current else state.selected
    if not forward and not wrap_from_current:
        start = state.selected + 1
    match = _find_match(current_rows, start, state.search_query, forward=forward)
    if match is None:
        return
    state.selected = match
    state.clamp(len(current_rows), window_size)


def _handle_key(key: Optional[str], state: MarketsTuiState, rows: MarketsRows, window_size: int) -> bool:
    current_rows = state.rows(rows)
    total = len(current_rows)
    if state.mode == "search":
        if key is None:
            return False
        if key in {"\r", "\n"}:
            state.mode = "normal"
            state.search_query = state.search_buffer
            _jump_to_match(
                state,
                rows,
                forward=state.search_direction == "forward",
                wrap_from_current=False,
                window_size=window_size,
            )
            return False
        if key == "\x1b":
            state.mode = "normal"
            state.search_buffer = state.search_query
            return False
        if key in {"\x7f", "\b"}:
            state.search_buffer = state.search_buffer[:-1]
            return False
        if len(key) == 1 and key.isprintable():
            state.search_buffer += key
        return False

    if key in {"q", "\x03"}:
        return True
    if key in {"/", "?"}:
        state.mode = "search"
        state.search_direction = "forward" if key == "/" else "backward"
        state.search_buffer = state.search_query
        return False
    if key in {"j", "\x1b[B"}:
        state.selected += 1
    elif key in {"k", "\x1b[A"}:
        state.selected -= 1
    elif key == "J":
        state.selected += 10
    elif key == "K":
        state.selected -= 10
    elif key == "g":
        state.selected = 0
    elif key == "G":
        state.selected = max(0, total - 1)
    elif key == "n":
        _jump_to_match(
            state,
            rows,
            forward=state.search_direction == "forward",
            wrap_from_current=True,
            window_size=window_size,
        )
        return False
    elif key == "N":
        _jump_to_match(
            state,
            rows,
            forward=state.search_direction != "forward",
            wrap_from_current=True,
            window_size=window_size,
        )
        return False
    elif key == "h":
        state.scope = "perp"
        state.selected = 0
        state.scroll = 0
    elif key == "l":
        state.scope = "spot"
        state.selected = 0
        state.scroll = 0
    elif key == "a":
        state.scope = "all"
        state.selected = 0
        state.scroll = 0
    state.clamp(len(state.rows(rows)), window_size)
    return False


def _render_table(
    rows: MarketsRows,
    include_category: bool,
    *,
    console: Console,
    state: MarketsTuiState,
    widths_by_scope: dict[str, list[int]],
    format_price: Callable[[Any], str],
    format_usd: Callable[[Any], str],
    format_rate_pct: Callable[[Any], str],
) -> Panel:
    current_rows = state.rows(rows)
    # Leave room for the panel border, title/subtitle, and terminal prompt line.
    window_size = max(5, console.size.height - 8)
    state.clamp(len(current_rows), window_size)
    visible_rows = current_rows[state.scroll : state.scroll + window_size]
    selected_index = state.selected - state.scroll

    show_perp_only_fields = state.scope != "spot"
    columns = market_table_columns(
        include_category=include_category,
        show_perp_only_fields=show_perp_only_fields,
    )
    rendered_rows = [
        market_table_row_values(
            row,
            include_category=include_category,
            show_perp_only_fields=show_perp_only_fields,
            format_price=format_price,
            format_usd=format_usd,
            format_rate_pct=format_rate_pct,
        )
        for row in visible_rows
    ]
    table = build_market_table(
        title=f"Markets ({len(rows['perpMarkets'])} perps, {len(rows['spotMarkets'])} spot)",
        columns=columns,
        rendered_rows=rendered_rows,
        widths=widths_by_scope[state.scope],
        highlighted_index=selected_index,
    )

    if state.mode == "search":
        prefix = "/" if state.search_direction == "forward" else "?"
        help_text = f"{prefix}{state.search_buffer}"
    else:
        help_text = (
            f"scope={state.scope}  rows={len(current_rows)}  "
            f"search={state.search_query or '-'}  "
            "hjkl/arrows move  gg/G top/bottom  / ? search  n/N next/prev  h perp  l spot  a all  q quit"
        )
    return Panel(table, subtitle=help_text)


def run_markets_tui(
    *,
    console: Console,
    rows: MarketsRows,
    include_category: bool,
    next_mids: Callable[[str], dict[str, Any]],
    sort_rows: Callable[[MarketsRows], MarketsRows],
    prepare_output: Callable[[MarketsRows], MarketsRows],
    format_price: Callable[[Any], str],
    format_usd: Callable[[Any], str],
    format_rate_pct: Callable[[Any], str],
    as_json: bool,
) -> None:
    if as_json:
        print(json.dumps(prepare_output(rows), ensure_ascii=False))

    row_map = {row["coin"]: row for row in [*rows["perpMarkets"], *rows["spotMarkets"]]}
    dexes = sorted({_market_row_dex(row) for row in row_map.values()}, key=lambda value: (value != "", value))
    if not dexes:
        return

    def refresh_rows() -> MarketsRows:
        return sort_rows(rows)

    if as_json:
        try:
            for dex in cycle(dexes):
                mids = next_mids(dex)
                for coin, row in row_map.items():
                    if _market_row_dex(row) != dex:
                        continue
                    if coin in mids:
                        row["price"] = mids[coin]
                print(json.dumps(prepare_output(refresh_rows()), ensure_ascii=False))
                time.sleep(1.0)
        except KeyboardInterrupt:
            return
        return

    state = MarketsTuiState()
    widths_by_scope: dict[str, list[int]] = {}
    for scope in ("all", "perp", "spot"):
        state.scope = scope
        scope_rows = state.rows(rows)
        show_perp_only_fields = scope != "spot"
        columns = market_table_columns(
            include_category=include_category,
            show_perp_only_fields=show_perp_only_fields,
        )
        rendered_rows = [
            market_table_row_values(
                row,
                include_category=include_category,
                show_perp_only_fields=show_perp_only_fields,
                format_price=format_price,
                format_usd=format_usd,
                format_rate_pct=format_rate_pct,
            )
            for row in scope_rows
        ]
        widths_by_scope[scope] = market_table_widths(columns, rendered_rows)
    state.scope = "all"
    try:
        with _raw_tty_mode():
            initial = _render_table(
                rows,
                include_category,
                console=console,
                state=state,
                widths_by_scope=widths_by_scope,
                format_price=format_price,
                format_usd=format_usd,
                format_rate_pct=format_rate_pct,
            )
            with Live(initial, console=console, refresh_per_second=8, screen=True) as live:
                for dex in cycle(dexes):
                    if _handle_key(_read_key(0.0), state, rows, 24):
                        return
                    mids = next_mids(dex)
                    for coin, row in row_map.items():
                        if _market_row_dex(row) != dex:
                            continue
                        if coin in mids:
                            row["price"] = mids[coin]
                    live.update(
                        _render_table(
                            refresh_rows(),
                            include_category,
                            console=console,
                            state=state,
                            widths_by_scope=widths_by_scope,
                            format_price=format_price,
                            format_usd=format_usd,
                            format_rate_pct=format_rate_pct,
                        )
                    )
                    tick_end = time.time() + 1.0
                    while time.time() < tick_end:
                        if _handle_key(_read_key(0.1), state, rows, 24):
                            return
                        live.update(
                            _render_table(
                                refresh_rows(),
                                include_category,
                                console=console,
                                state=state,
                                widths_by_scope=widths_by_scope,
                                format_price=format_price,
                                format_usd=format_usd,
                                format_rate_pct=format_rate_pct,
                            )
                        )
    except KeyboardInterrupt:
        return
