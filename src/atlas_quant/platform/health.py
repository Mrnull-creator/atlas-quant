
from dataclasses import dataclass


@dataclass(frozen=True)
class HealthStatus:
    """Represents the health and readiness of a service."""

    alive: bool
    ready: bool
    service: str

    def to_dict(self) -> dict[str, bool | str]:
        return {
            "alive": self.alive,
            "ready": self.ready,
            "service": self.service,
        }


def liveness_check(service: str) -> HealthStatus:
    """Report whether the service process is alive."""
    return HealthStatus(
        alive=True,
        ready=True,
        service=service,
    )


def readiness_check(
    service: str,
    *,
    dependencies_ready: bool = True,
) -> HealthStatus:
    """Report whether the service can accept requests."""
    return HealthStatus(
        alive=True,
        ready=dependencies_ready,
        service=service,
    )