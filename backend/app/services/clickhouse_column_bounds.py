"""What a column of the ClickHouse variant and interval tables can hold (SAFE-30).

The loaders fill these columns from file values, and the ClickHouse client refuses a whole
insert when one value falls outside its column: a read depth of 120,000 in a ``UInt16``, a
negative phase set in a ``UInt64``, a QUAL of 1e39 in a ``Float32``. That failed the upload
or the import with a 500, after the rows it replaced were deleted. So each value is bound
to its column before the write (``clickhouse_variant_rows``, ``clickhouse_interval_tracks``):
a value the column cannot hold, or that is not a finite number, is not a valid value and is
stored as missing. A record that cannot stand without the value, its position, is left out
as malformed by the loader that reads it, which counts it as it counts any other malformed
record.

The bounds are the columns' types: the depth columns (``calls.dp``, ``calls.ad``) are
``UInt32``, wide enough for a mitochondrial or amplicon call's real depth (see
``ensure_clickhouse_variant_tables``).
"""

from __future__ import annotations

import math
from typing import Any

UINT16: tuple[int, int] = (0, 2**16 - 1)
UINT32: tuple[int, int] = (0, 2**32 - 1)
UINT64: tuple[int, int] = (0, 2**64 - 1)
INT32: tuple[int, int] = (-(2**31), 2**31 - 1)
# The largest finite Float32 (numpy.finfo(numpy.float32).max).
FLOAT32_MAX = 3.4028234663852886e38


def column_int(value: Any, bounds: tuple[int, int]) -> int | None:
    """``value`` as the whole number an integer column of ``bounds`` stores, or None when it
    is missing, is not a finite number or falls outside the column.

    A fraction is cut to its whole part and text is read as a number, as the loaders'
    parsers read a value (``int(float(value))``): ``"12.7"`` is 12, ``"inf"`` and ``"."``
    are None."""
    if value is None:
        return None
    number: int
    if isinstance(value, int):
        number = value
    else:
        try:
            number = int(value)
        except (TypeError, ValueError, OverflowError):
            try:
                as_float = float(value)
            except (TypeError, ValueError, OverflowError):
                return None
            if not math.isfinite(as_float):
                return None
            number = int(as_float)
    low, high = bounds
    return number if low <= number <= high else None


def column_float32(value: Any) -> float | None:
    """``value`` as a ``Float32`` column stores it, or None when it is missing, is not a
    finite number or lies beyond the largest ``Float32``."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number) or abs(number) > FLOAT32_MAX:
        return None
    return number
