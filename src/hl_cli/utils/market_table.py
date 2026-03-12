from typing import Callable

from rich.table import Table

from ..types import DisplayValue, MarketRow

def market_table_columns(*, include_category: bool, show_perp_only_fields: bool) -> list[str]:
    columns = ["Coin", "Pair", "Price", "24h%", "Vol"]
    if include_category:
        columns.insert(1, "Category")
    if show_perp_only_fields:
        columns.extend(["Funding", "OI"])
    return columns

def market_table_row_values(
    row: MarketRow,
    *,
    include_category: bool,
    show_perp_only_fields: bool,
    format_price: Callable[[DisplayValue], str],
    format_usd: Callable[[DisplayValue], str],
    format_rate_pct: Callable[[DisplayValue], str],
) -> list[str]:
    values = [
        str(row.get("coin", "")),
        str(row.get("pairName", "")),
        format_price(row.get("price")),
        "-" if row.get("priceChange") is None else f"{float(row['priceChange']):.2f}%",
        format_usd(row.get("volumeUsd")),
    ]
    if include_category:
        values.insert(1, str(row.get("category") or "-"))
    if show_perp_only_fields:
        values.extend(
            [
                format_rate_pct(row.get("funding")),
                format_usd(row.get("openInterestUsd")),
            ]
        )
    return values

def market_table_widths(columns: list[str], rendered_rows: list[list[str]]) -> list[int]:
    widths = [len(column) for column in columns]
    for row in rendered_rows:
        for idx, value in enumerate(row):
            widths[idx] = max(widths[idx], len(value))
    return widths

def build_market_table(
    *,
    title: str,
    columns: list[str],
    rendered_rows: list[list[str]],
    widths: list[int],
    highlighted_index: int = -1,
) -> Table:
    table = Table(title=title)
    for idx, column in enumerate(columns):
        justify = "right" if column in {"Price", "24h%", "Vol", "Funding", "OI"} else "left"
        table.add_column(column, width=widths[idx], no_wrap=True, overflow="ellipsis", justify=justify)
    for idx, row in enumerate(rendered_rows):
        style = "bold reverse" if idx == highlighted_index else ""
        table.add_row(*row, style=style)
    return table
