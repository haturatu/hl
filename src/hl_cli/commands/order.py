import asyncio
from datetime import datetime
from decimal import Decimal, ROUND_DOWN
from typing import Any, Optional

from hyperliquid.utils.constants import MAINNET_API_URL
from hyperliquid.utils.signing import float_to_wire, get_timestamp_ms, sign_l1_action

from ..cli.runtime import CommandContext, cli_command, cli_context, confirm, finish_command, json_output_enabled, render_table
from ..cli.runtime import run_blocking
from ..core.context import CLIContext
from ..core.order_config import get_order_config, update_order_config
from ..core.testnet_policy import uses_main_perp_only
from ..infra.twap_registry import (
    find_twap_order,
    list_twap_orders,
    mark_twap_cancelled,
    register_twap_order,
)
from ..types import (
    ClearinghouseState,
    DisplayValue,
    ExchangeOrderStatus,
    ExchangeStatusFilled,
    ExchangeStatusResting,
    ExchangeSuccessEnvelope,
    OpenOrderRow,
    TableCell,
)
from ..utils.output import out, out_success
from ..utils.validators import (
    normalize_side,
    normalize_tif,
    side_is_buy,
    side_uses_spot,
    validate_positive_integer,
    validate_positive_number,
)
from ..utils.watch import watch_loop

def _ctx(ctx: CommandContext) -> CLIContext:
    return cli_context(ctx)

def _json(ctx: CommandContext) -> bool:
    return json_output_enabled(ctx)

def _done(ctx: CommandContext) -> None:
    finish_command(ctx)

def _wallet_perp_dexs_for_coin(coin: str) -> Optional[list[str]]:
    if ":" not in coin:
        return None
    return [coin.split(":", 1)[0]]

def _close_position_perp_dexs(context: CLIContext) -> list[str]:
    if uses_main_perp_only(context.config.testnet):
        return [""]
    return context.get_perp_dexs()

def _confirm(message: str, default: bool = False) -> bool:
    return confirm(message, default)

def _render_table(title: str, columns: list[str], rows: list[list[TableCell]]) -> None:
    render_table(title, columns, rows)

def _format_usd(value: DisplayValue) -> str:
    try:
        n = float(value)  # type: ignore[arg-type]
        return f"${n:,.2f}"
    except Exception:
        return f"${value}" if value is not None else "-"

def _extract_statuses(result: ExchangeSuccessEnvelope) -> list[ExchangeOrderStatus]:
    try:
        statuses = result.get("response", {}).get("data", {}).get("statuses", [])
        if isinstance(statuses, list):
            return statuses
        return []
    except Exception:
        return []

def _print_leverage_update(lev_result: Optional[dict[str, Any]], coin: str, leverage: Optional[int], is_cross: bool) -> None:
    if not lev_result:
        return
    if lev_result.get("status") == "ok":
        if leverage is not None:
            print(f"⚙️  Leverage set: {coin} {leverage}x ({'cross' if is_cross else 'isolated'})")
    else:
        print(f"⚠️  Leverage update failed: {lev_result.get('response')}")

def _print_order_feedback(
    *,
    result: ExchangeSuccessEnvelope,
    coin: str,
    side: str,
    order_kind: str,
    stake: Optional[float] = None,
) -> None:
    statuses = _extract_statuses(result)
    if not statuses:
        print("ℹ️  Request sent.")
        return

    first_error = None
    first_filled: ExchangeStatusFilled | None = None
    first_resting: ExchangeStatusResting | None = None
    for s in statuses:
        if isinstance(s, dict) and "error" in s and first_error is None:
            first_error = str(s["error"])
        if isinstance(s, dict) and "filled" in s and first_filled is None:
            first_filled = s["filled"]
        if isinstance(s, dict) and "resting" in s and first_resting is None:
            first_resting = s["resting"]

    if first_error is not None:
        print("❌ Order rejected")
        print(f"\nReason: {first_error}")
        if stake is not None:
            print(f"Your stake (margin): {_format_usd(stake)}")
            if "minimum value" in first_error.lower():
                print("\nTip: Increase --stake or --leverage so position value is at least $10.")
        return

    if first_filled is not None:
        print(f"✅ {order_kind} order executed")
        print(f"\nAsset: {coin}")
        print(f"Side: {side.upper()}")
        print(f"Filled size: {first_filled.get('totalSz')} {coin}")
        print(f"Average price: {_format_usd(first_filled.get('avgPx'))}")
        print(f"Order ID: {first_filled.get('oid')}")
        return

    if first_resting is not None:
        print(f"✅ {order_kind} order placed")
        print(f"\nAsset: {coin}")
        print(f"Side: {side.upper()}")
        print(f"Order ID: {first_resting.get('oid')}")
        return

    print("ℹ️  Request completed.")

def _parse_twap_interval(value: str) -> tuple[int, int]:
    parts = [x.strip() for x in value.split(",")]
    if len(parts) == 1:
        minutes = int(parts[0])
        if minutes <= 0:
            raise ValueError("minutes must be a positive integer")
        return minutes, 1
    if len(parts) == 2:
        minutes = int(parts[0])
        orders = int(parts[1])
        if minutes <= 0 or orders <= 0:
            raise ValueError("minutes and orders must be positive integers")
        return minutes * orders, orders
    raise ValueError("interval must be '<minutes>' or '<slice_minutes>,<orders>' (e.g. 30 or 5,10)")

def _get_max_leverage_for_coin(context: CLIContext, coin: str) -> int:
    info = context.get_public_client()

    dex = ""
    search_names = [coin]
    if ":" in coin:
        dex = coin.split(":", 1)[0]
        base = coin.split(":", 1)[1]
        search_names.append(base)

    meta = info.meta(dex=dex)
    for m in meta.get("universe", []):
        if m.get("name") in search_names:
            max_lev = m.get("maxLeverage")
            if max_lev is None:
                continue
            return int(max_lev)
    raise RuntimeError(f"Could not resolve max leverage for {coin}")

def _is_invalid_leverage_response(resp: object) -> bool:
    if not isinstance(resp, dict):
        return False
    if str(resp.get("status", "")).lower() != "err":
        return False
    return "invalid leverage value" in str(resp.get("response", "")).lower()

def _update_leverage_with_fallback(
    *,
    context: CLIContext,
    coin: str,
    leverage: int,
    is_cross: bool,
    emit_warning: bool = True,
) -> dict[str, Any]:
    wallet = context.get_wallet_client(perp_dexs=_wallet_perp_dexs_for_coin(coin))
    result = wallet.update_leverage(leverage, coin, is_cross=is_cross)
    if not _is_invalid_leverage_response(result):
        return result

    max_lev = _get_max_leverage_for_coin(context, coin)
    if emit_warning:
        print(
            f"Warning: Invalid leverage value ({leverage}) for {coin}. "
            f"Retrying with max leverage {max_lev}."
        )
    return wallet.update_leverage(max_lev, coin, is_cross=is_cross)

def _maybe_update_leverage(
    *,
    context: CLIContext,
    coin: str,
    leverage: Optional[int],
    cross: bool,
    isolated: bool,
    emit_warning: bool = True,
) -> Optional[dict[str, Any]]:
    if cross and isolated:
        raise RuntimeError("Use only one of --cross or --isolated")
    if leverage is None:
        if cross or isolated:
            raise RuntimeError("--cross/--isolated requires --leverage")
        return None
    if leverage <= 0:
        raise RuntimeError("leverage must be a positive integer")
    is_cross = cross or not isolated
    return _update_leverage_with_fallback(
        context=context,
        coin=coin,
        leverage=leverage,
        is_cross=is_cross,
        emit_warning=emit_warning,
    )

def _normalize_size_for_coin(context: CLIContext, coin: str, raw_size: float) -> float:
    if raw_size <= 0:
        raise RuntimeError("size must be a positive number")
    exchange = context.get_wallet_client(perp_dexs=_wallet_perp_dexs_for_coin(coin))
    asset = exchange.info.name_to_asset(coin)
    sz_decimals = int(exchange.info.asset_to_sz_decimals[asset])

    q = Decimal(1).scaleb(-sz_decimals)
    d = Decimal(str(raw_size)).quantize(q, rounding=ROUND_DOWN)
    if d <= 0:
        raise RuntimeError(f"size too small for {coin}; minimum unit is 1e-{sz_decimals}")
    return float(d)

def _resolve_perp_coin(context: CLIContext, coin: str) -> str:
    info = context.get_public_client()
    target = coin.strip()
    if not target:
        raise RuntimeError("coin must not be empty")

    mids = info.all_mids()
    if target in mids:
        return target
    up = target.upper()
    if up in mids:
        return up
    if ":" in target:
        dex, sym = target.split(":", 1)
        norm = f"{dex.lower()}:{sym.upper()}"
        if norm in mids:
            return norm

    for m in info.meta().get("universe", []):
        name = str(m.get("name", ""))
        if name == target or name.upper() == up:
            return name

    perp_candidates: list[tuple[str, int]] = []
    dex_names = [
        str(dex_item.get("name", ""))
        for dex_item in info.perp_dexs()
        if isinstance(dex_item, dict) and dex_item.get("name")
    ]
    builder_market_data = run_blocking(_fetch_all_builder_resolution_data(info, dex_names))
    for meta, dex_mids in builder_market_data:
        for m in meta.get("universe", []):
            full_name = str(m.get("name", ""))
            if not full_name:
                continue
            suffix = full_name.split(":", 1)[1] if ":" in full_name else full_name
            if full_name.upper() == up or suffix.upper() == up:
                if full_name not in dex_mids:
                    continue
                lev = int(m.get("maxLeverage", 0) or 0)
                perp_candidates.append((full_name, lev))

    if perp_candidates:
        perp_candidates.sort(key=lambda x: (x[1], x[0]), reverse=True)
        return perp_candidates[0][0]

    raise RuntimeError(f"Coin not found: {coin}")

def _resolve_spot_coin(context: CLIContext, coin: str) -> str:
    info = context.get_public_client()
    target = coin.strip()
    if not target:
        raise RuntimeError("coin must not be empty")

    mids = info.all_mids()
    if target in mids and ("/" in target or target.startswith("@")):
        return target
    up = target.upper()
    if up in mids and ("/" in up or up.startswith("@")):
        return up

    spot_meta = info.spot_meta()
    tokens = spot_meta.get("tokens", [])
    universe = spot_meta.get("universe", [])
    usdc_index = next((t.get("index") for t in tokens if str(t.get("name", "")).upper() == "USDC"), 0)
    token_index = next(
        (
            int(t.get("index"))
            for t in tokens
            if str(t.get("name", "")).upper() == up or str(t.get("fullName", "")).upper() == up
        ),
        None,
    )
    if token_index is None:
        raise RuntimeError(f"Spot market not found: {coin}")

    preferred = next(
        (
            str(p.get("name"))
            for p in universe
            if isinstance(p.get("tokens"), list)
            and len(p["tokens"]) >= 2
            and int(p["tokens"][0]) == token_index
            and int(p["tokens"][1]) == int(usdc_index)
        ),
        None,
    )
    if preferred and preferred in mids:
        return preferred

    fallback = next(
        (
            str(p.get("name"))
            for p in universe
            if isinstance(p.get("tokens"), list)
            and token_index in [int(x) for x in p["tokens"]]
            and str(p.get("name")) in mids
        ),
        None,
    )
    if fallback:
        return fallback

    raise RuntimeError(f"Spot market not found: {coin}")

def _resolve_coin_for_side(context: CLIContext, side: str, coin: str) -> str:
    if side_uses_spot(side):
        return _resolve_spot_coin(context, coin)
    return _resolve_perp_coin(context, coin)

def _resolve_tradable_coin(context: CLIContext, coin: str) -> str:
    try:
        return _resolve_perp_coin(context, coin)
    except RuntimeError:
        return _resolve_spot_coin(context, coin)

def _validate_side_mode_args(
    *,
    side: str,
    leverage: Optional[int],
    cross: bool,
    isolated: bool,
    reduce_only: bool = False,
) -> None:
    if side_uses_spot(side):
        if leverage is not None or cross or isolated:
            raise RuntimeError("--leverage/--cross/--isolated are only supported with long/short")
        if reduce_only:
            raise RuntimeError("--reduce-only is only supported with long/short")

def _mids_for_coin(context: CLIContext, coin: str) -> dict[str, str]:
    info = context.get_public_client()
    if ":" in coin:
        dex = coin.split(":", 1)[0]
        return info.all_mids(dex=dex)
    return info.all_mids()

def _stake_to_position_notional(stake: float, leverage: Optional[int]) -> float:
    if stake <= 0:
        raise RuntimeError("stake must be a positive number")
    lev = 1 if leverage is None else leverage
    if lev <= 0:
        raise RuntimeError("leverage must be a positive integer when used with --stake")
    return stake * float(lev)

def _place_native_twap(
    *,
    context: CLIContext,
    coin: str,
    is_buy: bool,
    size: float,
    minutes: int,
    reduce_only: bool,
    randomize: bool,
) -> dict[str, Any]:
    exchange = context.get_wallet_client(perp_dexs=_wallet_perp_dexs_for_coin(coin))
    asset = exchange.info.name_to_asset(coin)
    action = {
        "type": "twapOrder",
        "twap": {
            "a": asset,
            "b": is_buy,
            "s": float_to_wire(size),
            "r": reduce_only,
            "m": minutes,
            "t": randomize,
        },
    }
    nonce = get_timestamp_ms()
    signature = sign_l1_action(
        exchange.wallet,
        action,
        exchange.vault_address,
        nonce,
        exchange.expires_after,
        exchange.base_url == MAINNET_API_URL,
    )
    return exchange._post_action(action, signature, nonce)  # noqa: SLF001

def _cancel_native_twap(*, context: CLIContext, coin: str, twap_id: int) -> dict[str, Any]:
    exchange = context.get_wallet_client(perp_dexs=_wallet_perp_dexs_for_coin(coin))
    asset = exchange.info.name_to_asset(coin)
    action = {"type": "twapCancel", "a": asset, "t": twap_id}
    nonce = get_timestamp_ms()
    signature = sign_l1_action(
        exchange.wallet,
        action,
        exchange.vault_address,
        nonce,
        exchange.expires_after,
        exchange.base_url == MAINNET_API_URL,
    )
    return exchange._post_action(action, signature, nonce)  # noqa: SLF001

def _resolve_position_for_close(context: CLIContext, coin: str) -> tuple[str, float, bool]:
    user = context.get_wallet_address()
    info = context.get_public_client()
    target = coin.strip()
    if not target:
        raise RuntimeError("coin must not be empty")

    with_prefix = ":" in target
    up = target.upper()
    matches: list[tuple[str, float]] = []
    states = run_blocking(_fetch_all_perp_states(info, user, _close_position_perp_dexs(context)))
    for state in states:
        for row in state.get("assetPositions", []):
            pos = row.get("position", {})
            pos_coin = str(pos.get("coin", ""))
            if not pos_coin:
                continue
            szi = float(pos.get("szi", 0) or 0)
            if szi == 0:
                continue
            suffix = pos_coin.split(":", 1)[1] if ":" in pos_coin else pos_coin
            if with_prefix:
                if pos_coin.lower() == target.lower():
                    matches.append((pos_coin, szi))
            elif pos_coin.upper() == up or suffix.upper() == up:
                matches.append((pos_coin, szi))

    if not matches:
        raise RuntimeError(f"No open position found for {coin}")
    if not with_prefix and len(matches) > 1:
        coins = ", ".join(sorted({m[0] for m in matches}))
        raise RuntimeError(
            f"Multiple open positions matched '{coin}': {coins}. "
            "Please specify the dex-prefixed symbol (e.g. xyz:TSLA)."
        )
    resolved_coin, szi = matches[0]
    return resolved_coin, abs(szi), (szi < 0)

def _fetch_builder_resolution_data(info: Any, dex_name: str) -> tuple[dict[str, Any], dict[str, Any]]:
    return info.meta(dex=dex_name), info.all_mids(dex=dex_name)

async def _fetch_all_builder_resolution_data(
    info: Any,
    dex_names: list[str],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    return await asyncio.gather(
        *(asyncio.to_thread(_fetch_builder_resolution_data, info, dex_name) for dex_name in dex_names)
    )

async def _fetch_all_perp_states(info: Any, user: str, dexs: list[str]) -> list[ClearinghouseState]:
    return await asyncio.gather(
        *(asyncio.to_thread(info.user_state, user, dex) for dex in dexs)
    )

def _fetch_orders(context: CLIContext, user: str) -> list[OpenOrderRow]:
    orders = context.get_public_client().open_orders(user)
    return [
        {
            "oid": o["oid"],
            "coin": o["coin"],
            "side": "Buy" if o["side"] in {"B", "buy", True} else "Sell",
            "sz": o["sz"],
            "limitPx": o["limitPx"],
            "timestamp": datetime.fromtimestamp(o["timestamp"] / 1000).isoformat(),
        }
        for o in orders
    ]

def _network_name(context: CLIContext) -> str:
    return "testnet" if context.config.testnet else "mainnet"

def _extract_twap_id(response: ExchangeSuccessEnvelope) -> Optional[int]:
    status = response.get("response", {}).get("data", {}).get("status", {})
    if not isinstance(status, dict):
        return None
    running = status.get("running")
    if not isinstance(running, dict):
        return None
    try:
        return int(running["twapId"])
    except (KeyError, TypeError, ValueError):
        return None

def _render_twap_orders(title: str, records: list[Any]) -> None:
    _render_table(
        title,
        ["TWAP ID", "Coin", "Side", "Total Size", "Minutes", "Submitted"],
        [
            [
                record.twap_id,
                record.coin,
                record.side,
                record.total_size,
                record.duration_minutes,
                record.submitted_at,
            ]
            for record in records
        ],
    )

@cli_command
def order_ls(
    ctx: CommandContext,
    user: Optional[str] = None,
    watch: bool = False,
) -> None:
    context = _ctx(ctx)
    address = user if user else context.get_wallet_address()
    tracked_twaps = list_twap_orders(network=_network_name(context), user=address)
    if watch:
        watch_loop(
            lambda: _fetch_orders(context, address),
            lambda rows: _render_table(
                "Open Orders",
                ["OID", "Coin", "Side", "Size", "Price", "Time"],
                [[r["oid"], r["coin"], r["side"], r["sz"], r["limitPx"], r["timestamp"]] for r in rows],
            ),
            as_json=_json(ctx),
        )
        return
    orders = _fetch_orders(context, address)
    if _json(ctx):
        if tracked_twaps:
            out({"openOrders": orders, "trackedTwaps": [record.__dict__ for record in tracked_twaps]}, True)
        else:
            out(orders, True)
    else:
        if tracked_twaps:
            _render_twap_orders("Tracked TWAP Orders", tracked_twaps)
        out(orders, False)
    _done(ctx)

@cli_command
def order_limit(
    ctx: CommandContext,
    side: str,
    size: str,
    coin: str,
    price: str,
    tif: str = "Gtc",
    reduce_only: bool = False,
    stake: Optional[float] = None,
    leverage: Optional[int] = None,
    cross: bool = False,
    isolated: bool = False,
) -> None:
    context = _ctx(ctx)
    _validate_side_mode_args(
        side=side,
        leverage=leverage,
        cross=cross,
        isolated=isolated,
        reduce_only=reduce_only,
    )
    resolved_coin = _resolve_coin_for_side(context, side, coin)
    client = context.get_wallet_client(perp_dexs=_wallet_perp_dexs_for_coin(resolved_coin))
    is_buy = side_is_buy(side)
    limit_price = validate_positive_number(price, "price")
    if stake is not None:
        position_notional = _stake_to_position_notional(stake, leverage)
        order_size = position_notional / limit_price
    else:
        order_size = validate_positive_number(size, "size")
    order_size = _normalize_size_for_coin(context, resolved_coin, order_size)
    lev_result = _maybe_update_leverage(
        context=context,
        coin=resolved_coin,
        leverage=leverage,
        cross=cross,
        isolated=isolated,
        emit_warning=not _json(ctx),
    )
    result = client.order(
        resolved_coin,
        is_buy,
        order_size,
        limit_price,
        {"limit": {"tif": normalize_tif(tif)}},
        reduce_only=reduce_only,
    )
    if _json(ctx):
        out({"leverageUpdate": lev_result, "order": result} if lev_result is not None else result, True)
    else:
        _print_leverage_update(lev_result, coin, leverage, cross or not isolated)
        _print_order_feedback(
            result=result,
            coin=coin,
            side="buy" if is_buy else "sell",
            order_kind="Limit",
            stake=stake,
        )
    _done(ctx)

@cli_command
def order_market(
    ctx: CommandContext,
    side: str,
    size: str,
    coin: str,
    reduce_only: bool = False,
    slippage: Optional[float] = None,
    stake: Optional[float] = None,
    leverage: Optional[int] = None,
    cross: bool = False,
    isolated: bool = False,
) -> None:
    context = _ctx(ctx)
    _validate_side_mode_args(
        side=side,
        leverage=leverage,
        cross=cross,
        isolated=isolated,
        reduce_only=reduce_only,
    )
    resolved_coin = _resolve_coin_for_side(context, side, coin)
    client = context.get_wallet_client(perp_dexs=_wallet_perp_dexs_for_coin(resolved_coin))
    is_buy = side_is_buy(side)
    cfg = get_order_config()
    slippage_pct = (slippage if slippage is not None else float(cfg["slippage"])) / 100
    mids_cache: Optional[dict[str, str]] = None
    if stake is not None:
        mids_cache = _mids_for_coin(context, resolved_coin)
        mid = float(mids_cache[resolved_coin])
        position_notional = _stake_to_position_notional(stake, leverage)
        order_size = position_notional / mid
    else:
        order_size = validate_positive_number(size, "size")
    order_size = _normalize_size_for_coin(context, resolved_coin, order_size)
    lev_result = _maybe_update_leverage(
        context=context,
        coin=resolved_coin,
        leverage=leverage,
        cross=cross,
        isolated=isolated,
        emit_warning=not _json(ctx),
    )

    if reduce_only:
        mids = mids_cache if mids_cache is not None else _mids_for_coin(context, resolved_coin)
        mid = float(mids[resolved_coin])
        price = mid * (1 + slippage_pct) if is_buy else mid * (1 - slippage_pct)
        result = client.order(
            resolved_coin,
            is_buy,
            order_size,
            price,
            {"limit": {"tif": "Ioc"}},
            reduce_only=True,
        )
    else:
        result = client.market_open(
            resolved_coin,
            is_buy,
            order_size,
            slippage=slippage_pct,
        )

    if _json(ctx):
        out({"leverageUpdate": lev_result, "order": result} if lev_result is not None else result, True)
    else:
        _print_leverage_update(lev_result, coin, leverage, cross or not isolated)
        _print_order_feedback(
            result=result,
            coin=coin,
            side="buy" if is_buy else "sell",
            order_kind="Market",
            stake=stake,
        )
    _done(ctx)

@cli_command
def order_market_close(
    ctx: CommandContext,
    coin: str,
    slippage: Optional[float] = None,
    ratio: float = 1.0,
) -> None:
    if ratio <= 0 or ratio > 1:
        raise RuntimeError("ratio must be > 0 and <= 1")
    context = _ctx(ctx)
    resolved_coin, order_size, is_buy = _resolve_position_for_close(context, coin)
    client = context.get_wallet_client(perp_dexs=_wallet_perp_dexs_for_coin(resolved_coin))
    close_size = order_size * ratio
    cfg = get_order_config()
    slippage_pct = (slippage if slippage is not None else float(cfg["slippage"])) / 100
    result = client.market_close(
        resolved_coin,
        sz=_normalize_size_for_coin(context, resolved_coin, close_size),
        slippage=slippage_pct,
    )
    if _json(ctx):
        out(result, True)
    else:
        _print_order_feedback(
            result=result,
            coin=coin,
            side="buy" if is_buy else "sell",
            order_kind="Market close",
            stake=None,
        )
    _done(ctx)

@cli_command
def order_tpsl(
    ctx: CommandContext,
    coin: str,
    tp: Optional[float] = None,
    sl: Optional[float] = None,
    ratio: float = 1.0,
) -> None:
    if tp is None and sl is None:
        raise RuntimeError("Specify at least one of --tp or --sl")
    if tp is not None and tp <= 0:
        raise RuntimeError("tp must be a positive number")
    if sl is not None and sl <= 0:
        raise RuntimeError("sl must be a positive number")
    if ratio <= 0 or ratio > 1:
        raise RuntimeError("ratio must be > 0 and <= 1")

    context = _ctx(ctx)
    resolved_coin, position_size, is_buy_to_close = _resolve_position_for_close(context, coin)
    client = context.get_wallet_client(perp_dexs=_wallet_perp_dexs_for_coin(resolved_coin))
    protected_size = _normalize_size_for_coin(context, resolved_coin, position_size * ratio)

    results: dict[str, Any] = {
        "coin": coin,
        "resolvedCoin": resolved_coin,
        "closeSide": "buy" if is_buy_to_close else "sell",
        "size": protected_size,
        "ratio": ratio,
    }
    if tp is not None:
        tp_order_type = {"trigger": {"triggerPx": tp, "isMarket": True, "tpsl": "tp"}}
        results["tp"] = client.order(
            resolved_coin,
            is_buy_to_close,
            protected_size,
            tp,
            tp_order_type,
            reduce_only=True,
        )
    if sl is not None:
        sl_order_type = {"trigger": {"triggerPx": sl, "isMarket": True, "tpsl": "sl"}}
        results["sl"] = client.order(
            resolved_coin,
            is_buy_to_close,
            protected_size,
            sl,
            sl_order_type,
            reduce_only=True,
        )

    if _json(ctx):
        out({"tpsl": results}, True)
    else:
        print("✅ TP/SL orders submitted")
        print(f"\nAsset: {coin}")
        print(f"Close side: {'BUY' if is_buy_to_close else 'SELL'}")
        print(f"Protected size: {protected_size}")
        if tp is not None:
            print(f"TP trigger: {tp}")
        if sl is not None:
            print(f"SL trigger: {sl}")
    _done(ctx)

@cli_command
def order_twap(
    ctx: CommandContext,
    side: str,
    size: str,
    coin: str,
    interval: str,
    stake: Optional[float] = None,
    reduce_only: bool = False,
    randomize: bool = False,
    leverage: Optional[int] = None,
    cross: bool = False,
    isolated: bool = False,
) -> None:
    context = _ctx(ctx)
    if side_uses_spot(side):
        raise RuntimeError("TWAP is only supported with long/short")
    resolved_coin = _resolve_coin_for_side(context, side, coin)
    is_buy = side_is_buy(side)
    if stake is not None:
        mids = _mids_for_coin(context, resolved_coin)
        mid = float(mids[resolved_coin])
        position_notional = _stake_to_position_notional(stake, leverage)
        total_size = position_notional / mid
    else:
        total_size = validate_positive_number(size, "size")
    total_size = _normalize_size_for_coin(context, resolved_coin, total_size)
    minutes, compatibility_orders = _parse_twap_interval(interval)
    lev_result = _maybe_update_leverage(
        context=context,
        coin=resolved_coin,
        leverage=leverage,
        cross=cross,
        isolated=isolated,
        emit_warning=not _json(ctx),
    )
    response = _place_native_twap(
        context=context,
        coin=resolved_coin,
        is_buy=is_buy,
        size=total_size,
        minutes=minutes,
        reduce_only=reduce_only,
        randomize=randomize,
    )
    result = {
        "twap": {
            "side": "buy" if is_buy else "sell",
            "coin": coin,
            "resolvedCoin": resolved_coin,
            "totalSize": total_size,
            "stake": stake,
            "durationMinutes": minutes,
            "compatibilityInput": interval if compatibility_orders > 1 else None,
            "randomize": randomize,
            "reduceOnly": reduce_only,
            "leverageUpdate": lev_result,
            "response": response,
        }
    }
    twap_id = _extract_twap_id(response)
    if twap_id is not None:
        # TODO: Replace the local TWAP registry with an official info endpoint once
        # Hyperliquid exposes an API for listing/retrieving active TWAP orders by twapId.
        register_twap_order(
            network=_network_name(context),
            user=context.get_wallet_address(),
            coin=coin,
            resolved_coin=resolved_coin,
            twap_id=twap_id,
            side="buy" if is_buy else "sell",
            total_size=total_size,
            duration_minutes=minutes,
            randomize=randomize,
            reduce_only=reduce_only,
        )
        result["twap"]["twapId"] = twap_id
    if _json(ctx):
        out(result, True)
    else:
        _print_leverage_update(lev_result, coin, leverage, cross or not isolated)
        status = response.get("response", {}).get("data", {}).get("status", {})
        if isinstance(status, dict) and "error" in status:
            print("❌ TWAP order rejected")
            print(f"\nReason: {status.get('error')}")
        else:
            print("✅ TWAP order submitted")
            print(f"\nAsset: {coin}")
            print(f"Side: {'BUY' if is_buy else 'SELL'}")
            print(f"Total size: {total_size} {coin}")
            print(f"Duration: {minutes} min")
            print(f"Randomize: {'on' if randomize else 'off'}")
            if twap_id is not None:
                print(f"TWAP ID: {twap_id}")
                print("Manage it with 'hl order ls' or 'hl order twap-cancel'.")
    _done(ctx)

@cli_command
def order_twap_cancel(ctx: CommandContext, coin: Optional[str] = None, twap_id: Optional[str] = None) -> None:
    context = _ctx(ctx)
    address = context.get_wallet_address()
    if coin is not None and twap_id is not None:
        resolved_coin = coin
        twap_num = validate_positive_integer(twap_id, "twap_id")
    else:
        records = list_twap_orders(network=_network_name(context), user=address)
        if coin is not None:
            records = [record for record in records if coin in {record.coin, record.resolved_coin}]
        if not records:
            raise RuntimeError("No tracked active TWAP orders found")
        if twap_id is not None:
            twap_num = validate_positive_integer(twap_id, "twap_id")
            record = find_twap_order(
                network=_network_name(context),
                user=address,
                twap_id=twap_num,
                coin=coin,
            )
            if record is None:
                raise RuntimeError(f"Tracked TWAP {twap_num} not found")
            resolved_coin = record.resolved_coin
        elif _json(ctx):
            latest = records[0]
            resolved_coin = latest.resolved_coin
            twap_num = latest.twap_id
        else:
            _render_twap_orders("Tracked TWAP Orders", records)
            selected = input("Select TWAP ID to cancel: ").strip()
            twap_num = validate_positive_integer(selected, "twap_id")
            record = find_twap_order(
                network=_network_name(context),
                user=address,
                twap_id=twap_num,
            )
            if record is None:
                raise RuntimeError(f"Tracked TWAP {twap_num} not found")
            resolved_coin = record.resolved_coin
    response = _cancel_native_twap(context=context, coin=resolved_coin, twap_id=twap_num)
    status = response.get("response", {}).get("data", {}).get("status", {})
    if not (isinstance(status, dict) and status.get("error")):
        mark_twap_cancelled(
            network=_network_name(context),
            user=address,
            twap_id=twap_num,
            coin=resolved_coin,
        )
    out({"twapCancel": {"coin": resolved_coin, "twapId": twap_num, "response": response}}, _json(ctx))
    _done(ctx)

@cli_command
def order_cancel(ctx: CommandContext, oid: Optional[str] = None) -> None:
    context = _ctx(ctx)
    user = context.get_wallet_address()
    exchange = context.get_wallet_client()
    orders = context.get_public_client().open_orders(user)

    if not orders:
        if _json(ctx):
            out(
                {
                    "cancelled": False,
                    "reason": "no_open_orders",
                    "message": "No open orders to cancel",
                },
                True,
            )
        else:
            out_success("No open orders to cancel")
        _done(ctx)
        return

    if oid is None:
        if _json(ctx):
            latest = max(orders, key=lambda x: int(x.get("timestamp", 0)))
            oid = str(latest["oid"])
        else:
            _render_table(
                "Open Orders",
                ["OID", "Coin", "Side", "Size", "Price"],
                [[o["oid"], o["coin"], o["side"], o["sz"], o["limitPx"]] for o in orders],
            )
            oid = input("Select OID to cancel: ").strip()

    order_id = validate_positive_integer(oid, "oid")
    target = next((o for o in orders if int(o["oid"]) == order_id), None)
    if not target:
        raise RuntimeError(f"Order {order_id} not found")

    result = exchange.cancel(target["coin"], order_id)
    out(result, _json(ctx))
    _done(ctx)

@cli_command
def order_cancel_all(
    ctx: CommandContext,
    yes: bool = False,
    coin: Optional[str] = None,
) -> None:
    context = _ctx(ctx)
    user = context.get_wallet_address()
    exchange = context.get_wallet_client()
    orders = context.get_public_client().open_orders(user)
    if coin:
        orders = [o for o in orders if o["coin"] == coin]
    if not orders:
        if _json(ctx):
            out(
                {
                    "cancelled": 0,
                    "reason": "no_open_orders",
                    "message": "No open orders to cancel",
                },
                True,
            )
        else:
            out_success("No open orders to cancel")
        _done(ctx)
        return
    if not yes and not _confirm(f"Cancel {len(orders)} orders?", False):
        if _json(ctx):
            out({"cancelled": 0, "reason": "user_cancelled", "message": "Cancelled"}, True)
        else:
            out_success("Cancelled")
        _done(ctx)
        return
    result = exchange.bulk_cancel([{"coin": o["coin"], "oid": int(o["oid"])} for o in orders])
    out(result, _json(ctx))
    _done(ctx)

@cli_command
def order_set_leverage(
    ctx: CommandContext,
    coin: str,
    leverage: str,
    cross: bool = False,
    isolated: bool = False,
) -> None:
    context = _ctx(ctx)
    if cross and isolated:
        raise RuntimeError("Use only one of --cross or --isolated")
    is_cross = cross or not isolated
    requested = validate_positive_integer(leverage, "leverage")
    result = _update_leverage_with_fallback(
        context=context,
        coin=coin,
        leverage=requested,
        is_cross=is_cross,
        emit_warning=not _json(ctx),
    )
    if _json(ctx):
        out({"requestedLeverage": requested, "result": result}, True)
    else:
        if result.get("status") == "ok":
            print("✅ Leverage updated")
            print(f"\nAsset: {coin}")
            print(f"Leverage: {requested}x")
            print(f"Margin type: {'cross' if is_cross else 'isolated'}")
        else:
            print("❌ Leverage update failed")
            print(f"\nReason: {result.get('response')}")
    _done(ctx)

@cli_command
def order_configure(ctx: CommandContext, slippage: Optional[float] = None) -> None:
    if slippage is None:
        out(get_order_config(), _json(ctx))
    else:
        if slippage < 0:
            raise RuntimeError("Slippage must be a non-negative number")
        out(update_order_config(slippage=slippage), _json(ctx))
    _done(ctx)
