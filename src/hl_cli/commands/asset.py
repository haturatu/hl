import asyncio
import json
from queue import Empty, Queue
from typing import Optional

from hyperliquid.info import Info
from hyperliquid.utils.types import L2BookData

from ..cli.runtime import CommandContext, cli_command, console, run_blocking
from ..core.context import _build_info_client
from ..i18n import _
from ..types import AllMids, AssetLeveragePayload, ClearinghouseState, PerpMeta
from ..utils.output import out
from ..utils.validators import validate_address
from ..utils.watch import watch_loop
from .common import _ctx, _done, _format_price, _json, _render_table
from .order import _mids_for_coin, _resolve_tradable_coin


@cli_command
def asset_price(ctx: CommandContext, coin: str, watch: bool = False) -> None:
    context = _ctx(ctx)

    def fetch() -> dict[str, str]:
        resolved_coin = _resolve_tradable_coin(context, coin)
        mids = _mids_for_coin(context, resolved_coin)
        if resolved_coin not in mids:
            raise RuntimeError(_("Coin not found: {coin}").format(coin=coin))
        return {"coin": coin, "price": mids[resolved_coin]}

    if watch:
        watch_loop(
            fetch,
            lambda d: print(f"{d['coin']}: {_format_price(d['price'])}"),
            as_json=_json(ctx),
        )
        return
    out(fetch(), _json(ctx))
    _done(ctx)


@cli_command
def asset_book(ctx: CommandContext, coin: str, watch: bool = False) -> None:
    context = _ctx(ctx)

    def fetch() -> L2BookData:
        return context.get_public_client().l2_snapshot(coin)

    def render_book(book: L2BookData) -> None:
        bids = book.get("levels", [[], []])[0][:10]
        asks = book.get("levels", [[], []])[1][:10]
        _render_table(
            _("Asks"),
            [_("Price"), _("Size"), _("N")],
            [[x["px"], x["sz"], x["n"]] for x in asks[::-1]],
        )
        _render_table(
            _("Bids"),
            [_("Price"), _("Size"), _("N")],
            [[x["px"], x["sz"], x["n"]] for x in bids],
        )

    if watch:
        stream_info = _build_info_client(context.base_url, skip_ws=False)
        updates: Queue[L2BookData] = Queue()
        subscription = {"type": "l2Book", "coin": coin}
        subscription_id = stream_info.subscribe(
            subscription, lambda msg: updates.put(msg["data"])
        )

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
    ctx: CommandContext, coin: str, user: Optional[str] = None, watch: bool = False
) -> None:
    context = _ctx(ctx)
    address = validate_address(user) if user else context.get_wallet_address()

    def fetch() -> AssetLeveragePayload:
        info = context.get_public_client()
        state, meta, mids = run_blocking(
            _fetch_asset_leverage_inputs_async(info, address)
        )
        pos = next(
            (
                p["position"]
                for p in state["assetPositions"]
                if p["position"]["coin"] == coin
            ),
            None,
        )
        market = next((m for m in meta["universe"] if m["name"] == coin), None)
        account_value = float(state["marginSummary"]["accountValue"])
        margin_used = float(state["marginSummary"]["totalMarginUsed"])
        return {
            "coin": coin,
            "markPx": mids.get(coin),
            "maxLeverage": (market or {}).get("maxLeverage", 0),
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


async def _fetch_asset_leverage_inputs_async(
    info: Info, address: str
) -> tuple[ClearinghouseState, PerpMeta, AllMids]:
    state_task = asyncio.to_thread(info.user_state, address)
    meta_task = asyncio.to_thread(info.meta)
    mids_task = asyncio.to_thread(info.all_mids)
    state, meta, mids = await asyncio.gather(state_task, meta_task, mids_task)
    return state, meta, mids
