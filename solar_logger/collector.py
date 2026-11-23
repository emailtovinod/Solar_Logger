"""Polling loop: read registers, decode, store, and record every attempt."""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from datetime import datetime, timezone

from .registers import RegisterMap
from .sources import Source
from .storage import Poll

log = logging.getLogger(__name__)


def poll_once(source: Source, regmap: RegisterMap, device: str) -> Poll:
    """Read all blocks once. A failed block leaves its signals out; nothing is guessed."""
    started = time.monotonic()
    now = datetime.now(timezone.utc)
    regs: dict[int, int] = {}
    errors: list[str] = []
    for start, count in regmap.blocks():
        try:
            words = source.read(start, count)
            if len(words) != count:
                raise ValueError(f"expected {count} registers, got {len(words)}")
            regs.update(zip(range(start, start + count), words))
        except Exception as exc:  # any driver error counts as a failed block
            errors.append(f"{start}+{count}: {type(exc).__name__}: {exc}")
            source.close()
            if not regs:
                break  # first block failed: the device is most likely unreachable
    return Poll(
        time=now,
        device=device,
        values=regmap.decode(regs),
        ok=not errors,
        duration_ms=int((time.monotonic() - started) * 1000),
        error="; ".join(errors) or None,
    )


class Collector:
    def __init__(self, source: Source, regmap: RegisterMap, storage, device: str, interval: float, buffer_size: int = 5000):
        self.source = source
        self.regmap = regmap
        self.storage = storage
        self.device = device
        self.interval = interval
        # Polls wait here while the database is unreachable; oldest are dropped when full.
        self.pending: deque[Poll] = deque(maxlen=buffer_size)
        self.stop = threading.Event()

    def step(self) -> Poll:
        poll = poll_once(self.source, self.regmap, self.device)
        if poll.ok:
            log.debug("poll ok: %d signals in %d ms", len(poll.values), poll.duration_ms)
        else:
            log.warning("poll failed (%d signals read): %s", len(poll.values), poll.error)
        if len(self.pending) == self.pending.maxlen:
            log.error("buffer full, dropping oldest poll from %s", self.pending[0].time.isoformat())
        self.pending.append(poll)
        self.flush()
        return poll

    def flush(self) -> None:
        batch = list(self.pending)
        try:
            self.storage.write(batch)
        except Exception as exc:
            log.error("database write failed, %d poll(s) buffered: %s", len(batch), exc)
            return
        for _ in batch:
            self.pending.popleft()

    def run(self) -> None:
        log.info("collecting %d signals from %s every %ss", len(self.regmap.signals), self.device, self.interval)
        next_at = time.monotonic()
        while not self.stop.is_set():
            self.step()
            next_at += self.interval
            delay = next_at - time.monotonic()
            if delay < 0:  # poll overran the interval; don't try to catch up
                next_at = time.monotonic()
                delay = 0
            self.stop.wait(delay)
        self.flush()
        self.source.close()
