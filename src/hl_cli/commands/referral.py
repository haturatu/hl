from ..cli.runtime import CommandContext, cli_command
from ..utils.output import out
from .common import _ctx, _done, _json

@cli_command
def referral_set(ctx: CommandContext, code: str) -> None:
    result = _ctx(ctx).get_wallet_client().set_referrer(code)
    out(result, _json(ctx))
    _done(ctx)

@cli_command
def referral_status(ctx: CommandContext) -> None:
    context = _ctx(ctx)
    result = context.get_public_client().query_referral_state(context.get_wallet_address())
    out(result, _json(ctx))
    _done(ctx)
