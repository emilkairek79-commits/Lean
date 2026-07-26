"""Pine Script `na` (not-available) value handling.

Pine represents "no value yet" with the special constant `na`. We model it
with a dedicated sentinel rather than Python's `None` or `float('nan')` so
that booleans and strings can also be "na" without ambiguity, and so `na`
compares equal to itself (unlike NaN) which keeps `x == na` checks working
the way Pine authors expect when they write `myVar == na`.
"""

from __future__ import annotations


class _NA:
    __slots__ = ()

    def __repr__(self) -> str:
        return "na"

    def __bool__(self) -> bool:
        # In Pine, `na` used in a boolean context (e.g. an `if` condition)
        # is falsy.
        return False

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _NA)

    def __hash__(self) -> int:
        return hash("__pine_na__")


NA = _NA()


def is_na(value: object) -> bool:
    if value is NA:
        return True
    if isinstance(value, float) and value != value:  # NaN
        return True
    return False


def to_number(value: object) -> float:
    """Coerce a Pine value to a float, mapping na to NaN for arithmetic."""
    if is_na(value):
        return float("nan")
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    return float(value)


def na_safe(value: object, default: float = 0.0) -> float:
    """Like to_number but maps na/NaN to a fallback instead of NaN."""
    n = to_number(value)
    return default if n != n else n
