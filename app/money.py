"""Money helpers. All amounts are integer cents."""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from fractions import Fraction
from typing import Hashable, Mapping, TypeVar

K = TypeVar("K", bound=Hashable)


def to_cents(value: object) -> int:
    """Convert 1.29 / "1,29" / "1.29 €" / 129 (int is treated as euros!) to cents."""
    if value is None or value == "":
        raise ValueError("empty amount")
    if isinstance(value, bool):
        raise ValueError("invalid amount")
    if isinstance(value, (int, float, Decimal)):
        dec = Decimal(str(value))
    else:
        text = str(value).strip().replace("€", "").replace("EUR", "").replace(" ", "")
        if "," in text and "." in text:
            # 1.234,56 -> 1234.56
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", ".")
        try:
            dec = Decimal(text)
        except InvalidOperation as exc:
            raise ValueError(f"invalid amount: {value!r}") from exc
    return int((dec * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def fmt_eur(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    c = abs(cents)
    return f"{sign}{c // 100},{c % 100:02d} €"


def allocate(total: int, weights: Mapping[K, int | float | Fraction]) -> dict[K, int]:
    """Split ``total`` cents proportionally to ``weights`` (largest remainder method).

    The result always sums exactly to ``total``. Ties are broken deterministically by
    the string form of the key. Negative totals are split symmetrically.
    """
    items = [(k, Fraction(w)) for k, w in weights.items() if Fraction(w) > 0]
    if not items:
        raise ValueError("allocate() needs at least one positive weight")
    sign = -1 if total < 0 else 1
    amount = abs(total)
    wsum = sum(w for _, w in items)
    result: dict[K, int] = {}
    remainders: list[tuple[Fraction, str, K]] = []
    assigned = 0
    for key, w in items:
        exact = amount * w / wsum
        base = exact.numerator // exact.denominator
        result[key] = base
        assigned += base
        remainders.append((exact - base, str(key), key))
    leftover = amount - assigned
    remainders.sort(key=lambda r: (-r[0], r[1]))
    for i in range(leftover):
        result[remainders[i][2]] += 1
    return {k: sign * v for k, v in result.items()}
