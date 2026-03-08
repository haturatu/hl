from datetime import datetime
from decimal import Decimal, ROUND_DOWN
from typing import Any, Optional

import typer
from hyperliquid.utils.constants import MAINNET_API_URL
from hyperliquid.utils.signing import float_to_wire, get_timestamp_ms, sign_l1_action

from .cli_runtime import cli_command, cli_context, confirm, finish_command, json_output_enabled, render_table
from .context import CLIContext
from .order_config import get_order_config, update_order_config
from .output import out, out_success
from .validators import (
    normalize_side,
    normalize_tif,
    validate_positive_integer,
    validate_positive_number,
)
from .watch import watch_loop

order_app = typer.Typer(help="Order management and trading", no_args_is_help=True)


def _ctx(ctx: typer.Context) -> CLIContext:
    return cli_context(ctx)


def _json(ctx: typer.Context) -> bool:
    return json_output_enabled(ctx)


def _done(ctx: typer.Context) -> None:
    finish_command(ctx)


def _confirm(message: str, default: bool = False) -> bool:
    return confirm(message, default)


def _render_table(title: str, columns: list[str], rows: list[list[Any]]) -> None:
    render_table(title, columns, rows)


def _format_usd(value: str | float | int | None) -> str:
    try:
        n = float(value)  # type: ignore[arg-type]
        return f"${n:,.2f}"
    except Exception:
        return f"${value}" if value is not None else "-"


def _extract_statuses(result: dict[str, Any]) -> list[dict[str, Any] | str]:
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
    result: dict[str, Any],
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
    first_filled = None
    first_resting = None
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


def _is_invalid_leverage_response(resp: Any) -> bool:
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
    wallet = context.get_wallet_client()
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
    exchange = context.get_wallet_client()
    asset = exchange.info.name_to_asset(coin)
    sz_decimals = int(exchange.info.asset_to_sz_decimals[asset])

    q = Decimal(1).scaleb(-sz_decimals)
    d = Decimal(str(raw_size)).quantize(q, rounding=ROUND_DOWN)
    if d <= 0:
        raise RuntimeError(f"size too small for {coin}; minimum unit is 1e-{sz_decimals}")
    return float(d)


def _resolve_tradable_coin(context: CLIContext, coin: str) -> str:
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
    for dex_item in info.perp_dexs():
        dex_name = str(dex_item.get("name", "")) if isinstance(dex_item, dict) else ""
        if not dex_name:
            continue
        dex_mids = info.all_mids(dex=dex_name)
        meta = info.meta(dex=dex_name)
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
    if token_index is not None:
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
            ),
            None,
        )
        if fallback and fallback in mids:
            return fallback

    raise RuntimeError(f"Coin not found: {coin}")


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
    exchange = context.get_wallet_client()
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
    exchange = context.get_wallet_client()
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
    for dex in context.get_perp_dexs():
        state = info.user_state(user, dex=dex)
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


def _fetch_orders(context: CLIContext, user: str) -> list[dict[str, Any]]:
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


@order_app.command("ls")
@cli_command
def order_ls(
    ctx: typer.Context,
    user: Optional[str] = typer.Option(None, "--user"),
    watch: bool = typer.Option(False, "-w", "--watch"),
) -> None:
    context = _ctx(ctx)
    address = user if user else context.get_wallet_address()
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
    out(_fetch_orders(context, address), _json(ctx))
    _done(ctx)


@order_app.command("limit")
@cli_command
def order_limit(
    ctx: typer.Context,
    side: str,
    size: str,
    coin: str,
    price: str,
    tif: str = typer.Option("Gtc", "--tif"),
    reduce_only: bool = typer.Option(False, "--reduce-only"),
    stake: Optional[float] = typer.Option(None, "--stake", help="USD margin to derive size (size = stake * leverage / price)"),
    leverage: Optional[int] = typer.Option(None, "--leverage", help="Set leverage before placing order"),
    cross: bool = typer.Option(False, "--cross", help="Use cross margin when setting leverage"),
    isolated: bool = typer.Option(False, "--isolated", help="Use isolated margin when setting leverage"),
) -> None:
    context = _ctx(ctx)
    client = context.get_wallet_client()
    resolved_coin = _resolve_tradable_coin(context, coin)
    is_buy = normalize_side(side) == "buy"
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


@order_app.command("market")
@cli_command
def order_market(
    ctx: typer.Context,
    side: str,
    size: str,
    coin: str,
    reduce_only: bool = typer.Option(False, "--reduce-only"),
    slippage: Optional[float] = typer.Option(None, "--slippage"),
    stake: Optional[float] = typer.Option(None, "--stake", help="USD margin to derive size (size = stake * leverage / price)"),
    leverage: Optional[int] = typer.Option(None, "--leverage", help="Set leverage before placing order"),
    cross: bool = typer.Option(False, "--cross", help="Use cross margin when setting leverage"),
    isolated: bool = typer.Option(False, "--isolated", help="Use isolated margin when setting leverage"),
) -> None:
    context = _ctx(ctx)
    client = context.get_wallet_client()
    resolved_coin = _resolve_tradable_coin(context, coin)
    is_buy = normalize_side(side) == "buy"
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
    ctx: typer.Context,
    coin: str,
    slippage: Optional[float] = None,
    ratio: float = 1.0,
) -> None:
    if ratio <= 0 or ratio > 1:
        raise RuntimeError("ratio must be > 0 and <= 1")
    context = _ctx(ctx)
    client = context.get_wallet_client()
    resolved_coin, order_size, is_buy = _resolve_position_for_close(context, coin)
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


order_app.command("market-close", hidden=True)(order_market_close)


@order_app.command("tpsl")
@cli_command
def order_tpsl(
    ctx: typer.Context,
    coin: str,
    tp: Optional[float] = typer.Option(None, "--tp", help="Take-profit trigger price"),
    sl: Optional[float] = typer.Option(None, "--sl", help="Stop-loss trigger price"),
    ratio: float = typer.Option(1.0, "--ratio", help="Position ratio to protect (0 < ratio <= 1)"),
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
    client = context.get_wallet_client()
    resolved_coin, position_size, is_buy_to_close = _resolve_position_for_close(context, coin)
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


@order_app.command("twap")
@cli_command
def order_twap(
    ctx: typer.Context,
    side: str,
    size: str,
    coin: str,
    interval: str,
    stake: Optional[float] = typer.Option(None, "--stake", help="USD margin to derive total TWAP size"),
    reduce_only: bool = typer.Option(False, "--reduce-only"),
    randomize: bool = typer.Option(False, "--randomize", help="Enable randomized execution timing"),
    leverage: Optional[int] = typer.Option(None, "--leverage", help="Set leverage before placing order"),
    cross: bool = typer.Option(False, "--cross", help="Use cross margin when setting leverage"),
    isolated: bool = typer.Option(False, "--isolated", help="Use isolated margin when setting leverage"),
) -> None:
    context = _ctx(ctx)
    resolved_coin = _resolve_tradable_coin(context, coin)
    is_buy = normalize_side(side) == "buy"
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
    _done(ctx)


@order_app.command("twap-cancel")
@cli_command
def order_twap_cancel(ctx: typer.Context, coin: str, twap_id: str) -> None:
    context = _ctx(ctx)
    twap_num = validate_positive_integer(twap_id, "twap_id")
    response = _cancel_native_twap(context=context, coin=coin, twap_id=twap_num)
    out({"twapCancel": {"coin": coin, "twapId": twap_num, "response": response}}, _json(ctx))
    _done(ctx)


@order_app.command("cancel")
@cli_command
def order_cancel(ctx: typer.Context, oid: Optional[str] = None) -> None:
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


@order_app.command("cancel-all")
@cli_command
def order_cancel_all(
    ctx: typer.Context,
    yes: bool = typer.Option(False, "-y", "--yes"),
    coin: Optional[str] = typer.Option(None, "--coin"),
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


@order_app.command("set-leverage")
@cli_command
def order_set_leverage(
    ctx: typer.Context,
    coin: str,
    leverage: str,
    cross: bool = typer.Option(False, "--cross"),
    isolated: bool = typer.Option(False, "--isolated"),
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


@order_app.command("configure")
@cli_command
def order_configure(ctx: typer.Context, slippage: Optional[float] = typer.Option(None, "--slippage")) -> None:
    if slippage is None:
        out(get_order_config(), _json(ctx))
    else:
        if slippage < 0:
            raise RuntimeError("Slippage must be a non-negative number")
        out(update_order_config(slippage=slippage), _json(ctx))
    _done(ctx)
