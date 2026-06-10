"""Campground availability provider for Recreation.gov."""

from __future__ import annotations

from datetime import date, datetime, timezone

import httpx
from loguru import logger

from recsniper.models import AvailabilitySlot, Watch
from recsniper.utils import get_http_client, rate_limiter

from .base import BaseProvider

# Recreation.gov campground availability API
_BASE_URL = (
    "https://www.recreation.gov/api/camps/availability/campground"
)


class CampgroundProvider(BaseProvider):
    """Checks campsite availability via the Recreation.gov camps API.

    The API returns availability on a per-month basis, so the provider
    queries every month that overlaps the watch's date range and merges
    the results.
    """

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def check_availability(self, watch: Watch) -> list[AvailabilitySlot]:
        """Query campground availability for each overlapping month.

        Args:
            watch: The watch definition.

        Returns:
            A list of available slots, already filtered and optionally
            trimmed by consecutive-night / equipment filters.
        """
        months = self._months_in_range(watch.date_start, watch.date_end)
        all_slots: list[AvailabilitySlot] = []

        client = await get_http_client()

        for month_start in months:
            slots = await self._fetch_month(client, watch, month_start)
            all_slots.extend(slots)

        # Date-range filtering (the month query may include extra days)
        all_slots = self._filter_by_date_range(all_slots, watch)

        # Optional filters from watch.filters
        min_nights = watch.filters.get("min_consecutive_nights")
        if min_nights and isinstance(min_nights, int) and min_nights > 1:
            all_slots = self._filter_consecutive_nights(all_slots, min_nights)

        equipment = watch.filters.get("equipment")
        if equipment and isinstance(equipment, str):
            all_slots = self._filter_equipment(all_slots, equipment)

        logger.debug(
            "Campground {} — {} available slot(s) after filtering",
            watch.facility_id,
            len(all_slots),
        )
        return all_slots

    def build_deep_link(self, watch: Watch) -> str:
        return f"https://www.recreation.gov/camping/campgrounds/{watch.facility_id}"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _fetch_month(
        self,
        client: httpx.AsyncClient,
        watch: Watch,
        month_start: date,
    ) -> list[AvailabilitySlot]:
        """Fetch availability for a single calendar month.

        Args:
            client: Shared ``httpx.AsyncClient``.
            watch: Watch definition (used for facility_id and context).
            month_start: First day of the month to query.

        Returns:
            Parsed ``AvailabilitySlot`` objects for every available night.
        """
        start_str = f"{month_start.isoformat()}T00:00:00.000Z"
        url = f"{_BASE_URL}/{watch.facility_id}/month"
        params = {"start_date": start_str}

        logger.debug("GET {} params={}", url, params)

        await rate_limiter.acquire()

        try:
            response = await client.get(url, params=params)
        except httpx.HTTPError as exc:
            logger.warning(
                "HTTP error fetching campground {}: {}", watch.facility_id, exc
            )
            return []

        if response.status_code == 429:
            logger.warning(
                "Rate-limited (429) while querying campground {}",
                watch.facility_id,
            )
            return []

        if response.status_code != 200:
            logger.warning(
                "Unexpected status {} for campground {}",
                response.status_code,
                watch.facility_id,
            )
            return []

        try:
            data = response.json()
        except ValueError:
            logger.error(
                "Invalid JSON in campground response for {}",
                watch.facility_id,
            )
            return []

        logger.debug(
            "Campground {} month {} — received {} campsites",
            watch.facility_id,
            month_start,
            len(data.get("campsites", {})),
        )

        return self._parse_campsites(data, watch)

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_campsites(
        data: dict,
        watch: Watch,
    ) -> list[AvailabilitySlot]:
        """Parse the raw API JSON into ``AvailabilitySlot`` objects.

        Only dates with ``"Available"`` status are returned.
        """
        slots: list[AvailabilitySlot] = []
        campsites: dict = data.get("campsites", {})

        for site_id, site_info in campsites.items():
            site_name = site_info.get("site", "")
            campsite_type = site_info.get("campsite_type", "")
            availabilities: dict[str, str] = site_info.get("availabilities", {})

            for date_str, status in availabilities.items():
                if status != "Available":
                    continue

                try:
                    slot_date = datetime.fromisoformat(
                        date_str.replace("Z", "+00:00")
                    ).date()
                except (ValueError, TypeError):
                    logger.debug("Skipping unparseable date: {}", date_str)
                    continue

                slots.append(
                    AvailabilitySlot(
                        facility_id=watch.facility_id,
                        facility_name=watch.name,
                        site_id=str(site_id),
                        site_name=site_name,
                        date=slot_date,
                        status="Available",
                        booking_url=(
                            f"https://www.recreation.gov/camping/"
                            f"campgrounds/{watch.facility_id}"
                        ),
                        raw_data={
                            "campsite_type": campsite_type,
                            "date_str": date_str,
                            "site_info_keys": list(site_info.keys()),
                        },
                    )
                )
        return slots

    # ------------------------------------------------------------------
    # Filters
    # ------------------------------------------------------------------

    @staticmethod
    def _filter_consecutive_nights(
        slots: list[AvailabilitySlot],
        min_nights: int,
    ) -> list[AvailabilitySlot]:
        """Keep only slots that are part of a run of ≥ *min_nights* consecutive
        available dates **for the same site**.

        This ensures we only surface openings where the user can actually
        book a multi-night stay.
        """
        from collections import defaultdict
        from datetime import timedelta

        # Group by site_id
        by_site: dict[str, list[AvailabilitySlot]] = defaultdict(list)
        for s in slots:
            by_site[s.site_id].append(s)

        result: list[AvailabilitySlot] = []

        for _site_id, site_slots in by_site.items():
            sorted_slots = sorted(site_slots, key=lambda s: s.date)
            dates_set = {s.date for s in sorted_slots}
            qualifying_dates: set[date] = set()

            for slot in sorted_slots:
                # Check if this slot starts a run of min_nights
                if all(
                    slot.date + timedelta(days=d) in dates_set
                    for d in range(min_nights)
                ):
                    for d in range(min_nights):
                        qualifying_dates.add(slot.date + timedelta(days=d))

            result.extend(s for s in sorted_slots if s.date in qualifying_dates)

        return result

    @staticmethod
    def _filter_equipment(
        slots: list[AvailabilitySlot],
        equipment: str,
    ) -> list[AvailabilitySlot]:
        """Keep only slots whose campsite type matches *equipment* (case-insensitive).

        A simple substring match is used so that ``"STANDARD"`` matches
        ``"STANDARD NONELECTRIC"`` etc.
        """
        equipment_lower = equipment.lower()
        return [
            s
            for s in slots
            if equipment_lower
            in s.raw_data.get("campsite_type", "").lower()
        ]
