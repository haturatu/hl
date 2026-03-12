import argparse
import asyncio
import sys
import time
from textwrap import dedent
from types import SimpleNamespace
from typing import Any, Callable

from ..core.context import CLIContext, load_config
from ..commands import app as legacy

TOP_LEVEL_COMMANDS = ["account", "order", "asset", "markets", "referral", "completion"]
SUBCOMMANDS: dict[str, list[str]] = {
    "account": [
        "add",
        "ls",
        "set-default",
        "remove",
        "positions",
        "orders",
        "balances",
        "portfolio",
    ],
    "order": [
        "ls",
        "limit",
        "market",
        "tpsl",
        "twap",
        "twap-cancel",
        "cancel",
        "cancel-all",
        "set-leverage",
        "configure",
    ],
    "asset": ["price", "book", "leverage"],
    "markets": ["ls", "search"],
    "referral": ["set", "status"],
    "completion": ["bash"],
}
GLOBAL_OPTIONS = ["--json", "--testnet", "-h", "--help"]


def _ctx(json_output: bool, testnet: bool) -> SimpleNamespace:
    return SimpleNamespace(
        obj={
            "context": CLIContext(load_config(testnet)),
            "json": json_output,
            "start": time.perf_counter(),
        }
    )


def _exit_with_error(msg: str, code: int = 2) -> None:
    print(msg, file=sys.stderr)
    raise SystemExit(code)


def _bash_completion_script() -> str:
    top_level = " ".join(TOP_LEVEL_COMMANDS)
    global_options = " ".join(GLOBAL_OPTIONS)
    case_lines = "\n".join(
        [
            f'        {name}) COMPREPLY=( $(compgen -W "{ " ".join(values) }" -- "$cur") ) ;;'
            for name, values in SUBCOMMANDS.items()
        ]
    )
    return dedent(
        f"""\
        # bash completion for hl
        _hl_completion() {{
            local cur prev cmd i
            COMPREPLY=()
            cur="${{COMP_WORDS[COMP_CWORD]}}"
            prev="${{COMP_WORDS[COMP_CWORD-1]}}"
            cmd=""

            for ((i=1; i < COMP_CWORD; i++)); do
                case "${{COMP_WORDS[i]}}" in
                    -*) ;;
                    *)
                        cmd="${{COMP_WORDS[i]}}"
                        break
                        ;;
                esac
            done

            if [[ "$cur" == -* ]]; then
                COMPREPLY=( $(compgen -W "{global_options}" -- "$cur") )
                return 0
            fi

            if [[ -z "$cmd" ]]; then
                COMPREPLY=( $(compgen -W "{top_level}" -- "$cur") )
                return 0
            fi

            case "$cmd" in
{case_lines}
                *)
                    COMPREPLY=()
                    ;;
            esac
        }}

        complete -F _hl_completion hl
        """
    )


def _print_completion(shell: str) -> None:
    if shell != "bash":
        _exit_with_error(f"Unsupported shell: {shell}")
    print(_bash_completion_script(), end="")


def _parse_limit_shape(args: argparse.Namespace) -> tuple[str, str, str]:
    # Normal mode: hl order limit <side> <size> <coin> <price>
    # Stake mode:  hl order limit <side> <coin> <price> --stake <usd>
    a = args.a
    b = args.b
    c = args.c
    stake = args.stake
    if a is None or b is None:
        _exit_with_error("Missing arguments. See: hl order limit -h")

    if stake is not None:
        if c is not None:
            _exit_with_error(
                "When --stake is used, syntax is: hl order limit <side> <coin> <price> --stake <usd>"
            )
        try:
            px = float(b)
        except ValueError as exc:
            raise SystemExit(f"Invalid price: {b}") from exc
        if px <= 0:
            _exit_with_error("price must be positive")
        if float(stake) <= 0:
            _exit_with_error("stake must be positive")
        derived_size = str(float(stake) / px)
        return derived_size, a, b

    if c is None:
        _exit_with_error(
            "Missing price. Syntax: hl order limit <side> <size> <coin> <price>"
        )
    return a, b, c


def _parse_market_shape(args: argparse.Namespace) -> tuple[str, str]:
    # Normal mode: hl order market <side> <size> <coin>
    # Stake mode:  hl order market <side> <coin> --stake <usd>
    a = args.a
    b = args.b
    stake = args.stake
    if a is None:
        _exit_with_error("Missing arguments. See: hl order market -h")

    if stake is not None:
        if b is not None:
            _exit_with_error(
                "When --stake is used, syntax is: hl order market <side> <coin> --stake <usd>"
            )
        if float(stake) <= 0:
            _exit_with_error("stake must be positive")
        return "0", a

    if b is None:
        _exit_with_error("Missing coin. Syntax: hl order market <side> <size> <coin>")
    return a, b


def _build_parser() -> argparse.ArgumentParser:
    epilog = (
        "Command tree:\n"
        "  account add|ls|set-default|remove|positions|orders|balances|portfolio\n"
        "  order ls|limit|market|tpsl|twap|twap-cancel|cancel|cancel-all|set-leverage|configure\n"
        "  asset price|book|leverage\n"
        "  markets ls\n"
        "  referral set|status\n"
        "Examples:\n"
        "  hl account add\n"
        "  hl order twap buy 1 BTC 30 --randomize\n"
        "  hl order twap-cancel BTC 12345\n"
        "  hl account positions --watch\n"
    )
    p = argparse.ArgumentParser(
        prog="hl",
        description="CLI for Hyperliquid DEX",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=epilog,
    )
    p.add_argument("--json", action="store_true", help="Output in JSON format")
    p.add_argument("--testnet", action="store_true", help="Use testnet")

    sub = p.add_subparsers(dest="command")

    def add_cmd_parser(
        subparsers: Any,
        name: str,
        help_text: str,
        examples: list[str] | None = None,
    ) -> argparse.ArgumentParser:
        ep = None
        if examples:
            ep = "Examples:\n" + "\n".join([f"  {x}" for x in examples])
        return subparsers.add_parser(
            name,
            help=help_text,
            formatter_class=argparse.RawTextHelpFormatter,
            epilog=ep,
        )

    # account
    account = add_cmd_parser(
        sub,
        "account",
        "Account management and information",
        ["hl account add", "hl account ls", "hl account positions --watch"],
    )
    acc_sub = account.add_subparsers(dest="account_command")
    add_cmd_parser(acc_sub, "add", "Add account", ["hl account add"])
    add_cmd_parser(
        acc_sub, "ls", "List accounts", ["hl account ls", "hl --json account ls"]
    )
    acc_set = add_cmd_parser(
        acc_sub, "set-default", "Set default account", ["hl account set-default main"]
    )
    acc_set.add_argument("alias")
    acc_rm = add_cmd_parser(
        acc_sub,
        "remove",
        "Remove account",
        ["hl account remove main", "hl account remove main --force"],
    )
    acc_rm.add_argument("alias")
    acc_rm.add_argument("-f", "--force", action="store_true")

    for name, help_text in [
        ("positions", "Get positions"),
        ("orders", "Get orders"),
        ("balances", "Get balances"),
        ("portfolio", "Get portfolio"),
    ]:
        s = add_cmd_parser(
            acc_sub,
            name,
            help_text,
            [
                f"hl account {name}",
                f"hl account {name} --watch",
                f"hl account {name} --user 0x1234567890abcdef1234567890abcdef12345678",
            ],
        )
        s.add_argument("--user")
        s.add_argument("-w", "--watch", action="store_true")

    # order
    order = add_cmd_parser(
        sub,
        "order",
        "Order management and trading",
        [
            "hl order ls",
            "hl order limit buy 0.01 @142 88.5           # spot",
            "hl order limit long 0.01 BTC 60000          # perp",
            "hl order twap short 1 BTC 30                # perp",
        ],
    )
    ord_sub = order.add_subparsers(dest="order_command")
    ord_ls = add_cmd_parser(
        ord_sub, "ls", "List open orders", ["hl order ls", "hl order ls --watch"]
    )
    ord_ls.add_argument("--user")
    ord_ls.add_argument("-w", "--watch", action="store_true")

    ord_limit = add_cmd_parser(
        ord_sub,
        "limit",
        "Place limit order (buy/sell = spot, long/short = perp)",
        [
            "hl order limit buy 0.01 @142 88.5             # spot",
            "hl order limit sell 0.01 @142 95              # spot",
            "hl order limit long 0.001 BTC 65000           # perp",
            "hl order limit long BTC 65000 --stake 50 --leverage 20 --cross",
            "hl order limit short 0.1 ETH 3500 --tif Gtc   # perp",
            "hl order limit long 1 SOL 100 --reduce-only   # perp",
        ],
    )
    ord_limit.add_argument("side")
    ord_limit.add_argument("a", nargs="?")
    ord_limit.add_argument("b", nargs="?")
    ord_limit.add_argument("c", nargs="?")
    ord_limit.add_argument("--tif", default="Gtc")
    ord_limit.add_argument("--reduce-only", action="store_true")
    ord_limit.add_argument(
        "--stake",
        type=float,
        help="USD margin used to derive order size. With --leverage, size uses stake x leverage; without it, size uses stake only",
    )
    ord_limit.add_argument(
        "--leverage",
        type=int,
        help="Optional leverage update before placing the order. If omitted, the CLI does not multiply stake by leverage for size calculation",
    )
    ord_limit.add_argument(
        "--cross", action="store_true", help="Use cross margin with --leverage"
    )
    ord_limit.add_argument(
        "--isolated", action="store_true", help="Use isolated margin with --leverage"
    )

    ord_market = add_cmd_parser(
        ord_sub,
        "market",
        "Place market order (buy/sell = spot, long/short/close = perp)",
        [
            "hl order market buy @142 --stake 10             # spot",
            "hl order market sell @142 --stake 10            # spot",
            "hl order market long BTC --stake 50 --leverage 20 --cross   # perp",
            "hl order market short ETH 0.1 --slippage 0.5    # perp",
            "hl order market close ETH                       # perp",
            "hl order market close xyz:TSLA",
            "hl order market close ETH --ratio 0.5",
        ],
    )
    ord_market.add_argument("side")
    ord_market.add_argument("a", nargs="?")
    ord_market.add_argument("b", nargs="?")
    ord_market.add_argument(
        "--reduce-only",
        action="store_true",
        help="Perp only: reduce-only for long/short",
    )
    ord_market.add_argument("--slippage", type=float)
    ord_market.add_argument(
        "--stake",
        type=float,
        help="USD used to derive order size. For buy/sell this is spot size; for long/short this is perp margin.",
    )
    ord_market.add_argument(
        "--leverage",
        type=int,
        help="Perp only: optional leverage update before placing long/short",
    )
    ord_market.add_argument(
        "--cross",
        action="store_true",
        help="Perp only: use cross margin with --leverage",
    )
    ord_market.add_argument(
        "--isolated",
        action="store_true",
        help="Perp only: use isolated margin with --leverage",
    )
    ord_market.add_argument(
        "--ratio",
        type=float,
        default=1.0,
        help="Perp close only: close ratio (0 < ratio <= 1)",
    )

    ord_twap = add_cmd_parser(
        ord_sub,
        "twap",
        "Place TWAP order (perp only: use long/short)",
        [
            "hl order twap long 1 BTC 30",
            "hl order twap long 0 BTC 30 --stake 5",
            "hl order twap long 0 BTC 30 --stake 5 --leverage 20 --cross",
            "hl order twap short 2 ETH 5,10 --randomize",
            "hl order twap short 1 BTC 30 --leverage 20 --isolated",
            "hl order twap short 1 BTC 30 --reduce-only",
        ],
    )
    ord_twap.add_argument("side")
    ord_twap.add_argument("size")
    ord_twap.add_argument("coin")
    ord_twap.add_argument("interval")
    ord_twap.add_argument(
        "--stake",
        type=float,
        help="USD margin used to derive total TWAP size. With --leverage, size uses stake x leverage; without it, size uses stake only",
    )
    ord_twap.add_argument("--reduce-only", action="store_true")
    ord_twap.add_argument("--randomize", action="store_true")
    ord_twap.add_argument(
        "--leverage",
        type=int,
        help="Optional leverage update before placing the order. If omitted, the CLI does not multiply stake by leverage for size calculation",
    )
    ord_twap.add_argument(
        "--cross", action="store_true", help="Use cross margin with --leverage"
    )
    ord_twap.add_argument(
        "--isolated", action="store_true", help="Use isolated margin with --leverage"
    )

    ord_tpsl = add_cmd_parser(
        ord_sub,
        "tpsl",
        "Set TP/SL trigger orders for an open position",
        [
            "hl order tpsl ETH --tp 1900 --sl 1800",
            "hl order tpsl ETH --sl 1800 --ratio 0.5",
            "hl order tpsl xyz:TSLA --tp 420",
        ],
    )
    ord_tpsl.add_argument("coin")
    ord_tpsl.add_argument("--tp", type=float, help="Take-profit trigger price")
    ord_tpsl.add_argument("--sl", type=float, help="Stop-loss trigger price")
    ord_tpsl.add_argument(
        "--ratio",
        type=float,
        default=1.0,
        help="Position ratio to protect (0 < ratio <= 1)",
    )

    ord_twap_cancel = add_cmd_parser(
        ord_sub,
        "twap-cancel",
        "Cancel native TWAP order (interactive if omitted)",
        ["hl order twap-cancel BTC 12345", "hl order twap-cancel"],
    )
    ord_twap_cancel.add_argument("coin", nargs="?")
    ord_twap_cancel.add_argument("twap_id", nargs="?")

    ord_cancel = add_cmd_parser(
        ord_sub,
        "cancel",
        "Cancel order",
        ["hl order cancel 123456", "hl order cancel"],
    )
    ord_cancel.add_argument("oid", nargs="?")

    ord_cancel_all = add_cmd_parser(
        ord_sub,
        "cancel-all",
        "Cancel all orders",
        ["hl order cancel-all", "hl order cancel-all --coin BTC -y"],
    )
    ord_cancel_all.add_argument("-y", "--yes", action="store_true")
    ord_cancel_all.add_argument("--coin")

    ord_lev = add_cmd_parser(
        ord_sub,
        "set-leverage",
        "Set leverage",
        [
            "hl order set-leverage BTC 10 --cross",
            "hl order set-leverage ETH 5 --isolated",
        ],
    )
    ord_lev.add_argument("coin")
    ord_lev.add_argument("leverage")
    ord_lev.add_argument("--cross", action="store_true")
    ord_lev.add_argument("--isolated", action="store_true")

    ord_cfg = add_cmd_parser(
        ord_sub,
        "configure",
        "Configure order defaults",
        ["hl order configure", "hl order configure --slippage 0.8"],
    )
    ord_cfg.add_argument("--slippage", type=float)

    # asset
    asset = add_cmd_parser(
        sub,
        "asset",
        "Asset-specific information",
        ["hl asset price BTC", "hl asset book ETH --watch", "hl asset leverage BTC"],
    )
    as_sub = asset.add_subparsers(dest="asset_command")
    as_price = add_cmd_parser(
        as_sub,
        "price",
        "Get price",
        ["hl asset price BTC", "hl asset price BTC --watch"],
    )
    as_price.add_argument("coin")
    as_price.add_argument("-w", "--watch", action="store_true")

    as_book = add_cmd_parser(
        as_sub,
        "book",
        "Get orderbook",
        ["hl asset book BTC", "hl asset book ETH --watch"],
    )
    as_book.add_argument("coin")
    as_book.add_argument("-w", "--watch", action="store_true")

    as_lev = add_cmd_parser(
        as_sub,
        "leverage",
        "Get leverage info",
        [
            "hl asset leverage BTC",
            "hl asset leverage ETH --user 0x1234567890abcdef1234567890abcdef12345678",
            "hl asset leverage BTC --watch",
        ],
    )
    as_lev.add_argument("coin")
    as_lev.add_argument("--user")
    as_lev.add_argument("-w", "--watch", action="store_true")

    # markets
    markets = add_cmd_parser(
        sub,
        "markets",
        "Market information",
        ["hl markets ls", "hl markets search ORCL", "hl markets search xyz"],
    )
    mk_sub = markets.add_subparsers(dest="markets_command")
    mk_ls = add_cmd_parser(
        mk_sub,
        "ls",
        "List markets",
        [
            "hl markets ls",
            "hl markets ls --spot-only",
            "hl markets ls --perp-only --watch",
        ],
    )
    mk_ls.add_argument("--spot-only", action="store_true")
    mk_ls.add_argument("--perp-only", action="store_true")
    mk_ls.add_argument(
        "--category",
        nargs="?",
        const="*",
        help="Filter perp markets by category (e.g. stocks, commodities, indices, fx, preipo, crypto)",
    )
    mk_ls.add_argument(
        "--sort-by",
        default="volume",
        help="Sort markets by volume, oi, price, change, funding, or coin",
    )
    mk_ls.add_argument("-w", "--watch", action="store_true")
    mk_search = add_cmd_parser(
        mk_sub,
        "search",
        "Search markets by partial symbol/name",
        [
            "hl markets search ORCL",
            "hl markets search xyz",
            "hl markets search TSLA --perp-only",
        ],
    )
    mk_search.add_argument("query")
    mk_search.add_argument("--spot-only", action="store_true")
    mk_search.add_argument("--perp-only", action="store_true")
    mk_search.add_argument(
        "--category",
        nargs="?",
        const="*",
        help="Filter perp markets by category (e.g. stocks, commodities, indices, fx, preipo, crypto)",
    )
    mk_search.add_argument(
        "--sort-by",
        default="volume",
        help="Sort matches by volume, oi, price, change, funding, or coin",
    )

    # referral
    referral = add_cmd_parser(
        sub,
        "referral",
        "Referral management",
        ["hl referral set MYCODE", "hl referral status"],
    )
    rf_sub = referral.add_subparsers(dest="referral_command")
    rf_set = add_cmd_parser(
        rf_sub, "set", "Set referral code", ["hl referral set MYCODE"]
    )
    rf_set.add_argument("code")
    add_cmd_parser(rf_sub, "status", "Get referral status", ["hl referral status"])

    completion = add_cmd_parser(
        sub,
        "completion",
        "Print shell completion script",
        ['eval "$(hl completion bash)"'],
    )
    completion_sub = completion.add_subparsers(dest="completion_command")
    add_cmd_parser(
        completion_sub,
        "bash",
        "Print bash completion script",
        ['eval "$(hl completion bash)"'],
    )

    return p


async def _call(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
    try:
        if asyncio.iscoroutinefunction(fn):
            await fn(*args, **kwargs)
        else:
            fn(*args, **kwargs)
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001
        _exit_with_error(str(exc), 1)


async def dispatch(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    ctx = _ctx(args.json, args.testnet)

    cmd = args.command
    if cmd is None:
        parser.print_help()
        raise SystemExit(0)

    if cmd == "account":
        sc = args.account_command
        if sc is None:
            legacy._print_account_add_guide()
            return
        if sc == "add":
            await _call(legacy.account_add, ctx)
        elif sc == "ls":
            await _call(legacy.account_ls, ctx)
        elif sc == "set-default":
            await _call(legacy.account_set_default, ctx, args.alias)
        elif sc == "remove":
            await _call(legacy.account_remove, ctx, args.alias, args.force)
        elif sc == "positions":
            await _call(legacy.account_positions, ctx, user=args.user, watch=args.watch)
        elif sc == "orders":
            await _call(legacy.account_orders, ctx, user=args.user, watch=args.watch)
        elif sc == "balances":
            await _call(legacy.account_balances, ctx, user=args.user, watch=args.watch)
        elif sc == "portfolio":
            await _call(legacy.account_portfolio, ctx, user=args.user, watch=args.watch)
        else:
            _exit_with_error(f"Unknown account subcommand: {sc}")
        return

    if cmd == "order":
        sc = args.order_command
        if sc is None:
            _exit_with_error("Missing order subcommand. Run: hl order -h")
        if sc == "ls":
            await _call(legacy.order_ls, ctx, user=args.user, watch=args.watch)
        elif sc == "limit":
            size, coin, price = _parse_limit_shape(args)
            await _call(
                legacy.order_limit,
                ctx,
                args.side,
                size,
                coin,
                price,
                tif=args.tif,
                reduce_only=args.reduce_only,
                stake=args.stake,
                leverage=args.leverage,
                cross=args.cross,
                isolated=args.isolated,
            )
        elif sc == "market":
            if str(args.side).lower() == "close":
                if args.a is None or args.b is not None:
                    _exit_with_error("Close syntax: hl order market close <coin>")
                if args.stake is not None:
                    _exit_with_error("--stake cannot be used with market close")
                if args.leverage is not None or args.cross or args.isolated:
                    _exit_with_error(
                        "--leverage/--cross/--isolated cannot be used with market close"
                    )
                await _call(
                    legacy.order_market_close,
                    ctx,
                    args.a,
                    slippage=args.slippage,
                    ratio=args.ratio,
                )
            else:
                if args.ratio != 1.0:
                    _exit_with_error(
                        "--ratio is only supported with: hl order market close <coin>"
                    )
                size, coin = _parse_market_shape(args)
                await _call(
                    legacy.order_market,
                    ctx,
                    args.side,
                    size,
                    coin,
                    reduce_only=args.reduce_only,
                    slippage=args.slippage,
                    stake=args.stake,
                    leverage=args.leverage,
                    cross=args.cross,
                    isolated=args.isolated,
                )
        elif sc == "twap":
            await _call(
                legacy.order_twap,
                ctx,
                args.side,
                args.size,
                args.coin,
                args.interval,
                stake=args.stake,
                reduce_only=args.reduce_only,
                randomize=args.randomize,
                leverage=args.leverage,
                cross=args.cross,
                isolated=args.isolated,
            )
        elif sc == "tpsl":
            await _call(
                legacy.order_tpsl,
                ctx,
                args.coin,
                tp=args.tp,
                sl=args.sl,
                ratio=args.ratio,
            )
        elif sc == "twap-cancel":
            await _call(legacy.order_twap_cancel, ctx, args.coin, args.twap_id)
        elif sc == "cancel":
            await _call(legacy.order_cancel, ctx, oid=args.oid)
        elif sc == "cancel-all":
            await _call(legacy.order_cancel_all, ctx, yes=args.yes, coin=args.coin)
        elif sc == "set-leverage":
            await _call(
                legacy.order_set_leverage,
                ctx,
                args.coin,
                args.leverage,
                cross=args.cross,
                isolated=args.isolated,
            )
        elif sc == "configure":
            await _call(legacy.order_configure, ctx, slippage=args.slippage)
        else:
            _exit_with_error(f"Unknown order subcommand: {sc}")
        return

    if cmd == "asset":
        sc = args.asset_command
        if sc is None:
            _exit_with_error("Missing asset subcommand. Run: hl asset -h")
        if sc == "price":
            await _call(legacy.asset_price, ctx, args.coin, watch=args.watch)
        elif sc == "book":
            await _call(legacy.asset_book, ctx, args.coin, watch=args.watch)
        elif sc == "leverage":
            await _call(
                legacy.asset_leverage, ctx, args.coin, user=args.user, watch=args.watch
            )
        else:
            _exit_with_error(f"Unknown asset subcommand: {sc}")
        return

    if cmd == "markets":
        sc = args.markets_command
        if sc is None:
            _exit_with_error("Missing markets subcommand. Run: hl markets -h")
        if sc == "ls":
            await _call(
                legacy.markets_ls,
                ctx,
                spot_only=args.spot_only,
                perp_only=args.perp_only,
                category=args.category,
                sort_by=args.sort_by,
                watch=args.watch,
            )
        elif sc == "search":
            await _call(
                legacy.markets_search,
                ctx,
                args.query,
                spot_only=args.spot_only,
                perp_only=args.perp_only,
                category=args.category,
                sort_by=args.sort_by,
            )
        else:
            _exit_with_error(f"Unknown markets subcommand: {sc}")
        return

    if cmd == "referral":
        sc = args.referral_command
        if sc is None:
            _exit_with_error("Missing referral subcommand. Run: hl referral -h")
        if sc == "set":
            await _call(legacy.referral_set, ctx, args.code)
        elif sc == "status":
            await _call(legacy.referral_status, ctx)
        else:
            _exit_with_error(f"Unknown referral subcommand: {sc}")
        return

    if cmd == "completion":
        sc = args.completion_command
        if sc is None:
            _exit_with_error("Missing completion subcommand. Run: hl completion -h")
        _print_completion(sc)
        return

    _exit_with_error(f"Unknown command: {cmd}")


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    asyncio.run(dispatch(args, parser))


if __name__ == "__main__":
    main()
