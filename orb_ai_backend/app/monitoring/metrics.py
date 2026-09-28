"""In-process metrics collector.

Zero external dependencies — no Prometheus client needed. Metrics are
tracked in memory (bounded ring buffers + counters) and exposed through
the ``/api/v1/monitoring/metrics`` endpoint consumed by the Admin
Dashboard.

Thread-safe under CPython's GIL because we only mutate primitives and
short lists. For production Prometheus/Grafana integration, point a
scraper at ``/api/v1/monitoring/metrics/prometheus`` (text format).
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import RLock
from typing import Any, Deque


_MAX_LATENCIES_PER_ENDPOINT = 500  # last N request latencies retained


class MetricsCollector:
    """Bounded in-memory counters + rolling latency samples."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._started_at = time.time()
        self.total_requests: int = 0
        self.total_errors: int = 0
        # Status-code histogram: {"2xx": n, "3xx": n, "4xx": n, "5xx": n}
        self.status_buckets: dict[str, int] = defaultdict(int)
        # Per-endpoint counters
        self.by_endpoint: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"count": 0, "errors": 0, "latencies_ms": deque(maxlen=_MAX_LATENCIES_PER_ENDPOINT)}
        )
        # Rate-limit events
        self.rate_limit_hits: int = 0
        # Auth-related counters
        self.auth_success: int = 0
        self.auth_failures: int = 0
        self.market_data_counters: dict[str, int] = {
            "broker_ticks_received": 0,
            "quote_ticks_forwarded": 0,
            "quote_ticks_sent": 0,
            "quote_ticks_dropped": 0,
        }

    # ------------------------------------------------------------------ record

    def record_request(self, path: str, method: str, status_code: int, latency_ms: float) -> None:
        key = f"{method} {_normalise_path(path)}"
        bucket = f"{status_code // 100}xx"
        with self._lock:
            self.total_requests += 1
            self.status_buckets[bucket] += 1
            if status_code >= 500:
                self.total_errors += 1
            endpoint = self.by_endpoint[key]
            endpoint["count"] += 1
            if status_code >= 500:
                endpoint["errors"] += 1
            endpoint["latencies_ms"].append(latency_ms)

    def record_rate_limit(self) -> None:
        with self._lock:
            self.rate_limit_hits += 1

    def record_auth(self, success: bool) -> None:
        with self._lock:
            if success:
                self.auth_success += 1
            else:
                self.auth_failures += 1

    def record_market_data(self, name: str, amount: int = 1) -> None:
        if name not in self.market_data_counters or amount <= 0:
            return
        with self._lock:
            self.market_data_counters[name] += amount

    # ------------------------------------------------------------------ read

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            uptime_s = round(time.time() - self._started_at, 1)
            per_endpoint = []
            for name, data in self.by_endpoint.items():
                lats: Deque[float] = data["latencies_ms"]
                if lats:
                    ordered = sorted(lats)
                    p50 = _percentile(ordered, 50)
                    p95 = _percentile(ordered, 95)
                    p99 = _percentile(ordered, 99)
                    avg = round(sum(ordered) / len(ordered), 2)
                else:
                    p50 = p95 = p99 = avg = 0.0
                per_endpoint.append(
                    {
                        "endpoint": name,
                        "count": data["count"],
                        "errors": data["errors"],
                        "latency_ms": {
                            "avg": avg,
                            "p50": p50,
                            "p95": p95,
                            "p99": p99,
                        },
                    }
                )
            per_endpoint.sort(key=lambda x: -x["count"])
            return {
                "uptime_s": uptime_s,
                "total_requests": self.total_requests,
                "total_errors": self.total_errors,
                "error_rate": round(self.total_errors / self.total_requests, 4) if self.total_requests else 0.0,
                "rate_limit_hits": self.rate_limit_hits,
                "auth_success": self.auth_success,
                "auth_failures": self.auth_failures,
                **self.market_data_counters,
                "status_buckets": dict(self.status_buckets),
                "endpoints": per_endpoint,
            }

    def prometheus(self) -> str:
        """Emit metrics in Prometheus text exposition format."""
        snap = self.snapshot()
        lines = [
            "# HELP orb_ai_requests_total Total HTTP requests processed",
            "# TYPE orb_ai_requests_total counter",
            f'orb_ai_requests_total {snap["total_requests"]}',
            "# HELP orb_ai_errors_total Total 5xx responses",
            "# TYPE orb_ai_errors_total counter",
            f'orb_ai_errors_total {snap["total_errors"]}',
            "# HELP orb_ai_rate_limit_hits_total Rate-limited requests",
            "# TYPE orb_ai_rate_limit_hits_total counter",
            f'orb_ai_rate_limit_hits_total {snap["rate_limit_hits"]}',
            "# HELP orb_ai_auth_success_total Successful auth attempts",
            "# TYPE orb_ai_auth_success_total counter",
            f'orb_ai_auth_success_total {snap["auth_success"]}',
            "# HELP orb_ai_auth_failures_total Failed auth attempts",
            "# TYPE orb_ai_auth_failures_total counter",
            f'orb_ai_auth_failures_total {snap["auth_failures"]}',
            "# HELP orb_ai_uptime_seconds Process uptime seconds",
            "# TYPE orb_ai_uptime_seconds gauge",
            f'orb_ai_uptime_seconds {snap["uptime_s"]}',
        ]
        for name in self.market_data_counters:
            metric_name = f"orb_ai_{name}_total"
            lines.extend((f"# HELP {metric_name} {name.replace('_', ' ')}", f"# TYPE {metric_name} counter", f"{metric_name} {snap[name]}"))
        for bucket, count in snap["status_buckets"].items():
            lines.append(f'orb_ai_status_bucket_total{{bucket="{bucket}"}} {count}')
        return "\n".join(lines) + "\n"

    def reset(self) -> None:
        with self._lock:
            self.__init__()  # type: ignore[misc]


def _percentile(sorted_values: list[float], p: int) -> float:
    if not sorted_values:
        return 0.0
    k = max(0, min(len(sorted_values) - 1, int(round((p / 100.0) * (len(sorted_values) - 1)))))
    return round(sorted_values[k], 2)


def _normalise_path(path: str) -> str:
    """Group similar paths so id/uuid segments don't blow up the endpoint map."""
    parts = []
    for seg in path.strip("/").split("/"):
        # Replace uuid-ish and numeric segments with a placeholder
        if len(seg) >= 8 and "-" in seg:
            parts.append(":id")
        elif seg.isdigit():
            parts.append(":id")
        else:
            parts.append(seg)
    return "/" + "/".join(parts)


# Process-scoped singleton
metrics = MetricsCollector()
