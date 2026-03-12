from datetime import datetime
from typing import Any, Optional

from eth_account import Account as EthAccount

from ..cli.runtime import cli_command, console, run_blocking
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
from ..services.account_fetch import (
    account_perp_dexs as _service_account_perp_dexs,
    fetch_balances_async as _service_fetch_balances_async,
    fetch_portfolio_async as _service_fetch_portfolio_async,
    fetch_positions_async as _service_fetch_positions_async,
)
from ..types import BalancesPayload, OpenOrderRow, PortfolioPayload, PositionsPayload
from ..utils.output import out
from ..utils.validators import normalize_private_key, validate_address
from ..utils.watch import watch_loop
from .common import (
    _confirm,
    _ctx,
    _done,
    _format_address,
    _json,
    _network_name,
    _render_table,
)

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
def account_remove(ctx: Any, alias: str, force: bool = False) -> None:
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

def _account_perp_dexs(context: CLIContext) -> list[str]:
    return _service_account_perp_dexs(context)

def _fetch_positions(context: CLIContext, user: str) -> PositionsPayload:
    return run_blocking(_fetch_positions_async(context, user))

async def _fetch_positions_async(context: CLIContext, user: str) -> PositionsPayload:
    return await _service_fetch_positions_async(context, user)

@cli_command
def account_positions(ctx: Any, user: Optional[str] = None, watch: bool = False) -> None:
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

@cli_command
def account_orders(ctx: Any, user: Optional[str] = None, watch: bool = False) -> None:
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

def _fetch_balances(context: CLIContext, user: str) -> BalancesPayload:
    return run_blocking(_fetch_balances_async(context, user))

async def _fetch_balances_async(context: CLIContext, user: str) -> BalancesPayload:
    return await _service_fetch_balances_async(context, user)

async def _fetch_portfolio_async(context: CLIContext, user: str) -> PortfolioPayload:
    return await _service_fetch_portfolio_async(context, user)

@cli_command
def account_balances(ctx: Any, user: Optional[str] = None, watch: bool = False) -> None:
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
def account_portfolio(ctx: Any, user: Optional[str] = None, watch: bool = False) -> None:
    context = _ctx(ctx)
    address = validate_address(user) if user else context.get_wallet_address()

    def fetch() -> PortfolioPayload:
        return run_blocking(_fetch_portfolio_async(context, address))

    if watch:
        watch_loop(
            fetch,
            lambda d: (
                _render_table(
                    f"Portfolio AccountValue={d['accountValue']} MarginUsed={d['totalMarginUsed']}",
                    ["Coin", "Size", "Entry", "Value", "PnL", "Leverage"],
                    [
                        [p["coin"], p["size"], p["entryPx"], p["positionValue"], p["unrealizedPnl"], p["leverage"]]
                        for p in d["positions"]
                    ],
                ),
                _render_table(
                    "Spot Balances",
                    ["Token", "Total", "Hold", "Available"],
                    [[b["token"], b["total"], b["hold"], b.get("available", "-")] for b in d["spotBalances"]],
                ),
            ),
            as_json=_json(ctx),
        )
        return
    out(fetch(), _json(ctx))
    _done(ctx)
