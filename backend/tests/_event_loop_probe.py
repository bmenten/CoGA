"""Tell whether work runs off the event loop (OPS-9).

CPU work a coroutine runs on the event loop holds every other request until it ends. Two
probes show that it runs in a worker thread instead:

* ``LoopProbe.wrap`` makes a function's first call wait until a coroutine on the event loop
  has run while it runs (``LoopProbe.run``). That coroutine can run only when the function
  runs in a worker thread: on the event loop the call holds the loop, its wait times out,
  and the probe records that the loop was held (``held``). Deterministic, whatever the
  machine's speed.
* ``longest_stall`` runs an awaitable beside a coroutine that wakes every millisecond, and
  returns the longest time that coroutine waited to be served.

Import as ``from backend.tests._event_loop_probe import LoopProbe, longest_stall``.
"""

from __future__ import annotations

import asyncio
import functools
import gc
import threading
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

T = TypeVar("T")


class LoopProbe:
    """Whether a function's call held the event loop (see the module's docstring)."""

    def __init__(self, timeout: float = 5.0) -> None:
        self.timeout = timeout
        self.started = threading.Event()
        self.loop_ran = threading.Event()
        self.held = False

    def wrap(self, function: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(function)
        def waits_for_the_loop(*args: Any, **kwargs: Any) -> T:
            if not self.started.is_set():
                self.started.set()
                if not self.loop_ran.wait(self.timeout):
                    self.held = True
            return function(*args, **kwargs)

        return waits_for_the_loop

    async def _meanwhile(self) -> None:
        while not self.loop_ran.is_set():
            if self.started.is_set():
                self.loop_ran.set()
            await asyncio.sleep(0.001)

    async def run(self, work: Awaitable[T]) -> T:
        """Await ``work`` while another coroutine on the loop answers the wrapped call."""
        other = asyncio.create_task(self._meanwhile())
        try:
            return await work
        finally:
            other.cancel()


async def longest_stall(work: Awaitable[T]) -> tuple[T, float, float]:
    """``work``'s result, the longest time a coroutine waking every millisecond waited
    meanwhile, and how long ``work`` took, in seconds.

    The garbage collector is held off meanwhile: a collection stops every thread, on the
    loop or not, and its length depends on everything the test process holds.
    """
    stop = asyncio.Event()
    gaps: list[float] = []

    async def heartbeat() -> None:
        last = time.perf_counter()
        while not stop.is_set():
            await asyncio.sleep(0.001)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now

    gc.collect()
    gc.disable()
    beat = asyncio.create_task(heartbeat())
    try:
        await asyncio.sleep(0.005)
        start = time.perf_counter()
        result = await work
        duration = time.perf_counter() - start
    finally:
        stop.set()
        await beat
        gc.enable()
    return result, max(gaps, default=0.0), duration
