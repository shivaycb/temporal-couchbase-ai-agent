"""Utilities for handling decimal values in Python for Couchbase."""

from decimal import Decimal, ROUND_HALF_UP, getcontext
from typing import Union, Optional, Any
import json

# Set precision for financial calculations
getcontext().prec = 34  # High precision for financial calculations


class DecimalEncoder(json.JSONEncoder):
    """JSON encoder that handles Decimal types."""

    def default(self, obj):
        if isinstance(obj, Decimal):
            return str(obj)
        return super().default(obj)


def to_decimal(value: Union[float, int, str, Decimal]) -> Decimal:
    """
    Convert various numeric types to Python Decimal for Couchbase storage.

    Args:
        value: Numeric value to convert

    Returns:
        Decimal object with high precision
    """
    if isinstance(value, Decimal):
        return value

    if isinstance(value, (float, int)):
        # Convert to string first to avoid floating point precision issues
        return Decimal(str(value))
    elif isinstance(value, str):
        return Decimal(value)
    else:
        raise TypeError(f"Cannot convert {type(value)} to Decimal")


def from_decimal(value: Union[Decimal, Any]) -> Decimal:
    """
    Convert value to Python Decimal for calculations.

    Args:
        value: Decimal or other numeric value

    Returns:
        Python Decimal object
    """
    if isinstance(value, Decimal):
        return value
    elif isinstance(value, (float, int, str)):
        return Decimal(str(value))
    else:
        return Decimal(str(value))


def decimal_to_float(value: Union[Decimal, float]) -> float:
    """
    Convert Decimal to float for API responses.

    Warning: This may lose precision and should only be used for display purposes.

    Args:
        value: Decimal value

    Returns:
        Float representation
    """
    if isinstance(value, Decimal):
        return float(value)
    else:
        return float(value)


def decimal_to_string(value: Union[Decimal, float, int, str]) -> str:
    """
    Convert Decimal to string for Couchbase JSON storage with full precision.

    Args:
        value: Decimal value

    Returns:
        String representation preserving precision
    """
    if isinstance(value, Decimal):
        return str(value)
    elif isinstance(value, (float, int)):
        return str(Decimal(str(value)))
    else:
        return str(value)


def round_money(value: Union[Decimal, float], places: int = 2) -> Decimal:
    """
    Round monetary value to specified decimal places using banker's rounding.

    Args:
        value: Monetary value to round
        places: Number of decimal places (default: 2)

    Returns:
        Rounded Decimal value
    """
    decimal_value = from_decimal(value)
    quantize_value = Decimal(10) ** -places
    return decimal_value.quantize(quantize_value, rounding=ROUND_HALF_UP)


def add_money(*values: Union[Decimal, float, int, str]) -> Decimal:
    """
    Add multiple monetary values with proper precision.

    Args:
        *values: Monetary values to add

    Returns:
        Sum as Decimal
    """
    total = Decimal('0')
    for value in values:
        total += from_decimal(value)
    return total


def subtract_money(
    minuend: Union[Decimal, float, int, str],
    subtrahend: Union[Decimal, float, int, str]
) -> Decimal:
    """
    Subtract monetary values with proper precision.

    Args:
        minuend: Value to subtract from
        subtrahend: Value to subtract

    Returns:
        Difference as Decimal
    """
    result = from_decimal(minuend) - from_decimal(subtrahend)
    return result


def multiply_money(
    value: Union[Decimal, float, int, str],
    factor: Union[Decimal, float, int, str]
) -> Decimal:
    """
    Multiply monetary value by a factor.

    Args:
        value: Monetary value
        factor: Multiplication factor

    Returns:
        Product as Decimal
    """
    result = from_decimal(value) * Decimal(str(factor))
    return round_money(result)


def compare_money(
    value1: Union[Decimal, float, int, str],
    value2: Union[Decimal, float, int, str]
) -> int:
    """
    Compare two monetary values.

    Args:
        value1: First value
        value2: Second value

    Returns:
        -1 if value1 < value2, 0 if equal, 1 if value1 > value2
    """
    dec1 = from_decimal(value1)
    dec2 = from_decimal(value2)

    if dec1 < dec2:
        return -1
    elif dec1 > dec2:
        return 1
    else:
        return 0


def format_money(
    value: Union[Decimal, float, int, str],
    currency: str = "USD",
    include_symbol: bool = True
) -> str:
    """
    Format monetary value for display.

    Args:
        value: Monetary value
        currency: Currency code (default: USD)
        include_symbol: Include currency symbol

    Returns:
        Formatted string
    """
    decimal_value = round_money(from_decimal(value))

    # Format with thousands separator
    formatted = f"{decimal_value:,.2f}"

    if include_symbol:
        if currency == "USD":
            return f"${formatted}"
        elif currency == "EUR":
            return f"€{formatted}"
        elif currency == "GBP":
            return f"£{formatted}"
        else:
            return f"{formatted} {currency}"
    else:
        return formatted


def validate_positive_amount(value: Union[Decimal, float, int, str]) -> bool:
    """
    Validate that an amount is positive.

    Args:
        value: Amount to validate

    Returns:
        True if positive, False otherwise
    """
    return from_decimal(value) > 0


def validate_amount_range(
    value: Union[Decimal, float, int, str],
    min_value: Optional[Union[Decimal, float, int, str]] = None,
    max_value: Optional[Union[Decimal, float, int, str]] = None
) -> bool:
    """
    Validate that an amount is within a specified range.

    Args:
        value: Amount to validate
        min_value: Minimum allowed value (inclusive)
        max_value: Maximum allowed value (inclusive)

    Returns:
        True if within range, False otherwise
    """
    decimal_value = from_decimal(value)

    if min_value is not None and decimal_value < Decimal(str(min_value)):
        return False

    if max_value is not None and decimal_value > Decimal(str(max_value)):
        return False

    return True


# For backward compatibility - alias the new function names
to_decimal128 = to_decimal
from_decimal128 = from_decimal
