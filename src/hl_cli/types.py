from typing import Any, Literal, TypeAlias, TypedDict


class MarginSummary(TypedDict):
    accountValue: str
    totalMarginUsed: str


class PositionRow(TypedDict):
    coin: str
    size: Any
    entryPx: Any
    positionValue: Any
    unrealizedPnl: Any
    leverage: str
    liquidationPx: Any


class SpotBalanceRow(TypedDict):
    token: str
    total: Any
    hold: Any
    available: str


class PositionsPayload(TypedDict):
    positions: list[PositionRow]
    marginSummary: MarginSummary


class BalancesPayload(TypedDict):
    spotBalances: list[SpotBalanceRow]
    perpBalance: Any


class PortfolioPayload(TypedDict):
    positions: list[PositionRow]
    spotBalances: list[SpotBalanceRow]
    accountValue: str
    totalMarginUsed: str


class OpenOrderRow(TypedDict):
    oid: Any
    coin: str
    side: Literal["Buy", "Sell"]
    sz: Any
    limitPx: Any
    timestamp: str


class MarketRow(TypedDict):
    coin: str
    marketType: Literal["perp", "spot"]
    category: str | None
    pairName: str
    price: Any
    priceChange: float | None
    volumeUsd: Any
    funding: Any
    openInterest: Any
    openInterestUsd: float | None


class MarketsPayload(TypedDict):
    perpMarkets: list[MarketRow]
    spotMarkets: list[MarketRow]


MarketKind: TypeAlias = Literal["perp", "spot"]
