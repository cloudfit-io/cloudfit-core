"""Abstract base class for cloudfit provider plugins."""

from abc import ABC, abstractmethod
from ..models import MachineType


class Provider(ABC):
    """Interface that all cloudfit provider packages must implement."""

    @abstractmethod
    def fetch_instances(self, region: str) -> list[MachineType]:
        """Fetch all available machine types for a region."""
        ...

    @abstractmethod
    def get_pricing(self, instance_id: str, region: str) -> float:
        """Return on-demand price per hour for an instance in a region."""
        ...

    @abstractmethod
    def get_availability(self, instance_id: str, region: str) -> float:
        """Return availability score 0.0–1.0 (1.0 = actively maintained)."""
        ...
