import asyncio
from typing import Any

from ..core.context import CLIContext
from ..core.testnet_policy import uses_main_perp_only

def account_perp_dexs(context: CLIContext) -> list[str]:
    if uses_main_perp_only(context.config.testnet):
        return [""]
    return context.get_perp_dexs()

async def _fetch_perp_states(context: CLIContext, user: str) -> list[dict[str, Any]]:
    info = context.get_public_client()
    return await asyncio.gather(
        *(asyncio.to_thread(info.user_state, user, dex) for dex in account_perp_dexs(context))
    )

async def _fetch_spot_state(context: CLIContext, user: str) -> dict[str, Any]:
    return await asyncio.to_thread(context.get_public_client().spot_user_state, user)

def _position_rows_from_states(states: list[dict[str, Any]]) -> list[dict[str, Any]]:
    positions: list[dict[str, Any]] = []
    for state in states:
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
    return positions

def _spot_balance_rows(spot_state: dict[str, Any]) -> list[dict[str, Any]]:
    balances: list[dict[str, Any]] = []
    for balance in spot_state["balances"]:
        if float(balance["total"]) == 0:
            continue
        total = float(balance["total"])
        hold = float(balance["hold"])
        balances.append(
            {
                "token": balance["coin"],
                "total": balance["total"],
                "hold": balance["hold"],
                "available": f"{total - hold}",
            }
        )
    return balances

def _margin_summary(perp_states: list[dict[str, Any]]) -> dict[str, str]:
    account_value = sum(float(state["marginSummary"].get("accountValue", 0) or 0) for state in perp_states)
    margin_used = sum(float(state["marginSummary"].get("totalMarginUsed", 0) or 0) for state in perp_states)
    return {
        "accountValue": f"{account_value:.8f}",
        "totalMarginUsed": f"{margin_used:.8f}",
    }

async def fetch_positions_async(context: CLIContext, user: str) -> dict[str, Any]:
    perp_states = await _fetch_perp_states(context, user)
    return {
        "positions": _position_rows_from_states(perp_states),
        "marginSummary": _margin_summary(perp_states),
    }

async def fetch_balances_async(context: CLIContext, user: str) -> dict[str, Any]:
    info = context.get_public_client()
    perp_task = asyncio.to_thread(info.user_state, user)
    spot_task = _fetch_spot_state(context, user)
    perp_state, spot_state = await asyncio.gather(perp_task, spot_task)
    return {
        "spotBalances": _spot_balance_rows(spot_state),
        "perpBalance": perp_state["marginSummary"]["accountValue"],
    }

async def fetch_portfolio_async(context: CLIContext, user: str) -> dict[str, Any]:
    perp_task = _fetch_perp_states(context, user)
    spot_task = _fetch_spot_state(context, user)
    perp_states, spot_state = await asyncio.gather(perp_task, spot_task)
    margin_summary = _margin_summary(perp_states)
    return {
        "positions": _position_rows_from_states(perp_states),
        "spotBalances": _spot_balance_rows(spot_state),
        "accountValue": margin_summary["accountValue"],
        "totalMarginUsed": margin_summary["totalMarginUsed"],
    }
