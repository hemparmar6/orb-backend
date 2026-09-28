"""Token-bucket rate limiter for inbound WS messages.

One bucket per WebSocket connection. Refills continuously at ``rate`` tokens
per second up to ``capacity``. Each accepted inbound message spends 1 token.
When the bucket is empty, ``consume()`` returns False so the caller can close
the connection with policy-violation (1008).
"""
from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass(slots=True)
class TokenBucket:
    capacity: float
    rate: float  # tokens per second
    _tokens: float = 0.0
    _last_refill: float = 0.0

    def __post_init__(self) -> None:
        self._tokens = float(self.capacity)
        self._last_refill = time.monotonic()

    def consume(self, cost: float = 1.0) -> bool:
        now = time.monotonic()
        elapsed = now - self._last_refill
        if elapsed > 0:
            self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
            self._last_refill = now
        if self._tokens >= cost:
            self._tokens -= cost
            return True
        return False


def default_bucket() -> TokenBucket:
    """60 messages/minute burst, 1 msg/sec sustained — safe for a UI client."""
    return TokenBucket(capacity=60.0, rate=1.0)
