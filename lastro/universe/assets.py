"""Asset types that never enter the universe, whatever their volume.

The strategy follows trends in crypto assets. Three kinds of USDT pair look like
candidates by volume and are not:

- Leveraged tokens (Binance's UP/DOWN and the older BULL/BEAR): derivatives with
  a built-in rebalancing decay. Most of the extreme daily moves the quality
  checks found belong to them.
- Stablecoins and fiat currencies: nothing to follow; a 1% move is an event.
- Wrapped or pegged duplicates of assets already in the universe, and tokens
  pegged to off-chain assets such as gold.

The lists are plain Python so they are visible in the repository and reviewed
like code. A leveraged token is recognised by its suffix only when the rest of
the name is itself a listed asset, so JUP (Jupiter) stays and SUSHIUP goes.
"""

from __future__ import annotations

LEVERAGED_SUFFIXES = ("UP", "DOWN", "BULL", "BEAR")

STABLECOINS = frozenset({
    "USDT", "USDC", "BUSD", "TUSD", "USDP", "PAX", "DAI", "FDUSD", "USDE", "USDS", "USDSB", "USDD", "UST", "USTC",
    "SUSD", "GUSD", "LUSD", "FRAX", "USD1", "USDX", "USD0", "RLUSD", "PYUSD", "EURI", "AEUR", "EURC", "USDB",
    "BIDR", "IDRT", "BKRW", "BVND", "VAI", "USDJ", "MIM", "USDR", "CUSD", "XUSD",
})

FIAT = frozenset({
    "EUR", "GBP", "AUD", "BRL", "TRY", "RUB", "UAH", "NGN", "ZAR", "ARS", "JPY", "MXN", "PLN", "RON", "CZK", "CHF",
    "CAD", "COP", "KZT", "USD",
})

WRAPPED_OR_PEGGED = frozenset({
    "WBTC", "WBETH", "BETH", "WETH", "STETH", "CBBTC", "TBTC", "RENBTC", "BTCB", "BNBX", "WBNB", "RETH", "CBETH",
    "WSOL", "JITOSOL", "MSOL", "BNSOL", "PAXG", "XAUT", "KAU", "KAG",
})

REASON_LEVERAGED = "leveraged token"
REASON_STABLE = "stablecoin"
REASON_FIAT = "fiat currency"
REASON_WRAPPED = "wrapped or pegged asset"


def leveraged_base(base: str, known_bases: set[str] | frozenset[str]) -> str | None:
    """The underlying asset if `base` is a leveraged token on a listed asset, else None."""
    for suffix in LEVERAGED_SUFFIXES:
        if base.endswith(suffix) and len(base) > len(suffix):
            underlying = base[: -len(suffix)]
            if underlying in known_bases:
                return underlying
    return None


def exclusion_reason(base: str, known_bases: set[str] | frozenset[str]) -> str | None:
    """Why this base asset can never be in the universe, or None if its type is fine."""
    if base in STABLECOINS:
        return REASON_STABLE
    if base in FIAT:
        return REASON_FIAT
    if base in WRAPPED_OR_PEGGED:
        return REASON_WRAPPED
    if leveraged_base(base, known_bases):
        return REASON_LEVERAGED
    return None
