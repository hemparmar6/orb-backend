"""Module 10 — Enterprise Monitoring.

Provides in-process health probes, request/error/latency metrics, and a
ring-buffer log store consumed by the standalone React Admin Dashboard.

Everything degrades gracefully — if Redis, the AI provider, or a broker
integration is unavailable, monitoring reports the outage without
preventing application startup.
"""
from app.monitoring.health_checks import (
    aggregate_health,
    check_ai_service,
    check_broker_connectivity,
    check_database,
    check_redis,
    check_scheduler,
    check_websockets,
)
from app.monitoring.log_buffer import LogBuffer, log_buffer
from app.monitoring.metrics import MetricsCollector, metrics

__all__ = [
    "aggregate_health",
    "check_ai_service",
    "check_broker_connectivity",
    "check_database",
    "check_redis",
    "check_scheduler",
    "check_websockets",
    "LogBuffer",
    "log_buffer",
    "MetricsCollector",
    "metrics",
]
