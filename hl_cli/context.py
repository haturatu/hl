import os
from dataclasses import dataclass
from typing import Optional

from eth_account import Account as EthAccount
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils.constants import MAINNET_API_URL, TESTNET_API_URL

from .db import Account, get_default_account


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
        self._exchange: Optional[Exchange] = None
        self._perp_dexs: Optional[list[str]] = None

    @property
    def base_url(self) -> str:
        return TESTNET_API_URL if self.config.testnet else MAINNET_API_URL

    def get_public_client(self) -> Info:
        if self._info is None:
            self._info = Info(self.base_url, skip_ws=True, perp_dexs=self.get_perp_dexs())
        return self._info

    def get_wallet_client(self) -> Exchange:
        if self._exchange is None:
            if not self.config.private_key:
                if self.config.account and self.config.account.type == "readonly":
                    raise RuntimeError(
                        f'Account "{self.config.account.alias}" is read-only and cannot trade. '
                        "Use 'hl account add' to add an API wallet."
                    )
                raise RuntimeError("No account configured. Run 'hl account add'.")
            wallet = EthAccount.from_key(self.config.private_key)
            self._exchange = Exchange(
                wallet=wallet,
                base_url=self.base_url,
                account_address=self.get_wallet_address(),
                perp_dexs=self.get_perp_dexs(),
            )
        return self._exchange

    def get_perp_dexs(self) -> list[str]:
        if self._perp_dexs is not None:
            return self._perp_dexs
        try:
            temp = Info(self.base_url, skip_ws=True)
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
    default = None
    try:
        default = get_default_account()
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
