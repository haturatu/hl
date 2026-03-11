import asyncio
import json
import os
import time
from queue import Empty, Queue
from datetime import datetime
from typing import Any, Optional

from eth_account import Account as EthAccount
from hyperliquid.info import Info

from ..cli.markets_tui import run_markets_tui
from ..cli.runtime import (
    cli_command,
    cli_context,
    console,
    confirm,
    finish_command,
    json_output_enabled,
    render_table,
    run_blocking,
)
from ..core.context import CLIContext
from ..infra.db import (
    create_account,
    delete_account,
    get_account_by_alias,
    get_account_count,
    get_all_accounts,
    is_alias_taken,
    set_default_account,
)
from .order import (
    _mids_for_coin,
    _resolve_tradable_coin,
    order_cancel,
    order_cancel_all,
    order_configure,
    order_limit,
    order_ls,
    order_market,
    order_market_close,
    order_set_leverage,
    order_tpsl,
    order_twap,
    order_twap_cancel,
)
from ..utils.output import out
from ..utils.validators import (
    normalize_private_key,
    validate_address,
)
from ..utils.watch import watch_loop

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


MARKET_SORT_FIELDS = {"volume", "oi", "price", "change", "funding", "coin"}


def _normalize_market_sort(sort_by: str) -> str:
    value = sort_by.strip().lower()
    if value not in MARKET_SORT_FIELDS:
        allowed = ", ".join(sorted(MARKET_SORT_FIELDS))
        raise RuntimeError(f"invalid sort field: {sort_by} (expected one of: {allowed})")
    return value


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _sort_market_rows(rows: dict[str, list[dict[str, Any]]], sort_by: str) -> dict[str, list[dict[str, Any]]]:
    sort_by = _normalize_market_sort(sort_by)

    def numeric_value(row: dict[str, Any], key: str) -> float | None:
        return _to_float(row.get(key))

    def sort_rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
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

        def key(row: dict[str, Any]) -> tuple[int, float]:
            value = numeric_value(row, field)
            if value is None:
                return (1, 0.0)
            return (0, -value)

        return sorted(items, key=key)

    return {
        "perpMarkets": sort_rows(rows["perpMarkets"]),
        "spotMarkets": sort_rows(rows["spotMarkets"]),
    }


def _filter_market_rows_by_category(rows: dict[str, list[dict[str, Any]]], category: Optional[str]) -> dict[str, list[dict[str, Any]]]:
    if category is None or category == "*":
        return rows
    needle = category.strip().lower()
    if not needle:
        return rows
    return {
        "perpMarkets": [
            row for row in rows["perpMarkets"] if str(row.get("category", "")).lower() == needle
        ],
        "spotMarkets": [],
    }


def _prepare_market_output(rows: dict[str, list[dict[str, Any]]], include_category: bool) -> dict[str, list[dict[str, Any]]]:
    if include_category:
        return rows

    def strip_category(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
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


def _print_account_add_guide() -> None:
    console.print("\n[bold]Wallet Setup Guide[/bold]")
    console.print("1. To add an API wallet:")
    console.print("   - Generate an API wallet private key on Hyperliquid")
    console.print("   - Mainnet: https://app.hyperliquid.xyz/API")
    console.print("   - Testnet: https://app.hyperliquid-testnet.xyz/API")
    console.print("   - Run: [bold]hl account add[/bold] -> choose [bold]1[/bold]")
    console.print("")
    console.print("2. To add a read-only wallet:")
    console.print("   - Run: [bold]hl account add[/bold] -> choose [bold]2[/bold]")
    console.print("   - Enter the wallet address to monitor (0x...)")
    console.print("")
    console.print("Verification commands:")
    console.print(" - [bold]hl account ls[/bold]")
    console.print(" - [bold]hl account set-default <alias>[/bold]\n")


def _network_name(context: CLIContext) -> str:
    return "testnet" if context.config.testnet else "mainnet"


@cli_command
def account_add(ctx: Any) -> None:
    context = _ctx(ctx)
    is_testnet = context.config.testnet
    network = _network_name(context)

    print("\n=== Add New Account ===\n")
    _print_account_add_guide()
    print("1) Use existing API wallet")
    print("2) Add read-only account")
    choice = input("Select setup method [1/2]: ").strip()

    if choice == "1":
        api_url = "https://app.hyperliquid-testnet.xyz/API" if is_testnet else "https://app.hyperliquid.xyz/API"
        print(f"\nVisit {api_url} and generate an API wallet key.\n")
        api_key = normalize_private_key(input("Enter API wallet private key: ").strip())

        info = context.get_public_client()
        api_wallet_addr = EthAccount.from_key(api_key).address
        role = info.user_role(api_wallet_addr)
        if role.get("role") != "agent":
            raise RuntimeError("This key is not registered as an API wallet (agent) on Hyperliquid")
        user_address = role["data"]["user"]

        while True:
            alias = input("Alias: ").strip()
            if not alias:
                print("Alias cannot be empty.")
                continue
            if is_alias_taken(alias, network):
                print(f'Alias "{alias}" is already taken.')
                continue
            break

        set_as_default = get_account_count(network) == 0 or _confirm("Set as default account?", True)
        created = create_account(
            alias=alias,
            network=network,
            user_address=user_address,
            account_type="api_wallet",
            api_wallet_private_key=api_key,
            api_wallet_public_key=api_wallet_addr,
            set_as_default=set_as_default,
        )
        data = created.__dict__.copy()
        data["api_wallet_private_key"] = "[REDACTED]"
        out(data, _json(ctx))
    elif choice == "2":
        user_address = validate_address(input("Wallet address to watch: ").strip())
        while True:
            alias = input("Alias: ").strip()
            if not alias:
                print("Alias cannot be empty.")
                continue
            if is_alias_taken(alias, network):
                print(f'Alias "{alias}" is already taken.')
                continue
            break
        set_as_default = get_account_count(network) == 0 or _confirm("Set as default account?", True)
        created = create_account(
            alias=alias,
            network=network,
            user_address=user_address,
            account_type="readonly",
            set_as_default=set_as_default,
        )
        out(created.__dict__, _json(ctx))
    else:
        raise RuntimeError("Invalid selection")
    _done(ctx)


@cli_command
def account_ls(ctx: Any) -> None:
    context = _ctx(ctx)
    accounts = get_all_accounts(_network_name(context))
    if _json(ctx):
        out([a.__dict__ for a in accounts], True)
    else:
        if not accounts:
            print("No accounts found. Run 'hl account add'.")
        else:
            _render_table(
                "Accounts",
                ["*", "Alias", "Address", "Type", "API Wallet"],
                [
                    [
                        "*" if a.is_default else "",
                        a.alias,
                        _format_address(a.user_address),
                        a.type,
                        _format_address(a.api_wallet_public_key) if a.api_wallet_public_key else "-",
                    ]
                    for a in accounts
                ],
            )
    _done(ctx)


@cli_command
def account_set_default(ctx: Any, alias: str) -> None:
    context = _ctx(ctx)
    network = _network_name(context)
    if not get_account_by_alias(alias, network):
        raise RuntimeError(f'Account with alias "{alias}" not found')
    updated = set_default_account(alias, network)
    out(updated.__dict__, _json(ctx))
    _done(ctx)


@cli_command
def account_remove(
    ctx: Any,
    alias: str,
    force: bool = False,
) -> None:
    context = _ctx(ctx)
    network = _network_name(context)
    existing = get_account_by_alias(alias, network)
    if not existing:
        raise RuntimeError(f'Account with alias "{alias}" not found')
    if not force and not _confirm(f'Remove account "{alias}" ({existing.user_address})?', False):
        print("Cancelled.")
        raise SystemExit(0)
    ok = delete_account(alias, network)
    if not ok:
        raise RuntimeError("Failed to remove account")
    out({"deleted": True, "alias": alias}, _json(ctx))
    _done(ctx)


def _fetch_positions(context: CLIContext, user: str) -> dict[str, Any]:
    return run_blocking(_fetch_positions_async(context, user))


def _account_perp_dexs(context: CLIContext) -> list[str]:
    # Testnet uses main perp only to avoid rate-limiting on bulk per-dex account reads.
    if context.config.testnet:
        return [""]
    return context.get_perp_dexs()


async def _fetch_positions_async(context: CLIContext, user: str) -> dict[str, Any]:
    info = context.get_public_client()
    states = await asyncio.gather(
        *(asyncio.to_thread(info.user_state, user, dex) for dex in _account_perp_dexs(context))
    )
    positions: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []

    for state in states:
        summaries.append(state["marginSummary"])
        positions.extend(
            [
                {
                    "coin": p["position"]["coin"],
                    "size": p["position"]["szi"],
                    "entryPx": p["position"].get("entryPx"),
                    "positionValue": p["position"].get("positionValue"),
                    "unrealizedPnl": p["position"].get("unrealizedPnl"),
                    "leverage": f"{p['position']['leverage']['value']}x {p['position']['leverage']['type']}",
                    "liquidationPx": p["position"].get("liquidationPx") or "-",
                }
                for p in state["assetPositions"]
                if float(p["position"]["szi"]) != 0
            ]
        )

    account_value = sum(float(s.get("accountValue", 0) or 0) for s in summaries)
    margin_used = sum(float(s.get("totalMarginUsed", 0) or 0) for s in summaries)
    return {
        "positions": positions,
        "marginSummary": {
            "accountValue": f"{account_value:.8f}",
            "totalMarginUsed": f"{margin_used:.8f}",
        },
    }


@cli_command
def account_positions(
    ctx: Any,
    user: Optional[str] = None,
    watch: bool = False,
) -> None:
    context = _ctx(ctx)
    address = validate_address(user) if user else context.get_wallet_address()
    if watch:
        watch_loop(
            lambda: _fetch_positions(context, address),
            lambda data: _render_table(
                "Positions",
                ["Coin", "Size", "Entry", "Value", "PnL", "Leverage", "Liq"],
                [
                    [
                        p["coin"],
                        p["size"],
                        p["entryPx"],
                        p["positionValue"],
                        p["unrealizedPnl"],
                        p["leverage"],
                        p["liquidationPx"],
                    ]
                    for p in data["positions"]
                ],
            ),
            as_json=_json(ctx),
        )
        return
    out(_fetch_positions(context, address), _json(ctx))
    _done(ctx)


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


@cli_command
def account_orders(
    ctx: Any,
    user: Optional[str] = None,
    watch: bool = False,
) -> None:
    context = _ctx(ctx)
    address = validate_address(user) if user else context.get_wallet_address()
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


def _fetch_balances(context: CLIContext, user: str) -> dict[str, Any]:
    return run_blocking(_fetch_balances_async(context, user))


async def _fetch_balances_async(context: CLIContext, user: str) -> dict[str, Any]:
    info = context.get_public_client()
    perp_task = asyncio.to_thread(info.user_state, user)
    spot_task = asyncio.to_thread(info.spot_user_state, user)
    perp, spot = await asyncio.gather(perp_task, spot_task)
    balances = []
    for b in spot["balances"]:
        if float(b["total"]) == 0:
            continue
        total = float(b["total"])
        hold = float(b["hold"])
        balances.append(
            {
                "token": b["coin"],
                "total": b["total"],
                "hold": b["hold"],
                "available": f"{total - hold}",
            }
        )
    return {"spotBalances": balances, "perpBalance": perp["marginSummary"]["accountValue"]}


async def _fetch_portfolio_async(context: CLIContext, user: str) -> dict[str, Any]:
    info = context.get_public_client()
    perp_tasks = [
        asyncio.to_thread(info.user_state, user, dex)
        for dex in _account_perp_dexs(context)
    ]
    spot_task = asyncio.to_thread(info.spot_user_state, user)
    *perp_states, spot = await asyncio.gather(*perp_tasks, spot_task)

    positions: list[dict[str, Any]] = []
    for state in perp_states:
        positions.extend(
            [
                {
                    "coin": p["position"]["coin"],
                    "size": p["position"]["szi"],
                    "entryPx": p["position"].get("entryPx"),
                    "positionValue": p["position"].get("positionValue"),
                    "unrealizedPnl": p["position"].get("unrealizedPnl"),
                    "leverage": f"{p['position']['leverage']['value']}x {p['position']['leverage']['type']}",
                    "liquidationPx": p["position"].get("liquidationPx") or "-",
                }
                for p in state["assetPositions"]
                if float(p["position"]["szi"]) != 0
            ]
        )

    spot_balances = []
    for b in spot["balances"]:
        if float(b["total"]) == 0:
            continue
        total = float(b["total"])
        hold = float(b["hold"])
        spot_balances.append(
            {
                "token": b["coin"],
                "total": b["total"],
                "hold": b["hold"],
                "available": f"{total - hold}",
            }
        )

    account_value = sum(float(s["marginSummary"]["accountValue"]) for s in perp_states)
    margin_used = sum(float(s["marginSummary"]["totalMarginUsed"]) for s in perp_states)
    return {
        "positions": positions,
        "spotBalances": spot_balances,
        "accountValue": f"{account_value:.8f}",
        "totalMarginUsed": f"{margin_used:.8f}",
    }


@cli_command
def account_balances(
    ctx: Any,
    user: Optional[str] = None,
    watch: bool = False,
) -> None:
    context = _ctx(ctx)
    address = validate_address(user) if user else context.get_wallet_address()
    if watch:
        watch_loop(
            lambda: _fetch_balances(context, address),
            lambda data: _render_table(
                f"Balances (Perp USD: {data['perpBalance']})",
                ["Token", "Total", "Hold", "Available"],
                [[b["token"], b["total"], b["hold"], b["available"]] for b in data["spotBalances"]],
            ),
            as_json=_json(ctx),
        )
        return
    out(_fetch_balances(context, address), _json(ctx))
    _done(ctx)


@cli_command
def account_portfolio(
    ctx: Any,
    user: Optional[str] = None,
    watch: bool = False,
) -> None:
    context = _ctx(ctx)
    address = validate_address(user) if user else context.get_wallet_address()

    def fetch() -> dict[str, Any]:
        return run_blocking(_fetch_portfolio_async(context, address))

    if watch:
        watch_loop(
            fetch,
            lambda d: (
                _render_table(
                    f"Portfolio AccountValue={d['accountValue']} MarginUsed={d['totalMarginUsed']}",
                    ["Coin", "Size", "Entry", "Value", "PnL", "Leverage"],
                    [
                        [
                            p["coin"],
                            p["size"],
                            p["entryPx"],
                            p["positionValue"],
                            p["unrealizedPnl"],
                            p["leverage"],
                        ]
                        for p in d["positions"]
                    ],
                ),
                _render_table(
                    "Spot Balances",
                    ["Token", "Total", "Hold", "Available"],
                    [
                        [b["token"], b["total"], b["hold"], b.get("available", "-")]
                        for b in d["spotBalances"]
                    ],
                ),
            ),
            as_json=_json(ctx),
        )
        return
    out(fetch(), _json(ctx))
    _done(ctx)


@cli_command
def asset_price(ctx: Any, coin: str, watch: bool = False) -> None:
    context = _ctx(ctx)

    def fetch() -> dict[str, str]:
        resolved_coin = _resolve_tradable_coin(context, coin)
        mids = _mids_for_coin(context, resolved_coin)
        if resolved_coin not in mids:
            raise RuntimeError(f"Coin not found: {coin}")
        return {"coin": coin, "price": mids[resolved_coin]}

    if watch:
        watch_loop(fetch, lambda d: print(f"{d['coin']}: {_format_price(d['price'])}"), as_json=_json(ctx))
        return
    out(fetch(), _json(ctx))
    _done(ctx)


@cli_command
def asset_book(ctx: Any, coin: str, watch: bool = False) -> None:
    context = _ctx(ctx)

    def fetch() -> dict[str, Any]:
        book = context.get_public_client().l2_snapshot(coin)
        return book

    def render_book(book: dict[str, Any]) -> None:
        bids = book.get("levels", [[], []])[0][:10]
        asks = book.get("levels", [[], []])[1][:10]
        _render_table("Asks", ["Price", "Size", "N"], [[x["px"], x["sz"], x["n"]] for x in asks[::-1]])
        _render_table("Bids", ["Price", "Size", "N"], [[x["px"], x["sz"], x["n"]] for x in bids])

    if watch:
        stream_info = Info(context.base_url, skip_ws=False)
        updates: Queue[dict[str, Any]] = Queue()
        subscription = {"type": "l2Book", "coin": coin}
        subscription_id = stream_info.subscribe(subscription, lambda msg: updates.put(msg["data"]))

        try:
            initial = fetch()
            if _json(ctx):
                print(json.dumps(initial, ensure_ascii=False))
            else:
                console.clear()
                render_book(initial)

            while True:
                try:
                    book = updates.get(timeout=30.0)
                except Empty:
                    continue
                if _json(ctx):
                    print(json.dumps(book, ensure_ascii=False))
                else:
                    console.clear()
                    render_book(book)
        except KeyboardInterrupt:
            return
        finally:
            try:
                stream_info.unsubscribe(subscription, subscription_id)
            except Exception:
                pass
            if stream_info.ws_manager is not None:
                try:
                    stream_info.ws_manager.stop()
                except Exception:
                    pass
        return
    out(fetch(), _json(ctx))
    _done(ctx)


@cli_command
def asset_leverage(
    ctx: Any,
    coin: str,
    user: Optional[str] = None,
    watch: bool = False,
) -> None:
    context = _ctx(ctx)
    address = validate_address(user) if user else context.get_wallet_address()

    def fetch() -> dict[str, Any]:
        info = context.get_public_client()
        state, meta, mids = run_blocking(
            _fetch_asset_leverage_inputs_async(info, address)
        )
        pos = next(
            (p["position"] for p in state["assetPositions"] if p["position"]["coin"] == coin),
            None,
        )
        m = next((m for m in meta["universe"] if m["name"] == coin), None)
        account_value = float(state["marginSummary"]["accountValue"])
        margin_used = float(state["marginSummary"]["totalMarginUsed"])
        return {
            "coin": coin,
            "markPx": mids.get(coin),
            "maxLeverage": (m or {}).get("maxLeverage", 0),
            "position": pos,
            "margin": {
                "accountValue": state["marginSummary"]["accountValue"],
                "totalMarginUsed": state["marginSummary"]["totalMarginUsed"],
                "availableMargin": f"{max(0.0, account_value - margin_used):.2f}",
            },
        }

    if watch:
        watch_loop(fetch, lambda d: out(d, False), as_json=_json(ctx))
        return
    out(fetch(), _json(ctx))
    _done(ctx)


async def _fetch_asset_leverage_inputs_async(info: Any, address: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    state_task = asyncio.to_thread(info.user_state, address)
    meta_task = asyncio.to_thread(info.meta)
    mids_task = asyncio.to_thread(info.all_mids)
    state, meta, mids = await asyncio.gather(state_task, meta_task, mids_task)
    return state, meta, mids


def _build_market_rows(context: CLIContext, spot_only: bool, perp_only: bool) -> dict[str, list[dict[str, Any]]]:
    return run_blocking(_build_market_rows_async(context, spot_only, perp_only))


def _safe_token_name(tokens: list[dict[str, Any]], index: Any, default: str = "?") -> str:
    if not isinstance(index, int) or index < 0 or index >= len(tokens):
        return default
    return str(tokens[index].get("name", default))


async def _build_market_rows_async(context: CLIContext, spot_only: bool, perp_only: bool) -> dict[str, list[dict[str, Any]]]:
    info = context.get_public_client()
    spot_task = asyncio.to_thread(info.spot_meta_and_asset_ctxs)
    perp_categories_task = asyncio.to_thread(info.post, "/info", {"type": "perpCategories"})
    (spot_meta, spot_ctxs), perp_categories_raw = await asyncio.gather(spot_task, perp_categories_task)
    perp_categories = {
        str(coin): str(category)
        for coin, category in perp_categories_raw
        if isinstance(coin, str) and isinstance(category, str)
    }

    spot_rows: list[dict[str, Any]] = []
    perp_rows: list[dict[str, Any]] = []

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
            c = ctx_map.get(pair["name"], {})
            prev = float(c.get("prevDayPx", 0) or 0)
            mark = float(c.get("markPx", 0) or 0)
            chg = ((mark - prev) / prev * 100) if prev else None
            spot_rows.append(
                {
                    "coin": pair["name"],
                    "marketType": "spot",
                    "category": None,
                    "pairName": f"[Spot] {base}/{quote}",
                    "price": c.get("markPx", "?"),
                    "priceChange": chg,
                    "volumeUsd": c.get("dayNtlVlm", "?"),
                    "funding": None,
                    "openInterest": None,
                    "openInterestUsd": None,
                }
            )

    if not spot_only:
        # Main perp dex (keeps richer fields like funding/openInterest).
        perp_meta, perp_ctxs = await asyncio.to_thread(info.meta_and_asset_ctxs)
        tokens = spot_meta.get("tokens", [])
        collateral = _safe_token_name(tokens, perp_meta.get("collateralToken", 0), "USD")
        for i, market in enumerate(perp_meta["universe"]):
            if market.get("isDelisted"):
                continue
            c = perp_ctxs[i] if i < len(perp_ctxs) else {}
            prev = float(c.get("prevDayPx", 0) or 0)
            mark = float(c.get("markPx", 0) or 0)
            oi_raw = _to_float(c.get("openInterest"))
            chg = ((mark - prev) / prev * 100) if prev else None
            perp_rows.append(
                {
                    "coin": market["name"],
                    "marketType": "perp",
                    "category": perp_categories.get(str(market["name"])),
                    "pairName": f"{market['name']}/{collateral} {market.get('maxLeverage', '?')}x",
                    "price": c.get("markPx", "?"),
                    "priceChange": chg,
                    "volumeUsd": c.get("dayNtlVlm", "?"),
                    "funding": c.get("funding"),
                    "openInterest": c.get("openInterest"),
                    "openInterestUsd": (oi_raw * mark) if oi_raw is not None and mark > 0 else None,
                }
            )

        # Testnet uses main perp + spot only to avoid rate-limiting on bulk builder fetches.
        if not context.config.testnet:
            # Builder perps (stocks and other external markets).
            # These are dex-qualified symbols such as xyz:TSLA or flx:CRCL.
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
                    if not coin:
                        continue
                    if market.get("isDelisted"):
                        continue
                    c = ctxs[i] if i < len(ctxs) else {}
                    prev = float(c.get("prevDayPx", 0) or 0)
                    mark = float(c.get("markPx", 0) or 0)
                    oi_raw = _to_float(c.get("openInterest"))
                    chg = ((mark - prev) / prev * 100) if prev else None
                    perp_rows.append(
                        {
                            "coin": coin,
                            "marketType": "perp",
                            "category": perp_categories.get(coin),
                            "pairName": f"{coin}/{collateral} {market.get('maxLeverage', '?')}x",
                            "price": c.get("markPx", "?"),
                            "priceChange": chg,
                            "volumeUsd": c.get("dayNtlVlm", "?"),
                            "funding": c.get("funding"),
                            "openInterest": c.get("openInterest"),
                            "openInterestUsd": (oi_raw * mark) if oi_raw is not None and mark > 0 else None,
                        }
                    )

    return {"perpMarkets": perp_rows, "spotMarkets": spot_rows}


def _fetch_builder_market_data(info: Any, dex: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
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
    context = _ctx(ctx)
    include_category = category is not None
    q = query.strip().lower()
    if not q:
        raise RuntimeError("query must not be empty")
    rows = _prepare_market_output(
        _sort_market_rows(
            _filter_market_rows_by_category(_build_market_rows(context, spot_only, perp_only), category),
            sort_by,
        ),
        include_category,
    )
    perps = [
        x
        for x in rows["perpMarkets"]
        if q in str(x.get("coin", "")).lower()
        or q in str(x.get("pairName", "")).lower()
        or q in str(x.get("category", "")).lower()
    ]
    spots = [
        x
        for x in rows["spotMarkets"]
        if q in str(x.get("coin", "")).lower()
        or q in str(x.get("pairName", "")).lower()
        or q in str(x.get("category", "")).lower()
    ]
    out({"perpMarkets": perps, "spotMarkets": spots}, _json(ctx))
    _done(ctx)


@cli_command
def referral_set(ctx: Any, code: str) -> None:
    result = _ctx(ctx).get_wallet_client().set_referrer(code)
    out(result, _json(ctx))
    _done(ctx)


@cli_command
def referral_status(ctx: Any) -> None:
    context = _ctx(ctx)
    user = context.get_wallet_address()
    result = context.get_public_client().query_referral_state(user)
    out(result, _json(ctx))
    _done(ctx)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
