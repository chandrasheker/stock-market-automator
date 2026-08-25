"""Contract discovery and option-to-futures mapping.

MCX CRUDEOIL / CRUDEOILM options are options on the corresponding month's futures.
Official MCX specs: options last trading day is two business days prior to the
underlying futures last trading day. We therefore do **not** map by equal expiry
dates.

Proven mapping used here: unique contract-month token parsed from Zerodha
tradingsymbols (e.g. `25OCT` in `CRUDEOILM25OCT8000CE` and `CRUDEOILM25OCTFUT`).
If that mapping is not unique and internally consistent, this module raises
`AmbiguousFutureMappingError` instead of guessing.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import date

from crude_research.exceptions import AmbiguousFutureMappingError, InstrumentMasterError
from crude_research.market.models import Instrument, Underlying

log = logging.getLogger(__name__)


def parse_underlying(value: str) -> Underlying:
    try:
        return Underlying(value.strip().upper())
    except ValueError as exc:
        raise InstrumentMasterError(
            f"Unsupported underlying {value!r}. Expected CRUDEOIL or CRUDEOILM."
        ) from exc


def _same_underlying(instrument: Instrument, underlying: Underlying) -> bool:
    return instrument.underlying == underlying


def list_futures(master: Sequence[Instrument], underlying: str) -> list[Instrument]:
    target = parse_underlying(underlying)
    futures = [
        inst
        for inst in master
        if inst.exchange == "MCX" and inst.is_future and _same_underlying(inst, target)
    ]
    return sorted(futures, key=lambda inst: (inst.expiry or date.max, inst.tradingsymbol))


def list_option_expiries(master: Sequence[Instrument], underlying: str) -> list[date]:
    target = parse_underlying(underlying)
    expiries = {
        inst.expiry
        for inst in master
        if inst.exchange == "MCX"
        and inst.is_option
        and inst.expiry is not None
        and _same_underlying(inst, target)
    }
    return sorted(expiries)


def list_options(
    master: Sequence[Instrument],
    underlying: str,
    expiry: date,
) -> list[Instrument]:
    target = parse_underlying(underlying)
    options = [
        inst
        for inst in master
        if inst.exchange == "MCX"
        and inst.is_option
        and inst.expiry == expiry
        and _same_underlying(inst, target)
    ]
    return sorted(
        options,
        key=lambda inst: (inst.strike, 0 if inst.is_call else 1, inst.tradingsymbol),
    )


def _candidate_dump(instruments: Sequence[Instrument]) -> list[dict[str, str | int | None]]:
    return [
        {
            "tradingsymbol": inst.tradingsymbol,
            "instrument_token": inst.instrument_token,
            "expiry": inst.expiry.isoformat() if inst.expiry else None,
            "contract_month": inst.contract_month,
            "instrument_type": inst.instrument_type,
        }
        for inst in instruments
    ]


def resolve_underlying_future(
    master: Sequence[Instrument],
    *,
    underlying: str,
    option_expiry: date,
) -> Instrument:
    """Map an option expiry to its futures contract using contract-month metadata.

    Rules (all must hold):
    1. Restrict to the requested underlying (CRUDEOIL vs CRUDEOILM never mix).
    2. Collect options with this expiry; they must share one parseable contract month.
    3. There must be exactly one future with that same contract month.
    4. If the future has an expiry date, it must be on or after the option expiry
       (MCX: options expire two business days before the futures last trading day).

    Anything else is logged with every candidate and raised as an error.
    """
    target = parse_underlying(underlying)
    options = list_options(master, target.value, option_expiry)
    futures = list_futures(master, target.value)
    if not options:
        raise AmbiguousFutureMappingError(
            f"No {target} options found for expiry {option_expiry.isoformat()}",
            candidates=_candidate_dump(futures),
        )

    months = {opt.contract_month for opt in options}
    if None in months or len(months) != 1:
        log.error(
            "Cannot prove option contract month underlying=%s expiry=%s months=%s options=%s futures=%s",
            target,
            option_expiry,
            sorted(m or "<unparsed>" for m in months),
            _candidate_dump(options[:20]),
            _candidate_dump(futures),
        )
        raise AmbiguousFutureMappingError(
            f"Option contract month is missing or not unique for {target} {option_expiry.isoformat()}: "
            f"{sorted(m or '<unparsed>' for m in months)}. Refusing to guess the underlying future.",
            candidates=_candidate_dump(futures),
        )
    month = next(iter(months))
    assert month is not None

    matched = [fut for fut in futures if fut.contract_month == month]
    if len(matched) != 1:
        log.error(
            "Ambiguous futures for underlying=%s option_expiry=%s month=%s matches=%s all_futures=%s",
            target,
            option_expiry,
            month,
            _candidate_dump(matched),
            _candidate_dump(futures),
        )
        raise AmbiguousFutureMappingError(
            f"Expected exactly one {target} future for contract month {month}, found {len(matched)}. "
            "Refusing to guess.",
            candidates=_candidate_dump(matched or futures),
        )
    future = matched[0]
    if future.expiry is not None and future.expiry < option_expiry:
        log.error(
            "Mapped future expires before options underlying=%s option_expiry=%s future=%s",
            target,
            option_expiry,
            _candidate_dump([future]),
        )
        raise AmbiguousFutureMappingError(
            f"Mapped future {future.tradingsymbol} expires {future.expiry.isoformat()} "
            f"which is before option expiry {option_expiry.isoformat()}. Mapping rejected.",
            candidates=_candidate_dump([future]),
        )
    log.info(
        "Resolved %s options expiry=%s -> future %s (month=%s, future_expiry=%s) via %s",
        target,
        option_expiry,
        future.tradingsymbol,
        month,
        future.expiry,
        "CONTRACT_MONTH_FROM_TRADINGSYMBOL",
    )
    return future
