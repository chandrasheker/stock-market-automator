"""Zerodha order-margin / basket-margin adapters. Fail closed on missing numbers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from crude_research.exceptions import QuoteRequestError


class MarginProvider(Protocol):
    def standalone_short_margin(
        self, *, exchange: str, tradingsymbol: str, quantity: int, price: float
    ) -> float: ...

    def basket_final_margin(self, legs: Sequence[Mapping[str, Any]]) -> float: ...

    def live_charges(self, legs: Sequence[Mapping[str, Any]]) -> float | None: ...


def _num(payload: Mapping[str, Any], *keys: str) -> float | None:
    cur: object = payload
    for key in keys:
        if not isinstance(cur, Mapping) or key not in cur:
            return None
        cur = cur[key]
    if isinstance(cur, (int, float)) and not isinstance(cur, bool):
        return float(cur)
    return None


def parse_order_margin_total(payload: object) -> float:
    if isinstance(payload, list) and payload:
        payload = payload[0]
    if not isinstance(payload, Mapping):
        raise QuoteRequestError("order_margins returned no mapping")
    total = (
        _num(payload, "total")
        or _num(payload, "margin")
        or _num(payload, "SPAN")
        or _num(payload, "span")
    )
    if total is None or total <= 0:
        raise QuoteRequestError("order_margins missing a positive total")
    return total


def parse_basket_final_total(payload: object) -> float:
    if not isinstance(payload, Mapping):
        raise QuoteRequestError("basket_order_margins returned no mapping")
    total = (
        _num(payload, "final", "total")
        or _num(payload, "final_margin")
        or _num(payload, "final")
        or _num(payload, "total")
    )
    if total is None or total <= 0:
        raise QuoteRequestError("basket_order_margins missing final.total")
    return total


class FixedMarginProvider:
    """Deterministic margins for paper/backtest/tests. Not a live Kite response."""

    def __init__(
        self,
        *,
        standalone: float = 20_000.0,
        basket: float = 8_000.0,
        charges: float | None = 40.0,
    ) -> None:
        self.standalone = standalone
        self.basket = basket
        self.charges = charges

    def standalone_short_margin(
        self, *, exchange: str, tradingsymbol: str, quantity: int, price: float
    ) -> float:
        del exchange, tradingsymbol, quantity, price
        return self.standalone

    def basket_final_margin(self, legs: Sequence[Mapping[str, Any]]) -> float:
        del legs
        return self.basket

    def live_charges(self, legs: Sequence[Mapping[str, Any]]) -> float | None:
        del legs
        return self.charges


class KiteMarginProvider:
    def __init__(self, kite: Any) -> None:
        self._kite = kite

    def standalone_short_margin(
        self, *, exchange: str, tradingsymbol: str, quantity: int, price: float
    ) -> float:
        params = [
            {
                "exchange": exchange,
                "tradingsymbol": tradingsymbol,
                "transaction_type": "SELL",
                "variety": "regular",
                "product": "NRML",
                "order_type": "LIMIT",
                "quantity": quantity,
                "price": price,
            }
        ]
        return parse_order_margin_total(self._kite.order_margins(params))

    def basket_final_margin(self, legs: Sequence[Mapping[str, Any]]) -> float:
        return parse_basket_final_total(self._kite.basket_order_margins(list(legs)))

    def live_charges(self, legs: Sequence[Mapping[str, Any]]) -> float | None:
        try:
            raw = self._kite.basket_order_margins(list(legs))
        except Exception:
            return None
        if not isinstance(raw, Mapping):
            return None
        return _num(raw, "final", "charges") or _num(raw, "charges")
