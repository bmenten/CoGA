"""Fail a test that runs too long, instead of letting it hang the whole run.

For code whose way of failing is a loop that never ends, such as a gzip reader that keeps
feeding the same bytes to a finished decompressor. Import as
``from backend.tests._deadline import deadline``.
"""

from __future__ import annotations

import contextlib
import signal
from collections.abc import Iterator


@contextlib.contextmanager
def deadline(seconds: int) -> Iterator[None]:
    """Raise ``TimeoutError`` in the block if it still runs after ``seconds``.

    It uses SIGALRM, so it needs a POSIX system and the main thread, where pytest runs
    the tests. Without SIGALRM the block runs without a deadline.
    """
    if not hasattr(signal, "SIGALRM"):
        yield
        return

    def _expire(signum, frame):
        raise TimeoutError(f"still running after {seconds} s")

    previous = signal.signal(signal.SIGALRM, _expire)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)
