"""Money is integer fen everywhere (D-07); yuan only appears in display text."""
from __future__ import annotations


def format_yuan(fen: int) -> str:
    sign = "-" if fen < 0 else ""
    whole, cents = divmod(abs(fen), 100)
    return f"{sign}{whole:,}.{cents:02d} 元"
