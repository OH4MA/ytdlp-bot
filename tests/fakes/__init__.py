"""Shared test doubles."""

from tests.fakes.leases import FakeArtifactLeaseRegistry
from tests.fakes.media import FakeMediaWorker
from tests.fakes.network import FakeDnsResolver, FakeUrlPreflightClient
from tests.fakes.platform import FakePlatformPort
from tests.fakes.repositories import (
    InMemoryAccessRepository,
    InMemoryAdminConfirmationRepository,
    InMemoryArtifactRepository,
    InMemoryCapacityRepository,
    InMemoryDeliveryAttemptRepository,
    InMemoryJobPayloadRepository,
    InMemoryJobRepository,
    InMemoryNotificationOutboxRepository,
    InMemorySettingsRepository,
)
from tests.fakes.storage import TemporaryArtifactStore
from tests.fakes.system import DeterministicIdGenerator, FakeClock

__all__ = [
    "DeterministicIdGenerator",
    "FakeArtifactLeaseRegistry",
    "FakeClock",
    "FakeDnsResolver",
    "FakeMediaWorker",
    "FakePlatformPort",
    "FakeUrlPreflightClient",
    "InMemoryAccessRepository",
    "InMemoryAdminConfirmationRepository",
    "InMemoryArtifactRepository",
    "InMemoryCapacityRepository",
    "InMemoryDeliveryAttemptRepository",
    "InMemoryJobPayloadRepository",
    "InMemoryJobRepository",
    "InMemoryNotificationOutboxRepository",
    "InMemorySettingsRepository",
    "TemporaryArtifactStore",
]
