from dataclasses import dataclass

from atlas_quant.platform.config import get_settings


@dataclass(frozen=True)
class ServiceMetadata:
    """Describes the identity and environment of a service."""

    name: str
    version: str
    environment: str

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "version": self.version,
            "environment": self.environment,
        }


def get_service_metadata() -> ServiceMetadata:
    """Return metadata from the centralized application settings."""

    settings = get_settings()

    return ServiceMetadata(
        name=settings.service_name,
        version=settings.service_version,
        environment=settings.environment,
    )