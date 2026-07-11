import json
from typing import Iterable, cast

from hyperliquid.utils.types import L2BookData
from rich.console import Console
from rich.table import Table

from ..i18n import _
from ..types import (
    AccountRecord,
    AssetLeveragePayload,
    BalancesPayload,
    CancelNoopPayload,
    DisplayValue,
    ExchangeOrderStatus,
    ExchangeErrorEnvelope,
    ExchangeResponse,
    ExchangeStatusWithError,
    ExchangeSuccessEnvelope,
    JsonObject,
    JsonValue,
    MarketsPayload,
    OpenOrderRow,
    PortfolioPayload,
    PositionsPayload,
    TwapCancelPayload,
)
from .market_table import (
    build_market_table,
    market_table_columns,
    market_table_row_values,
    market_table_widths,
)

console = Console()


def out(data: JsonValue, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    if _render_known(data):
        return
    if isinstance(data, str):
        console.print(data)
        return
    console.print_json(json.dumps(data, ensure_ascii=False))


def _render_known(data: JsonValue) -> bool:
    if isinstance(data, list):
        if _print_open_orders_list(data):
            return True
        if _print_accounts_list(data):
            return True
        return False

    if not isinstance(data, dict):
        return False

    if "perpMarkets" in data and "spotMarkets" in data:
        _print_markets_payload(cast(MarketsPayload, data))
        return True

    if (
        "positions" in data
        and "spotBalances" in data
        and "accountValue" in data
        and "totalMarginUsed" in data
    ):
        _print_portfolio_payload(cast(PortfolioPayload, data))
        return True

    if "positions" in data and isinstance(data.get("positions"), list):
        _print_positions_payload(cast(PositionsPayload, data))
        return True

    if _print_account_record(data):
        return True

    if "spotBalances" in data and "perpBalance" in data:
        _print_balances_payload(cast(BalancesPayload, data))
        return True

    if "coin" in data and "price" in data and len(data.keys()) <= 3:
        console.print(_("Market Price"))
        console.print(_("- Asset: {coin}").format(coin=data.get("coin")))
        console.print(_("- Price: {price}").format(price=_fmt_price(data.get("price"))))
        return True

    if (
        "coin" in data
        and "markPx" in data
        and "maxLeverage" in data
        and "margin" in data
    ):
        _print_asset_leverage_payload(cast(AssetLeveragePayload, data))
        return True

    if "levels" in data and isinstance(data.get("levels"), list):
        _print_book_payload(cast(L2BookData, data))
        return True

    if "slippage" in data and len(data.keys()) == 1:
        console.print(_("Order Defaults"))
        console.print(
            _("- Slippage: {slippage}").format(slippage=_fmt_pct(data.get("slippage")))
        )
        return True

    if "twapCancel" in data and isinstance(data["twapCancel"], dict):
        _print_twap_cancel_payload(cast(TwapCancelPayload, data["twapCancel"]))
        return True

    if data.get("status") == "ok" and isinstance(data.get("response"), dict):
        _print_exchange_response(cast(ExchangeResponse, data["response"]))
        return True

    if _print_cancel_noop(data):
        return True

    if data.get("status") == "err":
        err = cast(ExchangeErrorEnvelope, data)
        console.print(_("[red]❌ Request failed[/red]"))
        console.print(_("Reason: {reason}").format(reason=err.get("response")))
        return True

    if _print_flat_dict(data):
        return True

    return False


def _print_positions_payload(data: PositionsPayload) -> None:
    positions = data.get("positions", [])
    if positions:
        tbl = Table(title=_("Positions"))
        for c in [
            "coin",
            "size",
            "entryPx",
            "positionValue",
            "unrealizedPnl",
            "leverage",
            "liquidationPx",
        ]:
            tbl.add_column(c)
        for p in positions:
            tbl.add_row(
                str(p.get("coin", "")),
                str(p.get("size", "")),
                str(p.get("entryPx", "")),
                str(p.get("positionValue", "")),
                str(p.get("unrealizedPnl", "")),
                str(p.get("leverage", "")),
                str(p.get("liquidationPx", "")),
            )
        console.print(tbl)
    else:
        console.print(_("No open positions"))

    ms = data.get("marginSummary")
    if isinstance(ms, dict):
        console.print(_("Margin Summary"))
        console.print(
            _("- Account value: {value}").format(value=_fmt_usd(ms.get("accountValue")))
        )
        console.print(
            _("- Total margin used: {value}").format(
                value=_fmt_usd(ms.get("totalMarginUsed"))
            )
        )


def _print_balances_payload(data: BalancesPayload) -> None:
    console.print(_("Balances"))
    console.print(
        _("- Perp balance: {value}").format(value=_fmt_usd(data.get("perpBalance")))
    )
    balances = data.get("spotBalances", [])
    if not balances:
        console.print(_("No spot balances"))
        return

    tbl = Table(title=_("Spot Balances"))
    cols = ["token", "total", "hold", "available"]
    for c in cols:
        tbl.add_column(c)
    for b in balances:
        tbl.add_row(*[str(b.get(c, "")) for c in cols])
    console.print(tbl)


def _iter_statuses(statuses: Iterable[ExchangeOrderStatus]) -> None:
    for s in statuses:
        if isinstance(s, str):
            console.print(s)
            continue
        if not isinstance(s, dict):
            console.print(str(s))
            continue
        if "error" in s:
            console.print(_("[red]❌ Order rejected[/red]"))
            console.print(_("Reason: {reason}").format(reason=s["error"]))
            continue
        if "filled" in s and isinstance(s["filled"], dict):
            f = s["filled"]
            console.print(_("[green]✅ Order filled[/green]"))
            console.print(_("Filled size: {size}").format(size=f.get("totalSz")))
            console.print(
                _("- Average price: {price}").format(price=_fmt_usd(f.get("avgPx")))
            )
            console.print(_("Order ID: {oid}").format(oid=f.get("oid")))
            continue
        if "resting" in s and isinstance(s["resting"], dict):
            r = s["resting"]
            console.print(_("[cyan]🕒 Order resting on book[/cyan]"))
            console.print(_("Order ID: {oid}").format(oid=r.get("oid")))
            continue
        console.print(json.dumps(s, ensure_ascii=False))


def _print_exchange_response(resp: ExchangeResponse) -> None:
    rtype = resp.get("type")
    data = resp.get("data")

    if rtype == "order":
        statuses = []
        if isinstance(data, dict):
            statuses = data.get("statuses") or []
        if statuses:
            _iter_statuses(statuses)
            return
        console.print(_("Order request accepted"))
        return

    if rtype in {"cancel", "batchModify", "twapOrder", "twapCancel", "default"}:
        if isinstance(data, dict) and "statuses" in data:
            _iter_statuses(data.get("statuses") or [])
            return
        if isinstance(data, dict) and "status" in data:
            s = data.get("status")
            if isinstance(s, dict) and "error" in s:
                err = cast(ExchangeStatusWithError, s)
                console.print(_("[red]Error:[/red] {error}").format(error=err["error"]))
            else:
                console.print(_("{rtype}: {status}").format(rtype=rtype, status=s))
            return
        console.print(_("{rtype}: ok").format(rtype=rtype))
        return

    console.print_json(
        json.dumps({"status": "ok", "response": resp}, ensure_ascii=False)
    )


def _print_open_orders_list(data: list[JsonValue]) -> bool:
    if not all(isinstance(x, dict) for x in data):
        return False
    rows: list[OpenOrderRow] = [
        cast(OpenOrderRow, x)
        for x in data
        if isinstance(x, dict)
        and {"oid", "coin", "side", "sz", "limitPx"}.issubset(x.keys())
    ]
    if len(rows) != len(data):
        return False
    if not rows:
        console.print(_("No open orders"))
        return
    tbl = Table(title=_("Open Orders"))
    for c in ["oid", "coin", "side", "sz", "limitPx", "timestamp"]:
        tbl.add_column(c)
    for r in rows:
        tbl.add_row(
            str(r.get("oid", "")),
            str(r.get("coin", "")),
            str(r.get("side", "")),
            str(r.get("sz", "")),
            _fmt_usd(r.get("limitPx")),
            str(r.get("timestamp", "")),
        )
    console.print(tbl)
    return True


def _print_accounts_list(data: list[JsonValue]) -> bool:
    if not all(isinstance(x, dict) for x in data):
        return False
    rows: list[AccountRecord] = [
        cast(AccountRecord, x)
        for x in data
        if isinstance(x, dict)
        and {"alias", "user_address", "type", "is_default"}.issubset(x.keys())
    ]
    if len(rows) != len(data):
        return False
    tbl = Table(title=_("Accounts"))
    for c in [
        "alias",
        "user_address",
        "type",
        "source",
        "api_wallet_public_key",
        "is_default",
    ]:
        tbl.add_column(c)
    for r in rows:
        tbl.add_row(
            str(r.get("alias", "")),
            str(r.get("user_address", "")),
            str(r.get("type", "")),
            str(r.get("source", "")),
            str(r.get("api_wallet_public_key") or "-"),
            "yes" if r.get("is_default") else "",
        )
    console.print(tbl)
    return True


def _print_account_record(data: JsonObject) -> bool:
    if not {"alias", "user_address", "type"}.issubset(data.keys()):
        return False
    account = cast(AccountRecord, data)
    console.print(_("[green]✅ Account saved[/green]"))
    console.print(_("Alias: {alias}").format(alias=account.get("alias")))
    console.print(_("Address: {address}").format(address=account.get("user_address")))
    console.print(_("Type: {type}").format(type=account.get("type")))
    if account.get("api_wallet_public_key"):
        console.print(
            _("API wallet: {wallet}").format(
                wallet=account.get("api_wallet_public_key")
            )
        )
    return True


def _print_portfolio_payload(data: PortfolioPayload) -> None:
    console.print(_("Portfolio"))
    console.print(
        _("- Account value: {value}").format(value=_fmt_usd(data.get("accountValue")))
    )
    console.print(
        _("- Margin used: {value}").format(value=_fmt_usd(data.get("totalMarginUsed")))
    )
    _print_positions_payload({"positions": data.get("positions", [])})
    _print_balances_payload(
        {
            "spotBalances": data.get("spotBalances", []),
            "perpBalance": data.get("accountValue"),
        }
    )


def _print_markets_payload(data: MarketsPayload) -> None:
    perp = data.get("perpMarkets", [])
    spot = data.get("spotMarkets", [])
    show_perp_category = any("category" in r for r in perp)
    show_spot_category = any("category" in r for r in spot)
    console.print(
        _("Markets: {perp} perp / {spot} spot").format(perp=len(perp), spot=len(spot))
    )
    if perp:
        columns = market_table_columns(
            include_category=show_perp_category,
            show_perp_only_fields=True,
        )
        rendered_rows = [
            market_table_row_values(
                r,
                include_category=show_perp_category,
                show_perp_only_fields=True,
                format_price=_fmt_price,
                format_usd=_fmt_usd,
                format_rate_pct=_fmt_rate_pct,
            )
            for r in perp
        ]
        tbl = build_market_table(
            title=_("Perp Markets"),
            columns=columns,
            rendered_rows=rendered_rows,
            widths=market_table_widths(columns, rendered_rows),
        )
        console.print(tbl)
    if spot:
        columns = market_table_columns(
            include_category=show_spot_category,
            show_perp_only_fields=False,
        )
        rendered_rows = [
            market_table_row_values(
                r,
                include_category=show_spot_category,
                show_perp_only_fields=False,
                format_price=_fmt_price,
                format_usd=_fmt_usd,
                format_rate_pct=_fmt_rate_pct,
            )
            for r in spot
        ]
        tbl = build_market_table(
            title=_("Spot Markets"),
            columns=columns,
            rendered_rows=rendered_rows,
            widths=market_table_widths(columns, rendered_rows),
        )
        console.print(tbl)


def _print_asset_leverage_payload(data: AssetLeveragePayload) -> None:
    console.print(_("Asset Leverage"))
    console.print(_("- Asset: {coin}").format(coin=data.get("coin")))
    console.print(
        _("- Mark price: {price}").format(price=_fmt_usd(data.get("markPx")))
    )
    console.print(
        _("- Max leverage: {leverage}x").format(leverage=data.get("maxLeverage"))
    )
    margin = data.get("margin") or {}
    console.print(_("Margin"))
    console.print(
        _("- Account value: {value}").format(value=_fmt_usd(margin.get("accountValue")))
    )
    console.print(
        _("- Margin used: {value}").format(value=_fmt_usd(margin.get("totalMarginUsed")))
    )
    console.print(
        _("- Available margin: {value}").format(
            value=_fmt_usd(margin.get("availableMargin"))
        )
    )
    pos = data.get("position")
    if isinstance(pos, dict):
        console.print(_("Position"))
        console.print(_("- Size: {size}").format(size=pos.get("szi")))
        console.print(_("- Entry: {price}").format(price=_fmt_usd(pos.get("entryPx"))))
        console.print(_("- Value: {value}").format(value=_fmt_usd(pos.get("positionValue"))))
        console.print(
            _("- Unrealized PnL: {value}").format(
                value=_fmt_usd(pos.get("unrealizedPnl"))
            )
        )
    else:
        console.print(_("Position: none"))


def _print_book_payload(data: L2BookData) -> None:
    levels = data.get("levels", [[], []])
    bids = levels[0][:10] if len(levels) > 0 else []
    asks = levels[1][:10] if len(levels) > 1 else []
    if asks:
        tbl = Table(title=_("Asks ({coin})").format(coin=data.get("coin", "-")))
        for c in ["px", "sz", "n"]:
            tbl.add_column(c)
        for x in asks[::-1]:
            tbl.add_row(
                _fmt_usd(x.get("px")), str(x.get("sz", "")), str(x.get("n", ""))
            )
        console.print(tbl)
    if bids:
        tbl = Table(title=_("Bids ({coin})").format(coin=data.get("coin", "-")))
        for c in ["px", "sz", "n"]:
            tbl.add_column(c)
        for x in bids:
            tbl.add_row(
                _fmt_usd(x.get("px")), str(x.get("sz", "")), str(x.get("n", ""))
            )
        console.print(tbl)


def _print_twap_cancel_payload(data: TwapCancelPayload) -> None:
    coin = data.get("coin")
    twap_id = data.get("twapId")
    response: ExchangeSuccessEnvelope = data["response"]
    status = response.get("response", {}).get("data", {}).get("status", {})
    if isinstance(status, dict) and status.get("error"):
        console.print(_("[red]❌ TWAP cancel rejected[/red]"))
        console.print(_("Asset: {coin}").format(coin=coin))
        console.print(_("TWAP ID: {twap_id}").format(twap_id=twap_id))
        console.print(_("Reason: {reason}").format(reason=status.get("error")))
        return
    console.print(_("[green]✅ TWAP cancel submitted[/green]"))
    console.print(_("Asset: {coin}").format(coin=coin))
    console.print(_("TWAP ID: {twap_id}").format(twap_id=twap_id))


def _print_cancel_noop(data: JsonObject) -> bool:
    if "cancelled" in data and "reason" in data:
        payload = cast(CancelNoopPayload, data)
        console.print(_(payload.get("message", _("No-op"))))
        return True
    return False


def _print_flat_dict(data: JsonObject) -> bool:
    if not data:
        return False
    if any(isinstance(v, (dict, list, tuple, set)) for v in data.values()):
        return False
    for k, v in data.items():
        console.print(f"{k}: {v}")
    return True


def _fmt_usd(value: DisplayValue) -> str:
    if value is None:
        return "-"
    try:
        n = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"${n:,.2f}"


def _fmt_price(value: DisplayValue) -> str:
    if value is None:
        return "-"
    try:
        n = float(value)
    except (TypeError, ValueError):
        return str(value)

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


def _fmt_pct(value: DisplayValue) -> str:
    if value is None:
        return "-"
    try:
        n = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{n:+.2f}%"


def _fmt_rate_pct(value: DisplayValue) -> str:
    if value is None:
        return "-"
    try:
        n = float(value)
    except (TypeError, ValueError):
        return str(value)

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


def out_error(message: str) -> None:
    console.print(_("[red]Error:[/red] {message}").format(message=message))


def out_success(message: str) -> None:
    console.print(_("[green]{message}[/green]").format(message=message))
