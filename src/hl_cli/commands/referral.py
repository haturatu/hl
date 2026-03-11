from typing import Any

from ..cli.runtime import cli_command
from ..utils.output import out
from .common import _ctx, _done, _json


@cli_command
def referral_set(ctx: Any, code: str) -> None:
    result = _ctx(ctx).get_wallet_client().set_referrer(code)
    out(result, _json(ctx))
    _done(ctx)


@cli_command
def referral_status(ctx: Any) -> None:
    context = _ctx(ctx)
    result = context.get_public_client().query_referral_state(context.get_wallet_address())
    out(result, _json(ctx))
    _done(ctx)
