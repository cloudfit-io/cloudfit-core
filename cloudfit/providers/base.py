"""Abstract base class for cloudfit provider plugins."""

from abc import ABC, abstractmethod
from ..models import MachineType


class Provider(ABC):
    """Interface that all cloudfit provider packages must implement.

    Availability is not part of this contract: the scoring engine derives it
    from ``MachineType.status`` (active / deprecated / tombstoned), which the
    provider sets when it builds each ``MachineType``. A provider may still
    expose a helper to look up live deprecation state, but the engine does not
    call one.
    """

    @abstractmethod
    def fetch_instances(self, region: str) -> list[MachineType]:
        """Fetch all available machine types for a region."""
        ...

    @abstractmethod
    def get_pricing(self, instance_id: str, region: str) -> float:
        """Return on-demand price per hour for an instance in a region."""
        ...
