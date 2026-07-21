"""Bounded-cardinality in-process metrics sink."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from threading import Lock

# Only these label keys are permitted.
_ALLOWED_LABEL_KEYS = frozenset(
    {
        "platform",
        "state",
        "outcome",
        "component",
        "phase",
        "plan",
        "error_code",
        "result",
    }
)

# Label values must match this pattern (no free-form host/path/id).
_SAFE_VALUE = frozenset(
    {
        # platforms
        "telegram",
        "discord",
        # generic
        "ok",
        "error",
        "true",
        "false",
        "direct_upload",
        "signed_link",
        "queued",
        "active",
        "completed",
        "failed",
        "cancelled",
        "ready",
        "not_ready",
        "storage",
        "http",
        "dispatcher",
        "cleanup",
        "worker",
        "platform",
        "database",
        "egress",
        "unknown",
    }
)


class MetricsError(ValueError):
    """Invalid metric label or name."""


@dataclass
class InMemoryMetricsSink:
    """Thread-safe counters and gauges with label allowlists."""

    counters: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    gauges: dict[str, float] = field(default_factory=dict)
    timings: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    _lock: Lock = field(default_factory=Lock)

    def reset(self) -> None:
        with self._lock:
            self.counters.clear()
            self.gauges.clear()
            self.timings.clear()

    def _key(self, name: str, tags: dict[str, str] | None) -> str:
        if not name or not name.replace("_", "").replace(".", "").isalnum():
            raise MetricsError("invalid metric name")
        if not tags:
            return name
        parts: list[str] = []
        for key in sorted(tags):
            if key not in _ALLOWED_LABEL_KEYS:
                raise MetricsError(f"label key not allowed: {key}")
            value = tags[key]
            # Bound cardinality: only short enum-like values.
            if len(value) > 32 or any(ch in value for ch in "/:@?&="):
                raise MetricsError(f"label value not allowed: {key}")
            # Prefer known set but allow stable error codes / states.
            if value not in _SAFE_VALUE and not value.replace("_", "").isalnum():
                raise MetricsError(f"label value not allowed: {key}")
            parts.append(f"{key}={value}")
        return name + "|" + ",".join(parts)

    def incr(self, name: str, *, tags: dict[str, str] | None = None, value: int = 1) -> None:
        key = self._key(name, tags)
        with self._lock:
            self.counters[key] += value

    def gauge(self, name: str, value: float, *, tags: dict[str, str] | None = None) -> None:
        key = self._key(name, tags)
        with self._lock:
            self.gauges[key] = value

    def timing(self, name: str, duration: timedelta, *, tags: dict[str, str] | None = None) -> None:
        key = self._key(name, tags)
        with self._lock:
            self.timings[key].append(duration.total_seconds())

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "counters": dict(self.counters),
                "gauges": dict(self.gauges),
                "timings": {k: list(v) for k, v in self.timings.items()},
            }
