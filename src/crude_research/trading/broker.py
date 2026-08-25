"""Broker interface. Paper fills are conservative; LIVE orders stay disabled unless armed."""

from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from crude_research.config import Settings
from crude_research.exceptions import LiveTradingDisabledError
from crude_research.strategy.margins import FixedMarginProvider, KiteMarginProvider, MarginProvider

log = logging.getLogger(__name__)

Txn = Literal["BUY", "SELL"]


@dataclass
class Order:
    order_id: str
    tradingsymbol: str
    transaction_type: Txn
    quantity: int
    price: float
    status: str
    filled_quantity: int = 0
    pending_quantity: int = 0
    average_price: float | None = None
    tag: str = ""


@dataclass
class Gtt:
    gtt_id: str
    tradingsymbol: str
    status: str
    trigger_values: list[float]
    orders: list[dict[str, Any]]
    triggered_order_ids: list[str] = field(default_factory=list)


class Broker(Protocol):
    margin: MarginProvider

    def place_order(
        self,
        *,
        tradingsymbol: str,
        transaction_type: Txn,
        quantity: int,
        price: float,
        tag: str = "",
    ) -> Order: ...

    def modify_order(self, order_id: str, *, price: float | None = None) -> Order: ...

    def cancel_order(self, order_id: str) -> Order: ...

    def orders(self) -> list[Order]: ...

    def positions(self) -> list[dict[str, Any]]: ...

    def trades(self) -> list[dict[str, Any]]: ...

    def place_gtt(
        self,
        *,
        tradingsymbol: str,
        last_price: float,
        quantity: int,
        target_price: float,
        stop_price: float,
    ) -> Gtt: ...

    def get_gtts(self) -> list[Gtt]: ...

    def delete_gtt(self, gtt_id: str) -> None: ...

    def ltp(self, tradingsymbol: str) -> float | None: ...


class PaperBroker:
    """Conservative paper fills: BUY at ask+slip, SELL at bid-slip. Never hits Kite orders."""

    def __init__(self, settings: Settings, *, quotes: dict[str, tuple[float, float]] | None = None) -> None:
        self.settings = settings
        self.margin: MarginProvider = FixedMarginProvider()
        self._quotes = quotes or {}
        self._orders: dict[str, Order] = {}
        self._gtts: dict[str, Gtt] = {}
        self._positions: dict[str, int] = {}
        self._ids = itertools.count(1)

    def set_quote(self, symbol: str, bid: float, ask: float) -> None:
        self._quotes[symbol] = (bid, ask)

    def _fill_price(self, txn: Txn, symbol: str, limit: float) -> float:
        bid, ask = self._quotes.get(symbol, (limit, limit))
        slip = self.settings.extra_slippage_pct
        if txn == "BUY":
            return ask * (1.0 + slip)
        return bid * (1.0 - slip)

    def place_order(
        self,
        *,
        tradingsymbol: str,
        transaction_type: Txn,
        quantity: int,
        price: float,
        tag: str = "",
    ) -> Order:
        oid = f"P{next(self._ids)}"
        fill = self._fill_price(transaction_type, tradingsymbol, price)
        signed = quantity if transaction_type == "BUY" else -quantity
        self._positions[tradingsymbol] = self._positions.get(tradingsymbol, 0) + signed
        order = Order(
            order_id=oid,
            tradingsymbol=tradingsymbol,
            transaction_type=transaction_type,
            quantity=quantity,
            price=price,
            status="COMPLETE",
            filled_quantity=quantity,
            pending_quantity=0,
            average_price=fill,
            tag=tag,
        )
        self._orders[oid] = order
        return order

    def modify_order(self, order_id: str, *, price: float | None = None) -> Order:
        order = self._orders[order_id]
        if order.status == "COMPLETE":
            return order
        if price is not None:
            order.price = price
        return order

    def cancel_order(self, order_id: str) -> Order:
        order = self._orders[order_id]
        if order.status != "COMPLETE":
            order.status = "CANCELLED"
            order.pending_quantity = 0
        return order

    def orders(self) -> list[Order]:
        return list(self._orders.values())

    def positions(self) -> list[dict[str, Any]]:
        out = []
        for symbol, qty in self._positions.items():
            if qty == 0:
                continue
            out.append({"tradingsymbol": symbol, "quantity": qty, "exchange": "MCX", "product": "NRML"})
        return out

    def trades(self) -> list[dict[str, Any]]:
        return [
            {
                "order_id": o.order_id,
                "tradingsymbol": o.tradingsymbol,
                "quantity": o.filled_quantity,
                "average_price": o.average_price,
                "transaction_type": o.transaction_type,
            }
            for o in self._orders.values()
            if o.filled_quantity
        ]

    def place_gtt(
        self,
        *,
        tradingsymbol: str,
        last_price: float,
        quantity: int,
        target_price: float,
        stop_price: float,
    ) -> Gtt:
        del last_price
        gid = f"G{next(self._ids)}"
        gtt = Gtt(
            gtt_id=gid,
            tradingsymbol=tradingsymbol,
            status="ACTIVE",
            trigger_values=[stop_price, target_price],
            orders=[
                {"transaction_type": "BUY", "quantity": quantity, "price": stop_price},
                {"transaction_type": "BUY", "quantity": quantity, "price": target_price},
            ],
        )
        self._gtts[gid] = gtt
        return gtt

    def get_gtts(self) -> list[Gtt]:
        return list(self._gtts.values())

    def delete_gtt(self, gtt_id: str) -> None:
        gtt = self._gtts.get(gtt_id)
        if gtt:
            gtt.status = "CANCELLED"

    def ltp(self, tradingsymbol: str) -> float | None:
        pair = self._quotes.get(tradingsymbol)
        if not pair:
            return None
        return (pair[0] + pair[1]) / 2.0


class ZerodhaBroker:
    """Real Kite execution. place_order/GTT raise unless LIVE is enabled AND armed."""

    def __init__(self, settings: Settings, *, armed: bool = False, kite: Any | None = None) -> None:
        self.settings = settings
        self._armed = armed
        if kite is not None:
            self._kite = kite
        else:
            from crude_research.auth.token import require_access_token
            from kiteconnect import KiteConnect

            if not settings.kite_api_key:
                raise LiveTradingDisabledError("KITE_API_KEY missing")
            self._kite = KiteConnect(api_key=settings.kite_api_key)
            self._kite.set_access_token(require_access_token(settings))
        self.margin: MarginProvider = KiteMarginProvider(self._kite)

    def arm(self, value: bool) -> None:
        self._armed = value

    def _require_live(self) -> None:
        if not self.settings.live_trading_enabled or not self._armed:
            raise LiveTradingDisabledError("LIVE_DISARMED")

    def place_order(
        self,
        *,
        tradingsymbol: str,
        transaction_type: Txn,
        quantity: int,
        price: float,
        tag: str = "",
    ) -> Order:
        self._require_live()
        oid = str(
            self._kite.place_order(
                variety="regular",
                exchange="MCX",
                tradingsymbol=tradingsymbol,
                transaction_type=transaction_type,
                quantity=quantity,
                product="NRML",
                order_type="LIMIT",
                price=price,
                tag=tag[:20] if tag else None,
            )
        )
        log.info("place_order accepted order_id=%s symbol=%s txn=%s", oid, tradingsymbol, transaction_type)
        return self._order_by_id(oid)

    def modify_order(self, order_id: str, *, price: float | None = None) -> Order:
        self._require_live()
        kwargs: dict[str, Any] = {"variety": "regular", "order_id": order_id}
        if price is not None:
            kwargs["price"] = price
        self._kite.modify_order(**kwargs)
        return self._order_by_id(order_id)

    def cancel_order(self, order_id: str) -> Order:
        self._require_live()
        self._kite.cancel_order(variety="regular", order_id=order_id)
        return self._order_by_id(order_id)

    def orders(self) -> list[Order]:
        return [self._parse_order(item) for item in self._kite.orders()]

    def positions(self) -> list[dict[str, Any]]:
        raw = self._kite.positions()
        net = raw.get("net", raw) if isinstance(raw, dict) else raw
        return list(net)

    def trades(self) -> list[dict[str, Any]]:
        return list(self._kite.trades())

    def place_gtt(
        self,
        *,
        tradingsymbol: str,
        last_price: float,
        quantity: int,
        target_price: float,
        stop_price: float,
    ) -> Gtt:
        self._require_live()
        payload = self._kite.place_gtt(
            trigger_type="two-leg",
            tradingsymbol=tradingsymbol,
            exchange="MCX",
            trigger_values=[stop_price, target_price],
            last_price=last_price,
            orders=[
                {
                    "transaction_type": "BUY",
                    "quantity": quantity,
                    "order_type": "LIMIT",
                    "product": "NRML",
                    "price": stop_price,
                },
                {
                    "transaction_type": "BUY",
                    "quantity": quantity,
                    "order_type": "LIMIT",
                    "product": "NRML",
                    "price": target_price,
                },
            ],
        )
        gid = str(payload.get("trigger_id") if isinstance(payload, dict) else payload)
        log.info("place_gtt accepted gtt_id=%s", gid)
        for item in self.get_gtts():
            if item.gtt_id == gid:
                return item
        return Gtt(gtt_id=gid, tradingsymbol=tradingsymbol, status="UNKNOWN", trigger_values=[], orders=[])

    def get_gtts(self) -> list[Gtt]:
        out: list[Gtt] = []
        for item in self._kite.get_gtts():
            out.append(
                Gtt(
                    gtt_id=str(item.get("id") or item.get("trigger_id")),
                    tradingsymbol=str(item.get("condition", {}).get("tradingsymbol") or item.get("tradingsymbol") or ""),
                    status=str(item.get("status") or "UNKNOWN"),
                    trigger_values=list(item.get("condition", {}).get("trigger_values") or []),
                    orders=list(item.get("orders") or []),
                )
            )
        return out

    def delete_gtt(self, gtt_id: str) -> None:
        self._require_live()
        self._kite.delete_gtt(gtt_id)

    def ltp(self, tradingsymbol: str) -> float | None:
        raw = self._kite.ltp([f"MCX:{tradingsymbol}"])
        row = raw.get(f"MCX:{tradingsymbol}") if isinstance(raw, dict) else None
        if isinstance(row, dict) and row.get("last_price"):
            return float(row["last_price"])
        return None

    def _order_by_id(self, order_id: str) -> Order:
        for item in self.orders():
            if item.order_id == order_id:
                return item
        return Order(
            order_id=order_id,
            tradingsymbol="",
            transaction_type="BUY",
            quantity=0,
            price=0.0,
            status="OPEN",
            pending_quantity=0,
        )

    def _parse_order(self, item: dict[str, Any]) -> Order:
        qty = int(item.get("quantity") or 0)
        filled = int(item.get("filled_quantity") or 0)
        txn_raw = str(item.get("transaction_type") or "BUY")
        txn: Txn = "SELL" if txn_raw.upper() == "SELL" else "BUY"
        avg = item.get("average_price")
        return Order(
            order_id=str(item.get("order_id")),
            tradingsymbol=str(item.get("tradingsymbol") or ""),
            transaction_type=txn,
            quantity=qty,
            price=float(item.get("price") or 0),
            status=str(item.get("status") or "OPEN"),
            filled_quantity=filled,
            pending_quantity=int(item.get("pending_quantity") or max(qty - filled, 0)),
            average_price=float(avg) if avg else None,
            tag=str(item.get("tag") or ""),
        )


def utcnow() -> datetime:
    return datetime.now(tz=UTC)
