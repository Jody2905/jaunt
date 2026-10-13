"""Money is whole cents (int), never float. Prices are in US dollars, as most travel is."""

from decimal import ROUND_HALF_UP, Decimal


def fmt(cents):
    """123456 -> 'US$1,234.56'"""
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}US${cents // 100:,}.{cents % 100:02d}"


def percent_of(cents, percent):
    return int((Decimal(cents) * percent / 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
