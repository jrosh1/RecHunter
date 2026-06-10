"""Abstract base class for all Recreation.gov availability providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, timedelta

from recsniper.models import AvailabilitySlot, Watch


class BaseProvider(ABC):
    """Base class that all availability providers must implement.

    Providers are stateless — they hold no per-request instance data.
    Each call to ``check_availability`` is self-contained and relies only
    on the ``Watch`` parameter for query context.
    """

    # ------------------------------------------------------------------
    # Abstract interface
    # ------------------------------------------------------------------

    @abstractmethod
    async def check_availability(self, watch: Watch) -> list[AvailabilitySlot]:
        """Check Recreation.gov for available slots matching *watch*.

        Implementations must:
        * Respect the rate limiter (``await rate_limiter.acquire()``)
        * Log requests/responses at DEBUG level via loguru
        * Handle HTTP and parsing errors gracefully (return ``[]``)

        Args:
            watch: The watch definition describing what to check.

        Returns:
            A list of ``AvailabilitySlot`` objects for every opening found.
        """
        ...

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def build_deep_link(self, watch: Watch) -> str:
        """Build a direct link to the facility on Recreation.gov.

        Subclasses should override this to produce a type-specific URL.
        The default implementation links to the generic facility page.

        Args:
            watch: The watch definition.

        Returns:
            A URL string pointing to the facility.
        """
        return f"https://www.recreation.gov/camping/campgrounds/{watch.facility_id}"

    def _filter_by_date_range(
        self,
        slots: list[AvailabilitySlot],
        watch: Watch,
    ) -> list[AvailabilitySlot]:
        """Filter *slots* to only those within the watch's date range.

        If ``watch.date_end`` is ``None`` the filter uses only
        ``watch.date_start`` as the lower bound (no upper bound).

        Args:
            slots: Unfiltered availability slots.
            watch: The watch whose ``date_start`` / ``date_end`` define
                   the acceptable window.

        Returns:
            A new list containing only the slots that fall within range.
        """
        start = watch.date_start
        end = watch.date_end  # may be None

        filtered: list[AvailabilitySlot] = []
        for slot in slots:
            if slot.date < start:
                continue
            if end is not None and slot.date > end:
                continue
            filtered.append(slot)
        return filtered

    @staticmethod
    def _iter_dates(start: date, end: date | None) -> list[date]:
        """Return an inclusive list of dates from *start* to *end*.

        If *end* is ``None``, returns a single-element list ``[start]``.
        """
        if end is None:
            return [start]
        days: list[date] = []
        current = start
        while current <= end:
            days.append(current)
            current += timedelta(days=1)
        return days

    @staticmethod
    def _months_in_range(start: date, end: date | None) -> list[date]:
        """Return the first-of-month dates covering *start* … *end*.

        Recreation.gov availability APIs typically accept queries anchored
        to the first of the month.  This helper produces the minimal set
        of month-start dates that covers the requested range.

        If *end* is ``None``, only the month containing *start* is returned.
        """
        if end is None:
            return [start.replace(day=1)]

        months: list[date] = []
        current = start.replace(day=1)
        last = end.replace(day=1)
        while current <= last:
            months.append(current)
            # Advance to the first of the next month
            if current.month == 12:
                current = current.replace(year=current.year + 1, month=1)
            else:
                current = current.replace(month=current.month + 1)
        return months
