"""Exact rounding that matches PostgreSQL's ROUND(numeric, n) (half away from zero).

CouchDB reduces sums of integers (counts, cents, seconds); the ratios that Part 2 rounds in
SQL are computed here from those integers, so results agree with PostgreSQL digit for digit.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, localcontext


def ratio(numerator: int, denominator: int, places: int) -> Decimal | None:
    """ROUND(numerator / denominator, places); None when the denominator is 0 (NULLIF)."""
    if not denominator:
        return None
    with localcontext() as context:
        context.prec = 50
        value = Decimal(int(numerator)) / Decimal(int(denominator))
        return value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def percent(part: int, whole: int, places: int = 2) -> Decimal | None:
    return ratio(100 * part, whole, places)


def money(cents: int) -> Decimal:
    return Decimal(int(cents)).scaleb(-2)


def as_float(value: Decimal | None) -> float | None:
    return None if value is None else float(value)
