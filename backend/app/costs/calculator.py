"""Pure cost arithmetic. No database, no provider knowledge: prices in, Decimals out.

    input_cost  = input_tokens  / 1,000,000 x input_price_per_mtok
    output_cost = output_tokens / 1,000,000 x output_price_per_mtok
    (+ the same for prompt-cache writes and reads, at their own prices)

Decimal is used throughout so sums of many tiny per-call costs do not drift.
"""

from dataclasses import dataclass
from decimal import Decimal

MTOK = Decimal(1_000_000)
ZERO = Decimal(0)


class MissingPriceError(ValueError):
    """Tokens of a kind were used but the price list has no price for that kind."""


@dataclass(frozen=True)
class ModelPrices:
    input_per_mtok: Decimal
    output_per_mtok: Decimal
    cache_write_per_mtok: Decimal | None = None
    cache_read_per_mtok: Decimal | None = None
    currency: str = "USD"


@dataclass(frozen=True)
class LLMCost:
    input: Decimal
    output: Decimal
    cache_write: Decimal
    cache_read: Decimal

    @property
    def total(self) -> Decimal:
        return self.input + self.output + self.cache_write + self.cache_read


def _per_mtok(tokens: int, price: Decimal | None, kind: str) -> Decimal:
    if tokens < 0:
        raise ValueError(f"negative {kind} token count: {tokens}")
    if tokens == 0:
        return ZERO
    if price is None:
        raise MissingPriceError(f"{tokens} {kind} tokens used but no {kind} price is set")
    return Decimal(tokens) / MTOK * price


def llm_cost(
    prices: ModelPrices,
    input_tokens: int,
    output_tokens: int,
    cache_write_tokens: int = 0,
    cache_read_tokens: int = 0,
) -> LLMCost:
    return LLMCost(
        input=_per_mtok(input_tokens, prices.input_per_mtok, "input"),
        output=_per_mtok(output_tokens, prices.output_per_mtok, "output"),
        cache_write=_per_mtok(cache_write_tokens, prices.cache_write_per_mtok, "cache write"),
        cache_read=_per_mtok(cache_read_tokens, prices.cache_read_per_mtok, "cache read"),
    )
