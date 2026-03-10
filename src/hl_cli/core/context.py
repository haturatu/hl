import os
from dataclasses import dataclass
from typing import Optional

from eth_account import Account as EthAccount
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils.constants import MAINNET_API_URL, TESTNET_API_URL
import requests

from ..infra.db import Account, get_default_account


@dataclass
class Config:
    private_key: Optional[str]
    wallet_address: Optional[str]
    testnet: bool
    account: Optional[Account]


class CLIContext:
    def __init__(self, config: Config):
        self.config = config
        self._info: Optional[Info] = None
        self._multi_perp_info: Optional[Info] = None
        self._exchange_clients: dict[tuple[str, ...], Exchange] = {}
        self._perp_dexs: Optional[list[str]] = None

    @property
    def base_url(self) -> str:
        return TESTNET_API_URL if self.config.testnet else MAINNET_API_URL

    def get_public_client(self) -> Info:
        if self._info is None:
            self._info = _build_info_client(self.base_url, skip_ws=True)
        return self._info

    def get_multi_perp_public_client(self) -> Info:
        if self._multi_perp_info is None:
            self._multi_perp_info = _build_info_client(
                self.base_url,
                skip_ws=True,
                perp_dexs=self.get_perp_dexs(),
            )
        return self._multi_perp_info

    def get_wallet_client(self, perp_dexs: Optional[list[str]] = None) -> Exchange:
        key = tuple(perp_dexs or [])
        if key not in self._exchange_clients:
            if not self.config.private_key:
                if self.config.account and self.config.account.type == "readonly":
                    raise RuntimeError(
                        f'Account "{self.config.account.alias}" is read-only and cannot trade. '
                        "Use 'hl account add' to add an API wallet."
                    )
                raise RuntimeError("No account configured. Run 'hl account add'.")
            wallet = EthAccount.from_key(self.config.private_key)
            kwargs = {
                "wallet": wallet,
                "base_url": self.base_url,
                "account_address": self.get_wallet_address(),
            }
            if perp_dexs is not None:
                kwargs["perp_dexs"] = perp_dexs
            try:
                self._exchange_clients[key] = Exchange(**kwargs)
            except IndexError:
                self._exchange_clients[key] = Exchange(
                    **kwargs,
                    spot_meta=_load_safe_spot_meta(self.base_url),
                )
        return self._exchange_clients[key]

    def get_perp_dexs(self) -> list[str]:
        if self._perp_dexs is not None:
            return self._perp_dexs
        try:
            temp = _build_info_client(self.base_url, skip_ws=True)
            raw = temp.perp_dexs()
            names = [str(x.get("name")) for x in raw if isinstance(x, dict) and x.get("name")]
            self._perp_dexs = ["", *names] if names else [""]
        except Exception:
            self._perp_dexs = [""]
        return self._perp_dexs

    def get_wallet_address(self) -> str:
        if self.config.wallet_address:
            return self.config.wallet_address
        if self.config.private_key:
            return EthAccount.from_key(self.config.private_key).address
        raise RuntimeError("No account configured. Run 'hl account add'.")


def load_config(testnet: bool) -> Config:
    network = "testnet" if testnet else "mainnet"
    default = None
    try:
        default = get_default_account(network)
    except Exception:
        default = None

    if default:
        return Config(
            private_key=default.api_wallet_private_key,
            wallet_address=default.user_address,
            testnet=testnet,
            account=default,
        )

    private_key = os.getenv("HYPERLIQUID_PRIVATE_KEY")
    wallet_address = os.getenv("HYPERLIQUID_WALLET_ADDRESS")

    if private_key and not wallet_address:
        wallet_address = EthAccount.from_key(private_key).address

    return Config(
        private_key=private_key,
        wallet_address=wallet_address,
        testnet=testnet,
        account=None,
    )


def _load_safe_spot_meta(base_url: str) -> dict:
    # Testnet currently returns some spot pairs with invalid token indexes.
    # Filter those out before constructing the SDK client.
    response = requests.post(f"{base_url}/info", json={"type": "spotMeta"}, timeout=20)
    response.raise_for_status()
    spot_meta = response.json()
    tokens = spot_meta.get("tokens", [])
    max_idx = len(tokens) - 1

    safe_universe = []
    for pair in spot_meta.get("universe", []):
        refs = pair.get("tokens")
        if not isinstance(refs, list) or len(refs) < 2:
            continue
        if any(not isinstance(ref, int) or ref < 0 or ref > max_idx for ref in refs[:2]):
            continue
        safe_universe.append(pair)

    return {
        **spot_meta,
        "universe": safe_universe,
    }


def _build_info_client(base_url: str, **kwargs: object) -> Info:
    try:
        return Info(base_url, **kwargs)
    except IndexError:
        # Testnet-only defensive fallback for malformed spot metadata.
        if "spot_meta" in kwargs:
            raise
        return Info(base_url, spot_meta=_load_safe_spot_meta(base_url), **kwargs)
