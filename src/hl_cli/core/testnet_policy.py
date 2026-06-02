from hyperliquid.utils.types import SpotAssetInfo, SpotMeta, SpotTokenInfo


def uses_main_perp_only(testnet: bool) -> bool:
    return testnet


def includes_builder_perps(testnet: bool) -> bool:
    return not testnet


def valid_spot_token_refs(tokens: list[SpotTokenInfo], refs: object) -> bool:
    if not isinstance(refs, list) or len(refs) < 2:
        return False
    max_idx = len(tokens) - 1
    return not any(
        not isinstance(ref, int) or ref < 0 or ref > max_idx for ref in refs[:2]
    )


def filter_safe_spot_universe(spot_meta: SpotMeta) -> list[SpotAssetInfo]:
    tokens = spot_meta.get("tokens", [])
    return [
        pair
        for pair in spot_meta.get("universe", [])
        if valid_spot_token_refs(tokens, pair.get("tokens"))
    ]
