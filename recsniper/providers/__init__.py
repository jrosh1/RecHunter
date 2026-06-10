"""Provider factory for Recreation.gov availability checking."""

from .base import BaseProvider
from .campground import CampgroundProvider
from .permit import PermitProvider
from .timed_entry import TimedEntryProvider
from recsniper.models import ReservationType


def get_provider(reservation_type: ReservationType) -> BaseProvider:
    """Factory function to get the right provider for a reservation type.

    Args:
        reservation_type: The type of reservation to check availability for.

    Returns:
        A provider instance capable of checking availability for the given type.

    Raises:
        KeyError: If the reservation type is not supported.
    """
    providers: dict[ReservationType, BaseProvider] = {
        ReservationType.CAMPGROUND: CampgroundProvider(),
        ReservationType.PERMIT: PermitProvider(),
        ReservationType.TIMED_ENTRY: TimedEntryProvider(),
    }
    try:
        return providers[reservation_type]
    except KeyError:
        raise KeyError(
            f"No provider registered for reservation type: {reservation_type!r}. "
            f"Supported types: {', '.join(t.value for t in providers)}"
        )


__all__ = [
    "BaseProvider",
    "CampgroundProvider",
    "PermitProvider",
    "TimedEntryProvider",
    "get_provider",
]
