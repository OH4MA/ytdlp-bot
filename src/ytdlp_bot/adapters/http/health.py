"""Private liveness and readiness handlers."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ReadinessState:
    """Aggregate readiness without secrets or job details."""

    configuration: bool = False
    database: bool = False
    migrations: bool = False
    recovery: bool = False
    http: bool = False
    dispatcher: bool = False
    platforms: bool = False
    egress: bool = False
    storage: bool = False
    admission_open: bool = True

    def is_ready(self) -> bool:
        return all(
            (
                self.configuration,
                self.database,
                self.migrations,
                self.recovery,
                self.http,
                self.dispatcher,
                self.platforms,
                self.egress,
                self.storage,
                self.admission_open,
            )
        )

    def public_view(self) -> dict[str, object]:
        return {
            "ready": self.is_ready(),
            "admission_open": self.admission_open,
            "dependencies": {
                "configuration": self.configuration,
                "database": self.database,
                "migrations": self.migrations,
                "recovery": self.recovery,
                "http": self.http,
                "dispatcher": self.dispatcher,
                "platforms": self.platforms,
                "egress": self.egress,
                "storage": self.storage,
            },
        }


@dataclass
class HealthController:
    readiness: ReadinessState = field(default_factory=ReadinessState)

    def live(self) -> dict[str, str]:
        return {"status": "live"}

    def ready(self) -> tuple[int, dict[str, object]]:
        view = self.readiness.public_view()
        return (200 if view["ready"] else 503, view)

    def close_admission(self) -> None:
        self.readiness.admission_open = False
