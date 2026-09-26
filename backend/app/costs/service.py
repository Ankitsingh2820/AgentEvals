"""Applies estimated costs to a finished run. Kept separate from the engine: the executor
records what happened (tokens, calls); this module prices it.

Costs are computed once, when the run finishes, from the pricing in effect on the run's
start date, and then stored. Adding new prices later never changes a past run.
"""

import logging
from decimal import Decimal

from sqlalchemy.orm import Session

from app.costs.calculator import ZERO, MissingPriceError, llm_cost
from app.costs.pricing import find_model_pricing, find_tool_pricing, snapshot, to_prices
from app.models import Run

logger = logging.getLogger(__name__)


class CurrencyMismatchError(ValueError):
    pass


def apply_costs(db: Session, run: Run) -> None:
    on = run.started_at.date()
    llm_total, tool_total = ZERO, ZERO
    priced, unpriced = 0, 0
    currencies: set[str] = set()
    model_cache: dict[tuple[str, str], object] = {}
    tool_cache: dict[str, object] = {}

    for step in run.steps:
        if step.llm_call is not None:
            call = step.llm_call
            if call.total_tokens == 0:
                continue  # failed request with no usage reported: nothing to price
            key = (call.provider, call.model)
            if key not in model_cache:
                model_cache[key] = find_model_pricing(db, call.provider, call.model, on)
            pricing = model_cache[key]
            if pricing is None:
                unpriced += 1
                continue
            try:
                cost = llm_cost(
                    to_prices(pricing),
                    call.input_tokens,
                    call.output_tokens,
                    call.cache_creation_input_tokens,
                    call.cache_read_input_tokens,
                )
            except MissingPriceError as e:
                logger.warning("llm call not priced", extra={"run_id": run.id, "reason": str(e)})
                unpriced += 1
                continue
            call.pricing_id = pricing.id
            call.pricing_snapshot = snapshot(pricing)
            call.input_cost = cost.input
            call.output_cost = cost.output
            call.cache_write_cost = cost.cache_write
            call.cache_read_cost = cost.cache_read
            call.estimated_cost = cost.total
            llm_total += cost.total
            currencies.add(pricing.currency)
            priced += 1

        elif step.tool_call is not None:
            name = step.tool_call.tool_name
            if name not in tool_cache:
                tool_cache[name] = find_tool_pricing(db, name, on)
            tool_pricing = tool_cache[name]
            # Tools without a price entry are local/free and contribute nothing.
            if tool_pricing is not None:
                step.tool_call.pricing_id = tool_pricing.id
                step.tool_call.estimated_cost = Decimal(tool_pricing.price_per_call)
                tool_total += tool_pricing.price_per_call
                currencies.add(tool_pricing.currency)

    if len(currencies) > 1:
        raise CurrencyMismatchError(f"run mixes currencies {sorted(currencies)}")

    run.currency = currencies.pop() if currencies else None
    run.estimated_tool_cost = tool_total
    if unpriced == 0:
        run.cost_status = "complete"
        run.estimated_llm_cost = llm_total
        run.estimated_total_cost = llm_total + tool_total
    else:
        # A partial sum would understate the cost and silently skew averages, so run-level
        # totals stay empty; per-call costs that could be priced are still stored.
        run.cost_status = "partial" if priced else "unpriced"
        run.estimated_llm_cost = None
        run.estimated_total_cost = None
