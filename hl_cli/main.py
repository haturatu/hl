import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from typing import Any, Optional

import click
import typer
from eth_account import Account as EthAccount
from typer.core import TyperGroup

from .cli_runtime import (
    cli_command,
    cli_context,
    console,
    confirm,
    finish_command,
    json_output_enabled,
    render_table,
)
from .context import CLIContext, load_config
from .db import (
    create_account,
    delete_account,
    get_account_by_alias,
    get_account_count,
    get_all_accounts,
    is_alias_taken,
    set_default_account,
)
from .order_commands import (
    _mids_for_coin,
    _resolve_tradable_coin,
    order_app,
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
from .output import out, out_success
from .paths import (
    SERVER_CACHE_PATH,
    SERVER_LOG_PATH,
    SERVER_PID_PATH,
    SERVER_STATE_PATH,
)
from .validators import (
    normalize_private_key,
    validate_address,
)
from .watch import watch_loop

class FullHelpTyperGroup(TyperGroup):
    def format_help(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        formatter.write_heading("Usage")
        formatter.write_text(f"{ctx.info_name or 'hl'} [OPTIONS] COMMAND [ARGS]...")
        formatter.write_paragraph()
        if self.help:
            formatter.write_heading("Description")
            formatter.write_text(self.help)
            formatter.write_paragraph()
        formatter.write_heading("Command Tree (argparse style)")
        for line in self._collect_help_lines(ctx=ctx, cmd=self, path=ctx.info_name or "hl", depth=0):
            formatter.write_text(line)

    def _collect_help_lines(
        self,
        ctx: click.Context,
        cmd: click.Command,
        path: str,
        depth: int,
    ) -> list[str]:
        indent = "  " * depth
        lines: list[str] = [f"{indent}{path}"]

        options: list[str] = []
        arguments: list[str] = []
        for param in cmd.get_params(ctx):
            if isinstance(param, click.Option):
                if param.hidden:
                    continue
                flags = " | ".join([*param.opts, *param.secondary_opts]).strip()
                if flags:
                    options.append(flags)
            elif isinstance(param, click.Argument):
                arguments.append(param.human_readable_name)

        usage_chunks = [path]
        if arguments:
            usage_chunks.extend([f"<{a}>" for a in arguments])
        if options:
            usage_chunks.extend([f"[{o}]" for o in options])
        lines.append(f"{indent}  usage: {' '.join(usage_chunks)}")

        short_help = cmd.short_help or cmd.help
        if short_help:
            lines.append(f"{indent}  help: {short_help.splitlines()[0]}")

        if isinstance(cmd, click.Group):
            sub_ctx = click.Context(cmd, info_name=path, parent=ctx)
            for sub_name in cmd.list_commands(sub_ctx):
                sub_cmd = cmd.get_command(sub_ctx, sub_name)
                if sub_cmd is None or sub_cmd.hidden:
                    continue
                lines.extend(
                    self._collect_help_lines(
                        ctx=sub_ctx,
                        cmd=sub_cmd,
                        path=f"{path} {sub_name}",
                        depth=depth + 1,
                    )
                )

        return lines


app = typer.Typer(help="CLI for Hyperliquid DEX (Python)", no_args_is_help=True, cls=FullHelpTyperGroup)
account_app = typer.Typer(
    help="Account management and information.\n"
    "Wallet add quick start: run 'hl account add' and follow prompts.",
    no_args_is_help=False,
)
asset_app = typer.Typer(help="Asset-specific information", no_args_is_help=True)
markets_app = typer.Typer(help="Market information", no_args_is_help=True)
referral_app = typer.Typer(help="Referral management", no_args_is_help=True)
server_app = typer.Typer(help="Manage background cache server", no_args_is_help=True)

app.add_typer(account_app, name="account")
app.add_typer(order_app, name="order")
app.add_typer(asset_app, name="asset")
app.add_typer(markets_app, name="markets")
app.add_typer(referral_app, name="referral")
app.add_typer(server_app, name="server")


@app.callback()
def root_callback(
    ctx: typer.Context,
    json_output: bool = typer.Option(False, "--json", help="Output in JSON format"),
    testnet: bool = typer.Option(False, "--testnet", help="Use testnet"),
) -> None:
    ctx.obj = {
        "context": CLIContext(load_config(testnet)),
        "json": json_output,
        "start": time.perf_counter(),
    }


def _ctx(ctx: typer.Context) -> CLIContext:
    return cli_context(ctx)


def _json(ctx: typer.Context) -> bool:
    return json_output_enabled(ctx)


def _done(ctx: typer.Context) -> None:
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


def _pid_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _load_server_cache() -> Optional[dict[str, Any]]:
    if not SERVER_CACHE_PATH.exists():
        return None
    try:
        return json.loads(SERVER_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


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


@account_app.callback(invoke_without_command=True)
def account_callback(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        _print_account_add_guide()
        print(ctx.get_help())
        raise typer.Exit()


@account_app.command("add")
@cli_command
def account_add(ctx: typer.Context) -> None:
    context = _ctx(ctx)
    is_testnet = context.config.testnet

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
            if is_alias_taken(alias):
                print(f'Alias "{alias}" is already taken.')
                continue
            break

        set_as_default = get_account_count() == 0 or _confirm("Set as default account?", True)
        created = create_account(
            alias=alias,
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
            if is_alias_taken(alias):
                print(f'Alias "{alias}" is already taken.')
                continue
            break
        set_as_default = get_account_count() == 0 or _confirm("Set as default account?", True)
        created = create_account(
            alias=alias,
            user_address=user_address,
            account_type="readonly",
            set_as_default=set_as_default,
        )
        out(created.__dict__, _json(ctx))
    else:
        raise RuntimeError("Invalid selection")
    _done(ctx)


@account_app.command("ls")
@cli_command
def account_ls(ctx: typer.Context) -> None:
    accounts = get_all_accounts()
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


@account_app.command("set-default")
@cli_command
def account_set_default(ctx: typer.Context, alias: str) -> None:
    if not get_account_by_alias(alias):
        raise RuntimeError(f'Account with alias "{alias}" not found')
    updated = set_default_account(alias)
    out(updated.__dict__, _json(ctx))
    _done(ctx)


@account_app.command("remove")
@cli_command
def account_remove(
    ctx: typer.Context,
    alias: str,
    force: bool = typer.Option(False, "-f", "--force"),
) -> None:
    existing = get_account_by_alias(alias)
    if not existing:
        raise RuntimeError(f'Account with alias "{alias}" not found')
    if not force and not _confirm(f'Remove account "{alias}" ({existing.user_address})?', False):
        print("Cancelled.")
        raise typer.Exit(0)
    ok = delete_account(alias)
    if not ok:
        raise RuntimeError("Failed to remove account")
    out({"deleted": True, "alias": alias}, _json(ctx))
    _done(ctx)


def _fetch_positions(context: CLIContext, user: str) -> dict[str, Any]:
    info = context.get_public_client()
    positions: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []

    for dex in context.get_perp_dexs():
        state = info.user_state(user, dex=dex)
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


@account_app.command("positions")
@cli_command
def account_positions(
    ctx: typer.Context,
    user: Optional[str] = typer.Option(None, "--user"),
    watch: bool = typer.Option(False, "-w", "--watch"),
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


@account_app.command("orders")
@cli_command
def account_orders(
    ctx: typer.Context,
    user: Optional[str] = typer.Option(None, "--user"),
    watch: bool = typer.Option(False, "-w", "--watch"),
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
    perp = context.get_public_client().user_state(user)
    spot = context.get_public_client().spot_user_state(user)
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


@account_app.command("balances")
@cli_command
def account_balances(
    ctx: typer.Context,
    user: Optional[str] = typer.Option(None, "--user"),
    watch: bool = typer.Option(False, "-w", "--watch"),
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


@account_app.command("portfolio")
@cli_command
def account_portfolio(
    ctx: typer.Context,
    user: Optional[str] = typer.Option(None, "--user"),
    watch: bool = typer.Option(False, "-w", "--watch"),
) -> None:
    context = _ctx(ctx)
    address = validate_address(user) if user else context.get_wallet_address()

    def fetch() -> dict[str, Any]:
        pos = _fetch_positions(context, address)
        bal = _fetch_balances(context, address)
        return {
            "positions": pos["positions"],
            "spotBalances": bal["spotBalances"],
            "accountValue": pos["marginSummary"]["accountValue"],
            "totalMarginUsed": pos["marginSummary"]["totalMarginUsed"],
        }

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


@asset_app.command("price")
@cli_command
def asset_price(ctx: typer.Context, coin: str, watch: bool = typer.Option(False, "-w", "--watch")) -> None:
    context = _ctx(ctx)

    def fetch() -> dict[str, str]:
        resolved_coin = _resolve_tradable_coin(context, coin)
        cache = _load_server_cache()
        if ":" not in resolved_coin and cache and "allMids" in cache:
            mids = cache["allMids"]
        else:
            mids = _mids_for_coin(context, resolved_coin)
        if resolved_coin not in mids:
            raise RuntimeError(f"Coin not found: {coin}")
        return {"coin": coin, "price": mids[resolved_coin]}

    if watch:
        watch_loop(fetch, lambda d: print(f"{d['coin']}: {d['price']}"), as_json=_json(ctx))
        return
    out(fetch(), _json(ctx))
    _done(ctx)


@asset_app.command("book")
@cli_command
def asset_book(ctx: typer.Context, coin: str, watch: bool = typer.Option(False, "-w", "--watch")) -> None:
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
        watch_loop(fetch, render_book, as_json=_json(ctx))
        return
    out(fetch(), _json(ctx))
    _done(ctx)


@asset_app.command("leverage")
@cli_command
def asset_leverage(
    ctx: typer.Context,
    coin: str,
    user: Optional[str] = typer.Option(None, "--user"),
    watch: bool = typer.Option(False, "-w", "--watch"),
) -> None:
    context = _ctx(ctx)
    address = validate_address(user) if user else context.get_wallet_address()

    def fetch() -> dict[str, Any]:
        state = context.get_public_client().user_state(address)
        meta = context.get_public_client().meta()
        mids = context.get_public_client().all_mids()
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


def _build_market_rows(context: CLIContext, spot_only: bool, perp_only: bool) -> dict[str, list[dict[str, Any]]]:
    info = context.get_public_client()
    spot_meta, spot_ctxs = info.spot_meta_and_asset_ctxs()

    spot_rows: list[dict[str, Any]] = []
    perp_rows: list[dict[str, Any]] = []

    if not perp_only:
        ctx_map = {c["coin"]: c for c in spot_ctxs}
        for pair in spot_meta["universe"]:
            base = spot_meta["tokens"][pair["tokens"][0]]["name"]
            quote = spot_meta["tokens"][pair["tokens"][1]]["name"]
            c = ctx_map.get(pair["name"], {})
            prev = float(c.get("prevDayPx", 0) or 0)
            mark = float(c.get("markPx", 0) or 0)
            chg = ((mark - prev) / prev * 100) if prev else None
            spot_rows.append(
                {
                    "coin": pair["name"],
                    "pairName": f"[Spot] {base}/{quote}",
                    "price": c.get("markPx", "?"),
                    "priceChange": chg,
                    "volumeUsd": c.get("dayNtlVlm", "?"),
                    "funding": None,
                    "openInterest": None,
                }
            )

    if not spot_only:
        # Main perp dex (keeps richer fields like funding/openInterest).
        perp_meta, perp_ctxs = info.meta_and_asset_ctxs()
        collateral = spot_meta["tokens"][perp_meta.get("collateralToken", 0)].get("name", "USD")
        for i, market in enumerate(perp_meta["universe"]):
            c = perp_ctxs[i] if i < len(perp_ctxs) else {}
            prev = float(c.get("prevDayPx", 0) or 0)
            mark = float(c.get("markPx", 0) or 0)
            chg = ((mark - prev) / prev * 100) if prev else None
            perp_rows.append(
                {
                    "coin": market["name"],
                    "pairName": f"{market['name']}/{collateral} {market.get('maxLeverage', '?')}x",
                    "price": c.get("markPx", "?"),
                    "priceChange": chg,
                    "volumeUsd": c.get("dayNtlVlm", "?"),
                    "funding": c.get("funding"),
                    "openInterest": c.get("openInterest"),
                }
            )

        # Builder perps (stocks and other external markets).
        # These are dex-qualified symbols such as xyz:TSLA or flx:CRCL.
        for dex in context.get_perp_dexs():
            if not dex:
                continue
            meta = info.meta(dex=dex)
            mids = info.all_mids(dex=dex)
            coll_idx = meta.get("collateralToken", 0)
            collateral = spot_meta["tokens"][coll_idx].get("name", "USD")
            for market in meta.get("universe", []):
                coin = str(market.get("name"))
                if not coin:
                    continue
                perp_rows.append(
                    {
                        "coin": coin,
                        "pairName": f"{coin}/{collateral} {market.get('maxLeverage', '?')}x",
                        "price": mids.get(coin, "?"),
                        "priceChange": None,
                        "volumeUsd": "?",
                        "funding": None,
                        "openInterest": None,
                    }
                )

    return {"perpMarkets": perp_rows, "spotMarkets": spot_rows}


@markets_app.command("ls")
@cli_command
def markets_ls(
    ctx: typer.Context,
    spot_only: bool = typer.Option(False, "--spot-only"),
    perp_only: bool = typer.Option(False, "--perp-only"),
    watch: bool = typer.Option(False, "-w", "--watch"),
) -> None:
    context = _ctx(ctx)

    if watch:
        watch_loop(
            lambda: _build_market_rows(context, spot_only, perp_only),
            lambda d: _render_table(
                f"Markets ({len(d['perpMarkets'])} perps, {len(d['spotMarkets'])} spot)",
                ["Coin", "Pair", "Price", "24h%", "Vol", "Funding", "OI"],
                [
                    [
                        x["coin"],
                        x["pairName"],
                        x["price"],
                        "-" if x["priceChange"] is None else f"{x['priceChange']:.2f}%",
                        x["volumeUsd"],
                        x["funding"] if x["funding"] is not None else "-",
                        x["openInterest"] if x["openInterest"] is not None else "-",
                    ]
                    for x in [*d["perpMarkets"], *d["spotMarkets"]]
                ],
            ),
            as_json=_json(ctx),
        )
        return

    out(_build_market_rows(context, spot_only, perp_only), _json(ctx))
    _done(ctx)


@markets_app.command("search")
@cli_command
def markets_search(
    ctx: typer.Context,
    query: str,
    spot_only: bool = typer.Option(False, "--spot-only"),
    perp_only: bool = typer.Option(False, "--perp-only"),
) -> None:
    context = _ctx(ctx)
    q = query.strip().lower()
    if not q:
        raise RuntimeError("query must not be empty")
    rows = _build_market_rows(context, spot_only, perp_only)
    perps = [
        x for x in rows["perpMarkets"] if q in str(x.get("coin", "")).lower() or q in str(x.get("pairName", "")).lower()
    ]
    spots = [
        x for x in rows["spotMarkets"] if q in str(x.get("coin", "")).lower() or q in str(x.get("pairName", "")).lower()
    ]
    out({"perpMarkets": perps, "spotMarkets": spots}, _json(ctx))
    _done(ctx)


@referral_app.command("set")
@cli_command
def referral_set(ctx: typer.Context, code: str) -> None:
    result = _ctx(ctx).get_wallet_client().set_referrer(code)
    out(result, _json(ctx))
    _done(ctx)


@referral_app.command("status")
@cli_command
def referral_status(ctx: typer.Context) -> None:
    context = _ctx(ctx)
    user = context.get_wallet_address()
    result = context.get_public_client().query_referral_state(user)
    out(result, _json(ctx))
    _done(ctx)


@server_app.command("start")
@cli_command
def server_start(ctx: typer.Context) -> None:
    if SERVER_PID_PATH.exists():
        pid = int(SERVER_PID_PATH.read_text().strip())
        if _pid_running(pid):
            raise RuntimeError(f"Server is already running (pid: {pid})")

    args = [sys.executable, "-m", "hl_cli.server_process"]
    if _ctx(ctx).config.testnet:
        args.append("--testnet")

    logf = SERVER_LOG_PATH.open("a", encoding="utf-8")
    subprocess.Popen(args, stdout=logf, stderr=logf, start_new_session=True)

    timeout = time.time() + 10
    while time.time() < timeout:
        if SERVER_PID_PATH.exists():
            break
        time.sleep(0.2)

    if not SERVER_PID_PATH.exists():
        raise RuntimeError(f"Failed to start server. Check log: {SERVER_LOG_PATH}")

    out_success("Server started")
    _done(ctx)


@server_app.command("stop")
@cli_command
def server_stop(ctx: typer.Context) -> None:
    if not SERVER_PID_PATH.exists():
        raise RuntimeError("Server is not running")
    pid = int(SERVER_PID_PATH.read_text().strip())
    if _pid_running(pid):
        os.kill(pid, signal.SIGTERM)
    timeout = time.time() + 5
    while time.time() < timeout and _pid_running(pid):
        time.sleep(0.2)
    if _pid_running(pid):
        os.kill(pid, signal.SIGKILL)
    if SERVER_PID_PATH.exists():
        SERVER_PID_PATH.unlink()
    out_success("Server stopped")
    _done(ctx)


@server_app.command("status")
@cli_command
def server_status(ctx: typer.Context) -> None:
    running = False
    state: dict[str, Any] = {"running": False}

    if SERVER_PID_PATH.exists():
        try:
            pid = int(SERVER_PID_PATH.read_text().strip())
            running = _pid_running(pid)
        except Exception:
            running = False

    if running and SERVER_STATE_PATH.exists():
        try:
            state = json.loads(SERVER_STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            state = {"running": True}
    else:
        state = {"running": False}

    if _json(ctx):
        out(state, True)
    else:
        if not state.get("running"):
            print("Server is not running")
        else:
            print("Status: running")
            print(f"Network: {'testnet' if state.get('testnet') else 'mainnet'}")
            print(f"Uptime: {state.get('uptime', 0)}ms")
            cache = state.get("cache", {})
            print("Cache:")
            print(f"  Mid Prices: {'cached' if cache.get('hasMids') else 'not loaded'}")
            print(f"  Perp Meta: {'cached' if cache.get('hasPerpMetas') else 'not loaded'}")
            print(f"  Spot Meta: {'cached' if cache.get('hasSpotMeta') else 'not loaded'}")
    _done(ctx)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
