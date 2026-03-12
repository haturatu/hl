from typing import Literal, NotRequired, TypeAlias, TypedDict

from hyperliquid.utils.types import SpotAssetCtx, SpotMeta, SpotTokenInfo

NumericString: TypeAlias = str
DisplayNumericString: TypeAlias = NumericString | Literal["?"]
IsoTimestamp: TypeAlias = str
UnixMillis: TypeAlias = int
MarketKind: TypeAlias = Literal["perp", "spot"]
PerpDexName: TypeAlias = str
AllMids: TypeAlias = dict[str, NumericString]


class RawMarginSummary(TypedDict):
    accountValue: NumericString
    totalMarginUsed: NumericString
    totalNtlPos: NumericString
    totalRawUsd: NumericString


class CrossLeverage(TypedDict):
    type: Literal["cross"]
    value: int


class IsolatedLeverage(TypedDict):
    type: Literal["isolated"]
    value: int
    rawUsd: NumericString


Leverage: TypeAlias = CrossLeverage | IsolatedLeverage


class RawPosition(TypedDict):
    coin: str
    szi: NumericString
    leverage: Leverage
    marginUsed: NumericString
    positionValue: NumericString
    returnOnEquity: NumericString
    entryPx: NotRequired[NumericString | None]
    liquidationPx: NotRequired[NumericString | None]
    unrealizedPnl: NotRequired[NumericString | None]


class AssetPosition(TypedDict):
    position: RawPosition
    type: Literal["oneWay"]


class ClearinghouseState(TypedDict):
    marginSummary: RawMarginSummary
    crossMarginSummary: RawMarginSummary
    crossMaintenanceMarginUsed: NumericString
    withdrawable: NumericString
    assetPositions: list[AssetPosition]
    time: UnixMillis


class SpotBalance(TypedDict):
    coin: str
    token: int
    total: NumericString
    hold: NumericString
    entryNtl: NumericString


class SpotClearinghouseState(TypedDict):
    balances: list[SpotBalance]


class OpenOrderWire(TypedDict):
    coin: str
    limitPx: NumericString
    oid: int
    side: Literal["A", "B"]
    sz: NumericString
    timestamp: UnixMillis


class PerpUniverseAsset(TypedDict):
    szDecimals: int
    name: str
    maxLeverage: int
    marginTableId: int
    onlyIsolated: NotRequired[bool]
    isDelisted: NotRequired[bool]


class PerpAssetCtx(TypedDict):
    dayNtlVlm: NumericString
    funding: NumericString
    openInterest: NumericString
    oraclePx: NumericString
    prevDayPx: NumericString
    dayBaseVlm: NotRequired[NumericString]
    premium: NotRequired[NumericString]
    markPx: NotRequired[NumericString]
    midPx: NotRequired[NumericString | None]
    impactPxs: NotRequired[tuple[NumericString, NumericString] | None]


class PerpMeta(TypedDict):
    universe: list[PerpUniverseAsset]
    collateralToken: int
    dex: NotRequired[str]


class PerpDexInfo(TypedDict):
    name: str
    fullName: str
    deployer: str
    oracleUpdater: str | None
    feeRecipient: str | None
    assetToStreamingOiCap: list[tuple[str, NumericString]]
    subDeployers: list[str]
    deployerFeeScale: NumericString
    lastDeployerFeeScaleChangeTime: IsoTimestamp
    assetToFundingMultiplier: list[tuple[str, NumericString]]
    assetToFundingInterestRate: list[tuple[str, NumericString]]


class MarginSummary(TypedDict):
    accountValue: NumericString
    totalMarginUsed: NumericString


class PositionRow(TypedDict):
    coin: str
    size: NumericString
    entryPx: NumericString | None
    positionValue: NumericString | None
    unrealizedPnl: NumericString | None
    leverage: str
    liquidationPx: NumericString | Literal["-"]


class SpotBalanceRow(TypedDict):
    token: str
    total: NumericString
    hold: NumericString
    available: NumericString


class PositionsPayload(TypedDict):
    positions: list[PositionRow]
    marginSummary: MarginSummary


class BalancesPayload(TypedDict):
    spotBalances: list[SpotBalanceRow]
    perpBalance: NumericString


class PortfolioPayload(TypedDict):
    positions: list[PositionRow]
    spotBalances: list[SpotBalanceRow]
    accountValue: NumericString
    totalMarginUsed: NumericString


class OpenOrderRow(TypedDict):
    oid: int
    coin: str
    side: Literal["Buy", "Sell"]
    sz: NumericString
    limitPx: NumericString
    timestamp: IsoTimestamp


class MarketRowBase(TypedDict):
    coin: str
    pairName: str
    price: DisplayNumericString
    priceChange: float | None
    volumeUsd: DisplayNumericString


class MarketRow(MarketRowBase, total=False):
    marketType: MarketKind
    category: str | None
    funding: DisplayNumericString | None
    openInterest: DisplayNumericString | None
    openInterestUsd: float | None


class MarketsPayload(TypedDict):
    perpMarkets: list[MarketRow]
    spotMarkets: list[MarketRow]
