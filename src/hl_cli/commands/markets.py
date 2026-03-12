import asyncio
from typing import Any, Optional

from hyperliquid.info import Info

from ..cli.markets_tui import run_markets_tui
from ..cli.runtime import cli_command, console, run_blocking
from ..core.context import CLIContext
from ..core.testnet_policy import includes_builder_perps
from ..types import MarketRow, MarketsPayload, PerpAssetCtx, PerpMeta, SpotTokenInfo
from ..utils.output import out
from .common import _ctx, _done, _format_price, _format_rate_pct, _format_usd, _json

MARKET_SORT_FIELDS = {"volume", "oi", "price", "change", "funding", "coin"}

def _normalize_market_sort(sort_by: str) -> str:
    value = sort_by.strip().lower()
    if value not in MARKET_SORT_FIELDS:
        allowed = ", ".join(sorted(MARKET_SORT_FIELDS))
        raise RuntimeError(f"invalid sort field: {sort_by} (expected one of: {allowed})")
    return value

def _to_float(value: str | float | int | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None

def _sort_market_rows(rows: MarketsPayload, sort_by: str) -> MarketsPayload:
    sort_by = _normalize_market_sort(sort_by)

    def numeric_value(row: MarketRow, key: str) -> float | None:
        return _to_float(row.get(key))

    def sort_rows(items: list[MarketRow]) -> list[MarketRow]:
        if sort_by == "coin":
            return sorted(items, key=lambda row: str(row.get("coin", "")).lower())

        field_map = {
            "volume": "volumeUsd",
            "oi": "openInterestUsd",
            "price": "price",
            "change": "priceChange",
            "funding": "funding",
        }
        field = field_map[sort_by]

        def key(row: MarketRow) -> tuple[int, float]:
            value = numeric_value(row, field)
            if value is None:
                return (1, 0.0)
            return (0, -value)

        return sorted(items, key=key)

    return {
        "perpMarkets": sort_rows(rows["perpMarkets"]),
        "spotMarkets": sort_rows(rows["spotMarkets"]),
    }

def _filter_market_rows_by_category(
    rows: MarketsPayload, category: Optional[str]
) -> MarketsPayload:
    if category is None or category == "*":
        return rows
    needle = category.strip().lower()
    if not needle:
        return rows
    return {
        "perpMarkets": [row for row in rows["perpMarkets"] if str(row.get("category", "")).lower() == needle],
        "spotMarkets": [],
    }

def _prepare_market_output(
    rows: MarketsPayload, include_category: bool
) -> MarketsPayload:
    if include_category:
        return rows

    def strip_category(items: list[MarketRow]) -> list[MarketRow]:
        return [{k: v for k, v in row.items() if k not in {"category", "marketType"}} for row in items]

    return {
        "perpMarkets": strip_category(rows["perpMarkets"]),
        "spotMarkets": strip_category(rows["spotMarkets"]),
    }

def _watch_markets_prices(
    context: CLIContext,
    *,
    spot_only: bool,
    perp_only: bool,
    category: Optional[str],
    sort_by: str,
    as_json: bool,
) -> None:
    include_category = category is not None
    base_rows = _build_market_rows(context, spot_only, perp_only)
    filtered_rows = _filter_market_rows_by_category(base_rows, category)
    rows = _sort_market_rows(filtered_rows, sort_by)

    info = context.get_public_client()
    run_markets_tui(
        console=console,
        rows=rows,
        include_category=include_category,
        next_mids=lambda dex: info.all_mids(dex=dex) if dex else info.all_mids(),
        sort_rows=lambda current: _sort_market_rows(current, sort_by),
        prepare_output=lambda current: _prepare_market_output(current, include_category),
        format_price=_format_price,
        format_usd=_format_usd,
        format_rate_pct=_format_rate_pct,
        as_json=as_json,
    )

def _build_market_rows(context: CLIContext, spot_only: bool, perp_only: bool) -> MarketsPayload:
    return run_blocking(_build_market_rows_async(context, spot_only, perp_only))

def _safe_token_name(tokens: list[SpotTokenInfo], index: object, default: str = "?") -> str:
    if not isinstance(index, int) or index < 0 or index >= len(tokens):
        return default
    return str(tokens[index].get("name", default))

async def _build_market_rows_async(
    context: CLIContext, spot_only: bool, perp_only: bool
) -> MarketsPayload:
    info = context.get_public_client()
    spot_task = asyncio.to_thread(info.spot_meta_and_asset_ctxs)
    perp_categories_task = asyncio.to_thread(info.post, "/info", {"type": "perpCategories"})
    (spot_meta, spot_ctxs), perp_categories_raw = await asyncio.gather(spot_task, perp_categories_task)
    perp_categories = {
        str(coin): str(category)
        for coin, category in perp_categories_raw
        if isinstance(coin, str) and isinstance(category, str)
    }

    spot_rows: list[MarketRow] = []
    perp_rows: list[MarketRow] = []

    if not perp_only:
        ctx_map = {c["coin"]: c for c in spot_ctxs}
        tokens = spot_meta.get("tokens", [])
        for pair in spot_meta.get("universe", []):
            refs = pair.get("tokens", [])
            if not isinstance(refs, list) or len(refs) < 2:
                continue
            base = _safe_token_name(tokens, refs[0])
            quote = _safe_token_name(tokens, refs[1])
            # Testnet can return spot pairs whose token indexes do not exist.
            if base == "?" or quote == "?":
                continue
            ctx_row = ctx_map.get(pair["name"])
            if ctx_row is None:
                continue
            prev = float(ctx_row.get("prevDayPx", 0) or 0)
            mark = float(ctx_row.get("markPx", 0) or 0)
            chg = ((mark - prev) / prev * 100) if prev else None
            spot_rows.append(
                {
                    "coin": pair["name"],
                    "marketType": "spot",
                    "category": None,
                    "pairName": f"[Spot] {base}/{quote}",
                    "price": ctx_row.get("markPx", "?"),
                    "priceChange": chg,
                    "volumeUsd": ctx_row.get("dayNtlVlm", "?"),
                    "funding": None,
                    "openInterest": None,
                    "openInterestUsd": None,
                }
            )

    if not spot_only:
        perp_meta, perp_ctxs = await asyncio.to_thread(info.meta_and_asset_ctxs)
        tokens = spot_meta.get("tokens", [])
        collateral = _safe_token_name(tokens, perp_meta.get("collateralToken", 0), "USD")
        for i, market in enumerate(perp_meta["universe"]):
            if market.get("isDelisted"):
                continue
            if i >= len(perp_ctxs):
                continue
            ctx_row = perp_ctxs[i]
            prev = float(ctx_row.get("prevDayPx", 0) or 0)
            mark = float(ctx_row.get("markPx", 0) or 0)
            oi_raw = _to_float(ctx_row.get("openInterest"))
            chg = ((mark - prev) / prev * 100) if prev else None
            perp_rows.append(
                {
                    "coin": market["name"],
                    "marketType": "perp",
                    "category": perp_categories.get(str(market["name"])),
                    "pairName": f"{market['name']}/{collateral} {market.get('maxLeverage', '?')}x",
                    "price": ctx_row.get("markPx", "?"),
                    "priceChange": chg,
                    "volumeUsd": ctx_row.get("dayNtlVlm", "?"),
                    "funding": ctx_row.get("funding"),
                    "openInterest": ctx_row.get("openInterest"),
                    "openInterestUsd": (oi_raw * mark) if oi_raw is not None and mark > 0 else None,
                }
            )

        # Testnet uses main perp + spot only to avoid rate-limiting on bulk builder fetches.
        if includes_builder_perps(context.config.testnet):
            dexs = [dex for dex in context.get_perp_dexs() if dex]
            builder_results = await asyncio.gather(
                *(asyncio.to_thread(_fetch_builder_market_data, info, dex) for dex in dexs)
            )
            for meta, ctxs in builder_results:
                dex = str(meta.get("dex", ""))
                if not dex:
                    continue
                coll_idx = meta.get("collateralToken", 0)
                collateral = _safe_token_name(tokens, coll_idx, "USD")
                for i, market in enumerate(meta.get("universe", [])):
                    coin = str(market.get("name"))
                    if not coin or market.get("isDelisted"):
                        continue
                    if i >= len(ctxs):
                        continue
                    ctx_row = ctxs[i]
                    prev = float(ctx_row.get("prevDayPx", 0) or 0)
                    mark = float(ctx_row.get("markPx", 0) or 0)
                    oi_raw = _to_float(ctx_row.get("openInterest"))
                    chg = ((mark - prev) / prev * 100) if prev else None
                    perp_rows.append(
                        {
                            "coin": coin,
                            "marketType": "perp",
                            "category": perp_categories.get(coin),
                            "pairName": f"{coin}/{collateral} {market.get('maxLeverage', '?')}x",
                            "price": ctx_row.get("markPx", "?"),
                            "priceChange": chg,
                            "volumeUsd": ctx_row.get("dayNtlVlm", "?"),
                            "funding": ctx_row.get("funding"),
                            "openInterest": ctx_row.get("openInterest"),
                            "openInterestUsd": (oi_raw * mark) if oi_raw is not None and mark > 0 else None,
                        }
                    )

    return {"perpMarkets": perp_rows, "spotMarkets": spot_rows}

def _fetch_builder_market_data(info: Info, dex: str) -> tuple[PerpMeta, list[PerpAssetCtx]]:
    meta, ctxs = info.post("/info", {"type": "metaAndAssetCtxs", "dex": dex})
    meta["dex"] = dex
    return meta, ctxs

@cli_command
def markets_ls(
    ctx: Any,
    spot_only: bool = False,
    perp_only: bool = False,
    category: Optional[str] = None,
    sort_by: str = "volume",
    watch: bool = False,
) -> None:
    context = _ctx(ctx)
    include_category = category is not None

    if watch:
        _watch_markets_prices(
            context,
            spot_only=spot_only,
            perp_only=perp_only,
            category=category,
            sort_by=sort_by,
            as_json=_json(ctx),
        )
        return

    out(
        _prepare_market_output(
            _sort_market_rows(
                _filter_market_rows_by_category(_build_market_rows(context, spot_only, perp_only), category),
                sort_by,
            ),
            include_category,
        ),
        _json(ctx),
    )
    _done(ctx)

@cli_command
def markets_search(
    ctx: Any,
    query: str,
    spot_only: bool = False,
    perp_only: bool = False,
    category: Optional[str] = None,
    sort_by: str = "volume",
) -> None:
    include_category = category is not None
    q = query.strip().lower()
    if not q:
        raise RuntimeError("query must not be empty")
    rows = _prepare_market_output(
        _sort_market_rows(
            _filter_market_rows_by_category(_build_market_rows(_ctx(ctx), spot_only, perp_only), category),
            sort_by,
        ),
        include_category,
    )
    perps = [
        row
        for row in rows["perpMarkets"]
        if q in str(row.get("coin", "")).lower()
        or q in str(row.get("pairName", "")).lower()
        or q in str(row.get("category", "")).lower()
    ]
    spots = [
        row
        for row in rows["spotMarkets"]
        if q in str(row.get("coin", "")).lower()
        or q in str(row.get("pairName", "")).lower()
        or q in str(row.get("category", "")).lower()
    ]
    out({"perpMarkets": perps, "spotMarkets": spots}, _json(ctx))
    _done(ctx)
